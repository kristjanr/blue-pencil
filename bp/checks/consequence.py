"""Does anything here change anything?

This is the check that separates a novel whose four-hundredth page happens
*because of* its hundredth from five hundred pages of competent scenes. Every
carded consequence should be visible in the text, and a scene that leaves the
world exactly as it found it gets questioned.

Questioned, not cut. Quiet chapters exist and long series need them. But a quiet
chapter should be quiet on purpose, and the card is where that intention gets
recorded.
"""

from __future__ import annotations

import re

from ..models import Marginalium
from ..textstats import _STOP
from . import CheckContext, register

_TOKEN = re.compile(r"[A-Za-z][A-Za-z'\-]+")

#: Surface signals that state changed. Crude, and only ever used to *raise* a
#: question — never to pass a scene that the card says should have teeth.
_CHANGE_CUES = (
    "for the first time", "never again", "no longer", "from now on", "decided", "agreed",
    "refused", "died", "killed", "destroyed", "left", "arrived", "discovered", "realised",
    "realized", "told", "confessed", "betrayed", "promised", "swore", "broke", "lost", "won",
)


def _keywords(text: str, limit: int = 6) -> list[str]:
    seen: list[str] = []
    for t in (w.lower() for w in _TOKEN.findall(text)):
        if len(t) > 3 and t not in _STOP and t not in seen:
            seen.append(t)
    return sorted(seen, key=len, reverse=True)[:limit]


@register("consequence")
class ConsequenceAudit:
    name = "consequence"

    def run(self, ctx: CheckContext) -> list[Marginalium]:
        out: list[Marginalium] = []
        low = ctx.draft.text.lower()

        if ctx.card is not None:
            for expected in ctx.card.consequences_expected:
                kws = _keywords(expected)
                if kws and sum(1 for k in kws if k in low) / len(kws) < 0.34:
                    out.append(Marginalium(
                        check=self.name, severity="soft", scene_ref=ctx.draft.ref,
                        message=f"carded consequence does not show up in the text: {expected!r}",
                        fixes=["put the consequence on the page", "revise the card"],
                    ))

        cues = sum(1 for cue in _CHANGE_CUES if cue in low)
        carded_quiet = bool(ctx.card and not ctx.card.consequences_expected and not ctx.card.turn)
        if cues == 0 and ctx.draft.word_count > 600:
            severity = "note" if carded_quiet else "soft"
            out.append(Marginalium(
                check=self.name, severity=severity, scene_ref=ctx.draft.ref,
                message=("nothing in this chapter visibly changes a future state"
                         + (" — the card marks it as a quiet chapter, so this is a note, not a complaint"
                            if carded_quiet else
                            ". A scene that leaves the world as it found it should be quiet on purpose.")),
                fixes=["give someone a cost, a decision, or a piece of information they did not have",
                       "card it as a deliberate quiet chapter"],
                metric={"change_cues": float(cues)},
            ))

        if ctx.card is not None and ctx.card.threads_advanced:
            unmoved = []
            for tid in ctx.card.threads_advanced:
                row = ctx.graph.conn.execute("SELECT name FROM threads WHERE thread_id=?", (tid,)).fetchone()
                label = row["name"] if row else tid
                kws = _keywords(label, 4)
                if kws and not any(k in low for k in kws):
                    unmoved.append(f"{tid} ({label})")
            if unmoved:
                out.append(Marginalium(
                    check=self.name, severity="soft", scene_ref=ctx.draft.ref,
                    message="threads the card says this chapter advances are not visible: " + ", ".join(unmoved),
                    fixes=["advance them here", "move them to another chapter's card"],
                ))
        return out
