"""A chapter under the pencil — anyone's chapter, not just ours.

Stages 2, 5 and 6 work on any manuscript, including a human's fan continuation.
That is not incidental: the Continuity Editor ships before any generator exists,
and it is the cheapest possible proof that the story graph works. If the graph
cannot catch a planted error in a chapter someone else wrote, there is no point
drafting one.

So the input format is deliberately plain: markdown with optional YAML front
matter. Anything a person would hand you.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .textstats import estimate_tokens, words

_FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
_SCENE_BREAK = re.compile(r"^\s*(?:[*#•·—–-]\s*){3,}\s*$", re.M)


@dataclass
class DraftScene:
    index: int
    text: str
    first_line: int


@dataclass
class Draft:
    """A chapter, its declared metadata, and a line index for marginalia."""

    text: str
    meta: dict = field(default_factory=dict)
    path: Path | None = None
    body_offset: int = 0            # lines consumed by front matter

    @classmethod
    def load(cls, path: str | Path) -> "Draft":
        p = Path(path)
        return cls.parse(p.read_text(encoding="utf-8"), path=p)

    @classmethod
    def parse(cls, raw: str, *, path: Path | None = None) -> "Draft":
        meta: dict = {}
        offset = 0
        if (m := _FRONT.match(raw)):
            meta = yaml.safe_load(m.group(1)) or {}
            offset = raw[: m.end()].count("\n")
            raw = raw[m.end():]
        return cls(text=raw, meta=meta, path=path, body_offset=offset)

    # ------------------------------------------------------------------ fields
    @property
    def pov(self) -> str:
        return str(self.meta.get("pov", ""))

    @property
    def date_text(self) -> str:
        return str(self.meta.get("date", self.meta.get("date_inworld", "")))

    @property
    def place(self) -> str:
        return str(self.meta.get("place", self.meta.get("location", "")))

    @property
    def cast(self) -> list[str]:
        c = self.meta.get("cast", [])
        return [str(x) for x in c] if isinstance(c, list) else [str(c)]

    @property
    def card_id(self) -> str:
        return str(self.meta.get("card", ""))

    @property
    def ref(self) -> str:
        return str(self.meta.get("id", self.path.stem if self.path else "draft"))

    @property
    def word_count(self) -> int:
        return len(words(self.text))

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)

    # ------------------------------------------------------------------- lines
    @property
    def lines(self) -> list[str]:
        return self.text.split("\n")

    def line_of(self, char_index: int) -> int:
        """1-based line number in the *original file*, front matter included, so
        a marginalium points where the editor's cursor will land."""
        return self.text.count("\n", 0, max(0, char_index)) + 1 + self.body_offset

    def excerpt(self, char_index: int, *, width: int = 120) -> str:
        start = max(0, char_index - width // 3)
        end = min(len(self.text), char_index + width)
        snippet = self.text[start:end].replace("\n", " ").strip()
        return ("…" if start else "") + snippet + ("…" if end < len(self.text) else "")

    @property
    def scenes(self) -> list[DraftScene]:
        out: list[DraftScene] = []
        pos = 0
        for i, piece in enumerate(_SCENE_BREAK.split(self.text), start=1):
            if not piece.strip():
                pos += len(piece)
                continue
            idx = self.text.find(piece, pos)
            out.append(DraftScene(i, piece.strip(), self.line_of(idx if idx >= 0 else pos)))
            pos = (idx if idx >= 0 else pos) + len(piece)
        return out or [DraftScene(1, self.text, 1 + self.body_offset)]
