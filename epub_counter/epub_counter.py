#!/usr/bin/env python3
"""Count characters, words and tokens in EPUB files.

Answers one question: will these books fit in a model's context window,
and how much room is left over for the model to write?

    python epub_counter.py book1.epub book2.epub ...          # offline estimate
    python epub_counter.py --exact books/*.epub               # exact, via Anthropic API
    python epub_counter.py --dump-dir text/ books/*.epub      # also save the plain text

Parsing uses only the standard library. `--exact` needs `pip install anthropic`
and an ANTHROPIC_API_KEY (count_tokens is free, but it needs an account).
"""

from __future__ import annotations

import argparse
import posixpath
import re
import sys
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree as ET

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_CONTEXT = 1_000_000
# Max output of a single response on current Opus models. Whatever we write
# in one turn has to fit in the same window as the books we sent.
DEFAULT_OUTPUT_RESERVE = 128_000

# Offline estimate: English prose runs ~4 characters per token on older
# Claude tokenizers; the tokenizer introduced with Opus 4.7 (used by Opus 5.x)
# produces up to ~1.35x as many tokens for the same text. We report both ends
# so the verdict doesn't hinge on a guess.
CHARS_PER_TOKEN_OPTIMISTIC = 4.0
CHARS_PER_TOKEN_PESSIMISTIC = 4.0 / 1.35

CONTAINER_NS = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
OPF_NS = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}
XHTML_TYPES = {"application/xhtml+xml", "text/html", "application/x-dtbook+xml"}


class _TextExtractor(HTMLParser):
    """Turn XHTML into plain text, keeping paragraph breaks."""

    BLOCK = {
        "p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr",
        "blockquote", "section", "article", "hr", "pre", "dt", "dd", "figcaption",
    }
    SKIP = {"script", "style", "head", "title"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip_depth += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip_depth:
            self.parts.append(data)


def html_to_text(markup: str) -> str:
    parser = _TextExtractor()
    parser.feed(markup)
    parser.close()
    text = "".join(parser.parts)
    # Collapse the whitespace that comes from indentation in the markup:
    # it isn't part of the book, and we don't want to count or send it.
    text = re.sub(r"[ \t\r\f\v ]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


@dataclass
class Book:
    path: Path
    title: str
    text: str
    tokens: int | None = None

    @property
    def chars(self) -> int:
        return len(self.text)

    @property
    def words(self) -> int:
        return len(self.text.split())


def read_epub(path: Path) -> Book:
    with zipfile.ZipFile(path) as zf:
        container = ET.fromstring(zf.read("META-INF/container.xml"))
        rootfile = container.find(".//c:rootfile", CONTAINER_NS)
        if rootfile is None:
            raise ValueError(f"{path}: no rootfile in META-INF/container.xml")
        opf_path = rootfile.attrib["full-path"]
        opf_dir = posixpath.dirname(opf_path)
        opf = ET.fromstring(zf.read(opf_path))

        title_el = opf.find(".//dc:title", OPF_NS)
        title = (title_el.text or "").strip() if title_el is not None else ""

        manifest = {
            item.attrib["id"]: item.attrib
            for item in opf.findall(".//opf:manifest/opf:item", OPF_NS)
        }
        # The spine is the reading order. Fall back to every XHTML item in
        # the manifest if a (broken) book has no spine.
        spine_ids = [ref.attrib["idref"] for ref in opf.findall(".//opf:spine/opf:itemref", OPF_NS)]
        if not spine_ids:
            spine_ids = [i for i, a in manifest.items() if a.get("media-type") in XHTML_TYPES]

        names = set(zf.namelist())
        chapters = []
        for item_id in spine_ids:
            item = manifest.get(item_id)
            if item is None or item.get("media-type") not in XHTML_TYPES:
                continue
            href = posixpath.normpath(posixpath.join(opf_dir, unquote(item["href"])))
            if href not in names:
                print(f"warning: {path.name}: missing {href}", file=sys.stderr)
                continue
            chapter = html_to_text(zf.read(href).decode("utf-8", errors="replace"))
            if chapter:
                chapters.append(chapter)

    return Book(path=path, title=title or path.stem, text="\n\n".join(chapters))


def count_tokens_exact(books: list[Book], model: str) -> None:
    try:
        from anthropic import Anthropic
    except ImportError:
        sys.exit("--exact needs the Anthropic SDK: pip install anthropic")
    client = Anthropic()
    for book in books:
        resp = client.messages.count_tokens(
            model=model,
            messages=[{"role": "user", "content": book.text}],
        )
        book.tokens = resp.input_tokens


def fmt(n: float) -> str:
    return f"{round(n):,}"


def report(books: list[Book], context: int, reserve: int, model: str, exact: bool) -> int:
    name_w = max([len(b.title) for b in books] + [len("TOTAL")])
    tok_header = f"tokens ({model})" if exact else "est. tokens (low–high)"
    print(f"{'book':<{name_w}}  {'characters':>12}  {'words':>10}  {tok_header}")
    print("-" * (name_w + 52))

    def tokens_cell(chars: int, tokens: int | None) -> str:
        if exact:
            return fmt(tokens)
        return f"{fmt(chars / CHARS_PER_TOKEN_OPTIMISTIC)} – {fmt(chars / CHARS_PER_TOKEN_PESSIMISTIC)}"

    for b in books:
        print(f"{b.title:<{name_w}}  {fmt(b.chars):>12}  {fmt(b.words):>10}  {tokens_cell(b.chars, b.tokens)}")

    total_chars = sum(b.chars for b in books)
    total_words = sum(b.words for b in books)
    total_tokens = sum(b.tokens for b in books) if exact else None
    print("-" * (name_w + 52))
    print(f"{'TOTAL':<{name_w}}  {fmt(total_chars):>12}  {fmt(total_words):>10}  {tokens_cell(total_chars, total_tokens)}")
    print()

    budget = context - reserve
    print(f"Context window:           {fmt(context)} tokens")
    print(f"Reserved for output:      {fmt(reserve)} tokens (one full-length response)")
    print(f"Available for the books:  {fmt(budget)} tokens")
    print()

    if exact:
        worst = best = total_tokens
    else:
        best = total_chars / CHARS_PER_TOKEN_OPTIMISTIC
        worst = total_chars / CHARS_PER_TOKEN_PESSIMISTIC

    if worst <= budget:
        verdict, code = f"FITS — {fmt(budget - worst)} tokens to spare (worst case).", 0
    elif best <= budget:
        verdict, code = (
            "BORDERLINE — fits on the optimistic estimate, not on the pessimistic one. "
            "Run again with --exact to know for sure.",
            2,
        )
    else:
        verdict, code = f"DOES NOT FIT — over by at least {fmt(best - budget)} tokens.", 1
    print(verdict)

    if not exact:
        print("\n(Estimate only. For the real number, run with --exact; count_tokens is free.)")
    return code


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("epubs", nargs="+", type=Path, help="EPUB files, in series order")
    ap.add_argument("--exact", action="store_true", help="count tokens with the Anthropic count_tokens API")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"model for --exact (default: {DEFAULT_MODEL})")
    ap.add_argument("--context", type=int, default=DEFAULT_CONTEXT, help="context window in tokens")
    ap.add_argument("--output-reserve", type=int, default=DEFAULT_OUTPUT_RESERVE,
                    help="tokens to keep free for the model's reply")
    ap.add_argument("--dump-dir", type=Path, help="write each book's extracted plain text here")
    args = ap.parse_args(argv)

    books = [read_epub(p) for p in args.epubs]

    if args.dump_dir:
        args.dump_dir.mkdir(parents=True, exist_ok=True)
        for b in books:
            (args.dump_dir / f"{b.path.stem}.txt").write_text(b.text, encoding="utf-8")

    if args.exact:
        count_tokens_exact(books, args.model)

    return report(books, args.context, args.output_reserve, args.model, args.exact)


if __name__ == "__main__":
    sys.exit(main())
