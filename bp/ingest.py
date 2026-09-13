"""Stage 1 — corpus to scenes.

Nothing clever happens here on purpose. Ingestion is deterministic and
re-runnable, and every downstream artefact is derived from it, so a bug found in
week nine is a re-run rather than an archaeology project.

Scene boundaries come from the text itself — section breaks, chapter headers,
POV headers — with a model pass available (``--llm``) only to split long
chapters where the break is implicit. The deterministic path must work alone,
because a corpus that can only be ingested with an API key cannot be re-ingested
cheaply, and cheap re-ingestion is the whole point.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .db import Graph, SceneRow
from .embed import embed, pack
from .profile import SeriesProfile
from .textstats import Baseline, distinctive_phrases, estimate_tokens, spec_from_scenes, words

_SCENE_BREAK = re.compile(r"^\s*(?:[*#•·—–-]\s*){3,}\s*$|^\s*<hr\s*/?>\s*$", re.M | re.I)
_CHAPTER_HEAD = re.compile(
    r"^\s*(?:chapter|ch\.?)\s+([0-9]+|[ivxlcdm]+)\b[:.\s]*(.*)$|^\s*([0-9]{1,3})\s*[:.]\s*(.+)$",
    re.I | re.M,
)
_DATE_LINE = re.compile(
    r"^\s*(?:date|when)?\s*[:\-]?\s*("
    r"\d{4}-\d{2}-\d{2}|\d{4}-\d{2}|\d{3,4}\s*[A-Z]{1,3}|[A-Z][a-z]+\s+\d{1,2},?\s+\d{4}"
    r"|[A-Z][a-z]{2,8}\s+\d{3,4}"  # a bare month and year: "March 2309"
    r")\s*$",
    re.M,
)
_POV_LINE = re.compile(r"^\s*(?:pov|narrator)\s*[:\-]\s*(.+?)\s*$", re.I | re.M)

#: A chapter head as printed. Retail EPUBs routinely style these as an ordinary
#: <p>, so the tag name cannot be trusted to find them.
_CHAPTER_HEAD_TEXT = re.compile(
    r"^\s*(?:(?:chapter|part|prologue|epilogue)\b|\d{1,3}\s*[.:]\s+\S)", re.I
)

#: The other chapter-head shape: narrator, date and place on one dash-separated
#: line — "Bob – June 25, 2133 – Epsilon Eridani".
_DASH_BYLINE = re.compile(
    r"^[A-Z][\w'.\-]{1,24}(?:\s+[A-Z0-9][\w'.\-]{0,24}){0,2}\s*[–—]\s+\S", re.U
)

#: Apparatus, not story. Ingesting it pollutes the phrase ledger and the per-POV
#: style baselines with text the author never wrote in voice.
_FRONT_MATTER = re.compile(
    r"^\s*(?:table of contents|contents|copyright|dedication|acknowledge?ments?"
    r"|about the author|also by|titles by|title page|epigraph|foreword|preface)\b",
    re.I,
)
_PROPER = re.compile(r"\b([A-Z][a-z]{2,}(?:[-–][A-Z0-9][a-z0-9]*)?)\b")

#: Words that start sentences and get mistaken for names by a proper-noun scan.
_NOT_NAMES = frozenset("""
The A An And But Then When While After Before If So Now Later Still Yet That This These Those
There Here What Which Who How Why All Any Some No Not It He She They We You I My Our Your His
Her Their One Two Three First Next Last Chapter Part Book Even Once Just Well Maybe Because
""".split())


@dataclass
class ParsedScene:
    book_id: str
    chapter: int
    scene: int
    text: str
    pov: str = ""
    date_text: str = ""
    place: str = ""
    chapter_title: str = ""
    cast: list[str] = field(default_factory=list)


@dataclass
class IngestReport:
    books: int = 0
    chapters: int = 0
    scenes: int = 0
    words: int = 0
    tokens: int = 0
    povs: dict[str, int] = field(default_factory=dict)
    undated: int = 0
    unplaced: int = 0
    warnings: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            f"{self.books} books · {self.chapters} chapters · {self.scenes} scenes",
            f"{self.words:,} words · ~{self.tokens:,} tokens",
            "POV distribution: " + (", ".join(f"{k}={v}" for k, v in sorted(self.povs.items(), key=lambda t: -t[1])) or "none detected"),
            f"undated scenes: {self.undated} · unplaced scenes: {self.unplaced}",
        ]
        lines += [f"warning: {w}" for w in self.warnings[:12]]
        return "\n".join(lines)


# --------------------------------------------------------------------- readers
def split_byline(blocks: list[str], *, limit: int = 3) -> tuple[list[str], int]:
    """The short standalone lines under a chapter head — narrator, date, place.

    Returned as (lines, count_consumed). A byline line is short, is not a
    sentence, and is not itself a chapter head; prose fails all three.
    """
    lines: list[str] = []
    for block in blocks[:limit]:
        stripped = block.strip()
        if (
            not stripped
            or len(words(stripped)) > 8
            or stripped[-1:] in ".!?"
            or _CHAPTER_HEAD_TEXT.match(stripped)
        ):
            break
        lines.append(stripped)
    return lines, len(lines)


def read_epub(path: Path) -> list[tuple[str, str]]:
    """(title, plain text) per document in an EPUB, in spine order.

    Three things here are load-bearing, and each was a bug found against real
    retail EPUBs rather than a hypothetical:

    * Block text comes from ``p``/``h*`` only. Including ``div`` as well counts
      every chapter twice, because the container and its children both match.
    * Text is joined with no separator. Drop caps are markup — ``<span>D</span>``
      followed by ``aedalus`` — and a separator splits the word in half.
    * The chapter head is found by what it says, not by its tag. These books
      style it as a ``p``, so looking only for ``h1``-``h3`` falls through to the
      spine filename and every POV reads as ``part0031.html``.
    """
    from bs4 import BeautifulSoup  # imported lazily: text corpora need neither
    import ebooklib
    from ebooklib import epub

    book = epub.read_epub(str(path))
    out: list[tuple[str, str]] = []
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        soup = BeautifulSoup(item.get_content(), "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        # Preserve paragraph structure; it carries pacing information the
        # measured style layer depends on.
        for br in soup.find_all("br"):
            br.replace_with("\n")
        # <li> is included because books whose chapter head is a numbered list
        # item put the entire byline there: "Bob – June 25, 2133".
        found = soup.find_all(["p", "h1", "h2", "h3", "li"]) or soup.find_all("div")
        blocks: list[str] = []
        for b in found:
            block = re.sub(r"[ \t]+", " ", b.get_text("", strip=False)).strip()
            if block:
                blocks.append(block)
        if not blocks:
            blocks = [t for t in (soup.get_text("\n", strip=True),) if t]

        heading = soup.find(["h1", "h2", "h3"])
        title = ""
        if heading:
            title = re.sub(r"\s+", " ", heading.get_text(" ", strip=True))
        head_at = -1
        for i, block in enumerate(blocks[:4]):
            if _CHAPTER_HEAD_TEXT.match(block):
                head_at = i
                if not title:
                    title = re.sub(r"\s+", " ", block)
                break

        body = blocks
        byline: list[str] = []
        if head_at >= 0:
            byline, used = split_byline(blocks[head_at + 1:])
            body = blocks[head_at + 1 + used:]
        elif blocks and _DASH_BYLINE.match(blocks[0]):
            # The other shape: head and byline on one line, dash separated —
            # "Bob – June 25, 2133". The line is the title as well.
            title = title or blocks[0]
            byline = [p.strip() for p in re.split(r"\s*[–—]\s*|\s+-\s+", blocks[0]) if p.strip()]
            body = blocks[1:]

        if byline:
            # Normalise to the POV/date header the text reader already
            # understands, so detect_pov and detect_date need no epub-specific
            # special case.
            dated = [ln for ln in byline if _DATE_LINE.match(ln)]
            rest = [ln for ln in byline if ln not in dated]
            body = ([f"POV: {rest[0]}"] if rest else []) + dated + rest[1:] + body

        text = "\n\n".join(body)
        if not text.strip():
            continue
        if _FRONT_MATTER.match(title) or (head_at < 0 and _FRONT_MATTER.match(blocks[0])):
            continue
        if not title:
            title = item.get_name() or ""
        if len(words(text)) > 50:
            out.append((title, text))
    return out


def read_text(path: Path) -> list[tuple[str, str]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    chunks = _CHAPTER_HEAD.split(raw)
    if len(chunks) <= 1:
        return [(path.stem, raw)]
    # Rebuild (title, body) pairs from the split, which interleaves groups.
    out, buf, title = [], [chunks[0]], path.stem
    for m in _CHAPTER_HEAD.finditer(raw):
        pass
    parts = re.split(r"(?m)^(?=\s*(?:chapter|ch\.?)\s+[0-9ivxlcdm]+\b)", raw, flags=re.I)
    for part in parts:
        if not part.strip():
            continue
        first, _, rest = part.partition("\n")
        out.append((first.strip() or path.stem, rest if rest.strip() else part))
    return out or [(path.stem, raw)]


# ---------------------------------------------------------------------- parsing
def split_scenes(body: str, *, max_words: int = 2500) -> list[str]:
    """Section breaks first; oversized remainders split at paragraph gaps.

    The fallback split is on paragraph boundaries only. Splitting mid-paragraph
    to hit a word target would corrupt the pacing statistics that the technique
    spec is built from.
    """
    parts = [p.strip() for p in _SCENE_BREAK.split(body) if p and p.strip()]
    if not parts:
        parts = [body.strip()]
    out: list[str] = []
    for part in parts:
        if len(words(part)) <= max_words:
            out.append(part)
            continue
        paras = [p for p in re.split(r"\n\s*\n", part) if p.strip()]
        buf: list[str] = []
        count = 0
        for para in paras:
            n = len(words(para))
            if buf and count + n > max_words:
                out.append("\n\n".join(buf))
                buf, count = [], 0
            buf.append(para)
            count += n
        if buf:
            out.append("\n\n".join(buf))
    return [o for o in out if o.strip()]


def detect_pov(text: str, header: str, profile: SeriesProfile, known: list[str]) -> str:
    """POV from the header if the profile says that is where it lives, else from
    the text. Returns '' rather than guessing badly."""
    if (m := _POV_LINE.search(text[:400])):
        return profile.canonical(m.group(1))
    if profile.pov_from == "chapter_header" and header:
        stripped = re.sub(r"^\s*(?:chapter|ch\.?)\s+[0-9ivxlcdm]+\b[:.\s]*", "", header, flags=re.I).strip()
        cand = profile.canonical(stripped)
        if stripped and (cand in known or stripped in known):
            return cand
        looks_like_a_file = "/" in stripped or re.search(r"\.\w{2,5}$", stripped)
        if stripped and not looks_like_a_file and len(stripped.split()) <= 2 and stripped[:1].isupper():
            return cand
    if profile.narration_mode.startswith("first_person"):
        # First-person text rarely names its narrator; the header usually does.
        # If it didn't, the most-mentioned known name is a poor guess, so abstain.
        return ""
    head = text[:600]
    for name in known:
        if re.search(rf"\b{re.escape(name)}\b", head):
            return profile.canonical(name)
    return ""


def detect_date(text: str) -> str:
    m = _DATE_LINE.search(text[:500])
    return m.group(1).strip() if m else ""


def detect_place(text: str, places: list[str]) -> str:
    head = text[:800]
    for place in sorted(places, key=len, reverse=True):
        if re.search(rf"\b{re.escape(place)}\b", head, re.I):
            return place
    return ""


def detect_cast(text: str, known: list[str], profile: SeriesProfile) -> list[str]:
    found: list[str] = []
    for name in known:
        for alias in profile.alias_set(name):
            if re.search(rf"\b{re.escape(alias)}\b", text):
                found.append(profile.canonical(name))
                break
    return sorted(set(found))


def harvest_names(texts: list[str], *, min_count: int = 3, limit: int = 400) -> list[str]:
    """Bootstrap an entity list from proper-noun frequency.

    Only a bootstrap: extraction replaces it with cited entity records. But
    scene metadata has to exist before extraction runs, and cast tagging with no
    entity list at all makes retrieval useless.
    """
    counts: dict[str, int] = {}
    for t in texts:
        for m in _PROPER.finditer(t):
            name = m.group(1)
            if name in _NOT_NAMES or len(name) < 3:
                continue
            counts[name] = counts.get(name, 0) + 1
    ranked = [n for n, c in sorted(counts.items(), key=lambda t: -t[1]) if c >= min_count]
    return ranked[:limit]


# --------------------------------------------------------------------- pipeline
def ingest(
    corpus_dir: str | Path,
    graph: Graph,
    profile: SeriesProfile,
    *,
    places: list[str] | None = None,
    max_scene_words: int = 2500,
) -> IngestReport:
    corpus = Path(corpus_dir)
    files = sorted(
        [p for p in corpus.rglob("*") if p.suffix.lower() in {".epub", ".txt", ".md"} and p.is_file()]
    )
    if not files:
        raise FileNotFoundError(f"no .epub/.txt/.md files under {corpus}")

    report = IngestReport()
    places = places or sorted(profile.space.places)

    # Two passes: the first harvests names so the second can tag cast.
    raw_books: list[tuple[str, list[tuple[str, str]]]] = []
    for path in files:
        docs = read_epub(path) if path.suffix.lower() == ".epub" else read_text(path)
        raw_books.append((path.stem, docs))
    known = harvest_names([t for _, docs in raw_books for _, t in docs])
    known = [profile.canonical(n) for n in known]

    ordinal = 0
    per_pov: dict[str, list[str]] = {}
    for book_index, (book_title, docs) in enumerate(raw_books, start=1):
        # Profiles refer to books by position ("available_from: book 2"), so the
        # id has to be that, not the filename it happened to arrive in.
        book_id = f"book {book_index}"
        graph.add_book(book_id, book_title, book_index)
        report.books += 1
        for chapter_index, (title, body) in enumerate(docs, start=1):
            report.chapters += 1
            pieces = split_scenes(body, max_words=max_scene_words)
            chapter_pov = detect_pov(body, title, profile, known)
            chapter_date = detect_date(body)
            for scene_index, text in enumerate(pieces, start=1):
                pov = detect_pov(text, title, profile, known) or chapter_pov
                date_text = detect_date(text) or chapter_date
                span = None
                if date_text:
                    try:
                        span = profile.calendar.parse(date_text)
                    except Exception as exc:
                        report.warnings.append(f"{book_id} ch{chapter_index}: unparsed date {date_text!r} ({exc})")
                place = detect_place(text, places)
                cast = detect_cast(text, known, profile)
                scene_id = f"{book_id}.{chapter_index:02d}.{scene_index}"
                w = len(words(text))
                tok = estimate_tokens(text)
                graph.add_scene(SceneRow(
                    scene_id=scene_id, book_id=book_id, chapter=chapter_index, scene=scene_index,
                    pov=pov, date_text=date_text, day_lo=span.lo if span else None,
                    day_hi=span.hi if span else None, place=place, cast=cast, text=text,
                    words=w, tokens=tok, ord=ordinal, chapter_title=title,
                ))
                _index_chunks(graph, scene_id, text)
                _index_phrases(graph, scene_id, text, ordinal)
                ordinal += 1
                report.scenes += 1
                report.words += w
                report.tokens += tok
                if pov:
                    report.povs[pov] = report.povs.get(pov, 0) + 1
                    per_pov.setdefault(pov, []).append(text)
                if span is None:
                    report.undated += 1
                if not place:
                    report.unplaced += 1

    # The measured half of each POV's technique spec, plus its repetition budget.
    for pov, texts in per_pov.items():
        graph.write_style(spec_from_scenes(pov, texts))
        baseline = Baseline.build(pov, texts)
        graph.set_meta(f"baseline:{pov}", json.dumps({
            "total_words": baseline.total_words,
            "rates": dict(sorted(baseline.rates.items(), key=lambda t: -t[1])[:4000]),
            "unigram_rates": dict(sorted(baseline.unigram_rates.items(), key=lambda t: -t[1])[:4000]),
        }))

    graph.set_meta("entity_bootstrap", json.dumps(known))
    graph.set_meta("corpus_words", str(report.words))
    graph.set_meta("corpus_tokens", str(report.tokens))

    # Now that books have dates, 'available_from: book 2' can become a day.
    profile.resolve_channel_availability(graph.book_start_days())
    for ch in profile.channels:
        if ch.available_from_date is not None:
            graph.set_meta(f"channel_from:{ch.name}", str(ch.available_from_date.lo))
        elif ch.available_from_book:
            report.warnings.append(
                f"channel {ch.name!r} declares available_from {ch.available_from_book!r} "
                f"but no book with that id was dated during ingestion"
            )

    graph.commit()
    return report


def _index_chunks(graph: Graph, scene_id: str, text: str, *, target: int = 550) -> None:
    """Split a scene into 300–800 token chunks on paragraph boundaries."""
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    buf: list[str] = []
    count = 0
    ordinal = 0
    graph.conn.execute("DELETE FROM chunks WHERE scene_id=?", (scene_id,))

    def flush() -> None:
        nonlocal buf, count, ordinal
        if not buf:
            return
        body = "\n\n".join(buf)
        graph.conn.execute(
            "INSERT OR REPLACE INTO chunks(chunk_id,scene_id,ord,text,tokens,vec) VALUES(?,?,?,?,?,?)",
            (f"{scene_id}#{ordinal}", scene_id, ordinal, body, estimate_tokens(body), pack(embed(body))),
        )
        ordinal += 1
        buf, count = [], 0

    for para in paras:
        t = estimate_tokens(para)
        if buf and count + t > 800:
            flush()
        buf.append(para)
        count += t
        if count >= target:
            flush()
    flush()


def _index_phrases(graph: Graph, scene_id: str, text: str, ordinal: int) -> None:
    graph.conn.executemany(
        "INSERT OR IGNORE INTO phrase_ledger(phrase,scene_id,ord) VALUES(?,?,?)",
        [(p, scene_id, ordinal) for p in distinctive_phrases(text)],
    )
