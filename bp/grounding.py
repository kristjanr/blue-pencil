"""Can every record prove itself against the scene it cites?

The spot audit samples fifty claims and asks a human to read them. This asks
the same question of all of them at once, deterministically, for nothing — and
it is a better question, because a human reading a 150-character fragment on a
phone cannot tell a bad *quote* from a bad *record*.

Two checks, deliberately kept apart:

**Quote fidelity** — is the stored quote actually in the scene it is attributed
to? This is exact. A quote that is not in its own scene was invented or
misattributed, and that is a hard defect no amount of context excuses.

**Term grounding** — do the distinctive terms in the claim (proper nouns,
numbers) appear in the cited scene? Claims are paraphrases, so ordinary wording
will not survive; names and figures do. A claim naming three things the scene
never mentions is not grounded in that scene, whatever it says.

Neither check calls a model. Both are the kind of thing the engine should be
doing for itself rather than buying an opinion about.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# Capitalised words that start sentences or are simply common; matching on these
# would manufacture agreement rather than measure it.
_STOP = frozenset("""
a an the and or but if then than that this these those of in on at to for from by with
without within into onto over under again further once here there when where why how all
any both each few more most other some such no nor not only own same so too very can will
just don should now he she it they them his her its their we you i me my our your as is
was are were be been being have has had do does did doing would could may might must shall
one two three four five six seven eight nine ten first second third last next new old
after before during while because although though since until about against between
""".split())

_WORD = re.compile(r"[A-Za-z][A-Za-z'’\-]+")
_PROPER = re.compile(r"\b([A-Z][a-z’'\-]{2,})")
_NUMBER = re.compile(r"\b(\d[\d,.]*)\b")


def _norm(s: str) -> str:
    """Fold the typographic differences that are not real differences."""
    s = unicodedata.normalize("NFKC", s)
    s = (s.replace("’", "'").replace("‘", "'")
          .replace("“", '"').replace("”", '"')
          .replace("—", "-").replace("–", "-")
          .replace("…", ""))
    return re.sub(r"\s+", " ", s).strip().lower()


_ALNUM = re.compile(r"[^a-z0-9]+")


def _loose(s: str) -> str:
    """Strip everything but letters and digits.

    The fallback for quote fidelity, and only ever used to *rescue* a quote the
    strict test rejected — never to accuse one it accepted. Measured on the real
    corpus, 156 of 391 "quotes that appear nowhere" were punctuation artefacts:
    a curly quotation mark the model put at the wrong end of the line, an
    interjection whose quote marks it dropped, and — in one case — a scene whose
    own text reads "Iwas", a space lost to drop-cap handling at ingest. None of
    those is an invented quote, and counting them as such demoted records that
    could prove themselves perfectly well.
    """
    return _ALNUM.sub("", s.lower())


def _unescape(s: str) -> str:
    r"""Turn a literal ``’`` back into the character it stands for.

    The model quotes faithfully, but the escape sometimes survives as six
    literal characters — and after a second JSON hop it arrives *doubled*, as
    ``\\u2019``. That doubling is what made the settler's own decoder worse
    than useless: its pattern matched the second backslash, consumed the
    escape, and left the first backslash standing, so a faithful quote came
    out as ``\’`` and matched nothing. Prose is full of curly apostrophes, so
    this rejected real quotes as fabrications.
    """
    s = re.sub(r"\\{1,2}u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s or "")
    return s.replace("\\\\", "\\")


#: How much of a quote has to be findable in the scene before the quote counts
#: as evidence. Not 1.0, because a faithful quote can lose its tail to an
#: ellipsis or a trailing typographic difference; nowhere near the old
#: effective floor either, which accepted a 40-character prefix of a quote of
#: any length and so could pass 8% coverage.
_MIN_COVERAGE = 0.85

#: A ratio alone is not enough on a short string: eight of nine characters of
#: "the and a" will be found in almost any scene, and that is coincidence, not
#: evidence. Sixteen consecutive normalised characters is a phrase.
_MIN_COVERED_CHARS = 16


def _coverage(needle: str, haystack: str) -> float:
    """What fraction of ``needle`` sits, as a run from its start, in ``haystack``.

    Substring containment is monotone in the prefix — if the first k characters
    are present then so are the first k-1 — so the longest present prefix is a
    binary search rather than a scan.
    """
    if not needle:
        return 0.0
    if needle in haystack:
        return 1.0
    lo, hi = 0, len(needle)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if needle[:mid] in haystack:
            lo = mid
        else:
            hi = mid - 1
    return lo / len(needle)


def _verified(needle: str, haystack: str) -> bool:
    """Enough of the quote found, and enough of it in absolute terms."""
    covered = _coverage(needle, haystack) * len(needle)
    return (covered >= _MIN_COVERED_CHARS
            and covered / len(needle) >= _MIN_COVERAGE)


def quote_in_scene(quote: str, scene_norm: str, scene_raw: str = "") -> bool:
    """Is this quote really in this scene? The one place that decides.

    There used to be two answers to this question — this one, and a weaker
    copy inside the settler — which is how the settler came to reject 27% of
    its proposed closes for what turned out to be punctuation. A verification
    guard that is wrong manufactures exactly the fabrication it exists to
    catch, so there is now a single implementation and any caller that needs a
    different threshold passes an argument rather than forking it.

    Three tests, each strictly weaker than the last, and the loose one only
    ever *rescues* a quote the strict tests rejected — never accuses one they
    accepted.

    Every test is a *coverage* test, and that is the load-bearing part. The
    earlier shape asked whether the quote's first 40 characters were in the
    scene and accepted on that, looking at nothing after them. Which meant a
    quote that opened with real scene text and then ran on into something else
    entirely was accepted, with the something-else stored as evidence.

    That is not hypothetical. Book one produced three of them: the model's
    quote truncates at a curly apostrophe and the promise's own summary
    continues in the quote field —

        scene   : "Oh. My. God. I've hit the jackpot."
        emitted : "Oh. My. God. I\\Milo's search for habitable planets in
                   Omicron2 Eridani culminates in ..."

    Fourteen characters of real scene, then a sentence that was never in any
    scene. All three were caught here only because their matching prefix fell
    under the 40-character floor — an accident of length, not a check. The same
    fault in a longer quote would have been accepted silently.

    So: no prefix is accepted on its own account. Whatever fraction of the
    quote we could not find in the scene is unverified text, and unverified
    text does not become evidence by sitting next to some that is.
    """
    nq = _norm(_unescape(quote))
    if not nq:
        return False
    # The model often trims with an ellipsis, and a faithful quote can diverge
    # slightly at its tail, so an exact whole-string match is too strict. A
    # mostly-covered quote is still a quote; a barely-covered one is a splice.
    if _verified(nq, scene_norm):
        return True
    # Punctuation and whitespace are not evidence of invention. The
    # 30-character floor is what keeps this from rescuing by coincidence.
    lq = _loose(_unescape(quote))
    return len(lq) > 30 and _verified(lq, _loose(scene_raw))


def _terms(claim: str) -> set[str]:
    """The parts of a claim that paraphrase does not erase."""
    out = {re.sub(r"'s$", "", m.group(1).lower().replace("’", "'")).strip("-'")
           for m in _PROPER.finditer(claim)}
    out |= {m.group(1) for m in _NUMBER.finditer(claim)}
    return {t for t in out if t not in _STOP and len(t) > 2}


@dataclass
class Finding:
    kind: str
    record_id: str
    scene: str
    claim: str
    quote_ok: bool
    missing: list[str] = field(default_factory=list)
    found: int = 0
    total: int = 0

    @property
    def grounding(self) -> float:
        return self.found / self.total if self.total else 1.0

    @property
    def severity(self) -> str:
        if not self.quote_ok:
            return "quote not in scene"
        return "ungrounded" if self.grounding < 0.5 else "thin"


@dataclass
class Report:
    citations: int = 0
    records: int = 0
    quote_missing: int = 0
    ungrounded: int = 0
    thin: int = 0
    no_quote: int = 0
    findings: list[Finding] = field(default_factory=list)

    def render(self, show: int = 25) -> str:
        n = max(self.citations, 1)
        lines = [
            f"{self.citations:,} citations across {self.records:,} records",
            "",
            f"quote not found in its own scene : {self.quote_missing:,} ({self.quote_missing/n:.1%})",
            f"claim ungrounded (<50% of terms) : {self.ungrounded:,} ({self.ungrounded/n:.1%})",
            f"thin (50-99% of terms)           : {self.thin:,} ({self.thin/n:.1%})",
            f"no quote stored at all           : {self.no_quote:,} ({self.no_quote/n:.1%})",
        ]
        hard = [f for f in self.findings if f.severity != "thin"]
        if hard:
            lines += ["", f"worst {min(show, len(hard))} of {len(hard)}:"]
            for f in sorted(hard, key=lambda f: (f.quote_ok, f.grounding))[:show]:
                lines.append(f"  [{f.severity}] {f.kind} {f.record_id} @ {f.scene}")
                lines.append(f"      {f.claim[:100]}")
                if f.missing:
                    lines.append(f"      not in scene: {', '.join(sorted(f.missing)[:8])}")
        return "\n".join(lines)


_CLAIM_SQL = {
    "entity":   ("entities", "entity_id",  "name",    "description"),
    "event":    ("events",   "event_id",   "summary", None),
    "object":   ("objects",  "object_id",  "name",    "state"),
    "promise":  ("promises", "promise_id", "summary", None),
    "thread":   ("threads",  "thread_id",  "name",    "state"),
}


def check_grounding(graph, *, min_terms: int = 2, common_at: float = 0.05) -> Report:
    """Test every citation against the scene it names.

    ``common_at`` is what stops this measuring nothing. A term only counts as
    evidence if it is *rare*: "Herschel" appearing in a scene means something,
    "unknown" or "Bob" does not, and in this series nearly every narrator is a
    Bob. Rather than hand-maintain a list of words to ignore — which would need
    revising for every new series — take the corpus's own word: a term found in
    more than this fraction of scenes cannot discriminate between scenes, so it
    is dropped from the test.
    """
    scenes, povs, scenes_raw = {}, {}, {}
    for r in graph.conn.execute("SELECT scene_id, text, pov FROM scenes"):
        scenes[r["scene_id"]] = _norm(r["text"])
        scenes_raw[r["scene_id"]] = r["text"]
        # A first-person scene never names its own narrator: Bill's chapter says
        # "I", not "Bill". Requiring the POV name to appear in its own scene
        # manufactures a failure out of the series' own narration.
        povs[r["scene_id"]] = {w.lower() for w in _WORD.findall(r["pov"] or "")}

    # Aliases let a claim say "Riker" where the scene says "Bob-3"; without this
    # the check would report disagreement that is only naming.
    aliases: dict[str, set[str]] = {}
    for r in graph.conn.execute("SELECT name, aliases FROM entities"):
        try:
            alt = {a.lower() for a in __import__("json").loads(r["aliases"] or "[]")}
        except Exception:
            alt = set()
        if alt:
            aliases.setdefault(r["name"].lower(), set()).update(alt)

    # Document frequency over scenes, used to discard terms too common to be evidence.
    df: dict[str, int] = {}
    for text in scenes.values():
        for w in set(_WORD.findall(text)):
            df[w] = df.get(w, 0) + 1
    common = {w for w, n in df.items() if n > len(scenes) * common_at}

    claims: dict[tuple[str, str], str] = {}
    for kind, (table, idcol, main, extra) in _CLAIM_SQL.items():
        cols = f"{idcol}, {main}" + (f", {extra}" if extra else "")
        for r in graph.conn.execute(f"SELECT {cols} FROM {table}"):
            text = r[main] or ""
            if extra and r[extra]:
                text = f"{text} — {r[extra]}"
            claims[(kind, r[idcol])] = text

    report = Report()
    seen_records = set()
    for c in graph.conn.execute(
            "SELECT record_kind, record_id, scene_id, quote FROM citations"):
        kind, rid, sid, quote = c["record_kind"], c["record_id"], c["scene_id"], c["quote"] or ""
        scene = scenes.get(sid)
        if scene is None:
            continue                      # dangling scene refs are a separate report
        report.citations += 1
        seen_records.add((kind, rid))
        claim = claims.get((kind, rid), "")

        if not quote.strip():
            report.no_quote += 1
            quote_ok = False
        else:
            quote_ok = quote_in_scene(quote, scene, scenes_raw.get(sid, ""))

        terms = _terms(claim) - povs.get(sid, set()) - common
        missing = []
        for t in terms:
            if t in scene:
                continue
            if any(a in scene for a in aliases.get(t, ())):
                continue
            missing.append(t)
        found, total = len(terms) - len(missing), len(terms)

        if not quote_ok and quote.strip():
            report.quote_missing += 1
        if total >= min_terms:
            ratio = found / total
            if ratio < 0.5:
                report.ungrounded += 1
            elif ratio < 1.0:
                report.thin += 1

        if (not quote_ok and quote.strip()) or (total >= min_terms and found / total < 0.5):
            report.findings.append(Finding(
                kind=kind, record_id=rid, scene=sid, claim=claim,
                quote_ok=quote_ok, missing=missing, found=found, total=total))

    report.records = len(seen_records)
    return report


# ----------------------------------------------------------------- remediation
_TABLE_FOR = {"entity": ("entities", "entity_id"), "event": ("events", "event_id"),
              "object": ("objects", "object_id"), "promise": ("promises", "promise_id"),
              "thread": ("threads", "thread_id")}

UNPROVEN_CONFIDENCE = 0.5


def demote_unproven(graph, report: Report, *, run_id: str = "") -> dict[str, int]:
    """Strip the authority of evidence from records that have none.

    Extraction already does this for a record that arrives with no citation at
    all: it backfills the scene, marks the record ``inferred`` rather than
    ``explicit``, and caps confidence. A record whose quote cannot be found in
    the scene it names is in exactly that position — it asserts evidence that
    does not exist — so it earns the same treatment.

    The record is kept, not deleted. It may well be true; what it cannot do is
    prove itself, and the planner should weigh it accordingly rather than
    trusting it as though a human had checked it.
    """
    counts: dict[str, int] = {}
    run_id = run_id or f"demote-{__import__('time').strftime('%Y%m%d-%H%M%S')}"
    for f in report.findings:
        if f.quote_ok:
            continue
        table, idcol = _TABLE_FOR.get(f.kind, (None, None))
        if table is None:
            continue
        # Record what is about to be destroyed, before destroying it. When 156
        # of these turned out to be punctuation artefacts rather than invented
        # quotes, the only reason the demotion could be undone was that a backup
        # happened to predate it — luck standing in for design.
        before = graph.conn.execute(
            f"SELECT claim_type, confidence FROM {table} WHERE {idcol}=?", (f.record_id,)).fetchone()
        if before is not None:
            for field, new in (("claim_type", "inferred"),
                               ("confidence", min(before["confidence"], UNPROVEN_CONFIDENCE))):
                if before[field] != new:
                    graph.record_change(table=table, record_id=f.record_id, field=field,
                                        old=before[field], new=new, run_id=run_id,
                                        reason=f"grounding: {f.severity}")
        cur = graph.conn.execute(
            f"UPDATE {table} SET claim_type='inferred', confidence=MIN(confidence, ?) "
            f"WHERE {idcol}=? AND (claim_type != 'inferred' OR confidence > ?)",
            (UNPROVEN_CONFIDENCE, f.record_id, UNPROVEN_CONFIDENCE))
        if cur.rowcount:
            counts[f.kind] = counts.get(f.kind, 0) + cur.rowcount
    graph.conn.commit()
    return counts
