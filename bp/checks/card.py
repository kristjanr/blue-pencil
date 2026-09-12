"""Does the scene contain the reveals on its card — and only those?

The card is a contract in one direction: the drafter may not add a reveal the
card does not contain. This matters more than it looks. Acceptance re-extracts
the chapter and writes the deltas into the graph, so an uncarded reveal does not
merely wander off-plan — it silently rewrites the world state that every later
chapter is checked against. Corrupting the ledger is a slower, worse failure
than a bad scene.
"""

from __future__ import annotations

import re

from ..models import Marginalium
from ..textstats import _STOP
from . import CheckContext, register

_TOKEN = re.compile(r"[A-Za-z][A-Za-z'\-]+")


def _keywords(text: str, limit: int = 8) -> list[str]:
    seen: list[str] = []
    for t in (w.lower() for w in _TOKEN.findall(text)):
        if len(t) > 3 and t not in _STOP and t not in seen:
            seen.append(t)
    return sorted(seen, key=len, reverse=True)[:limit]


@register("card")
class CardComplianceCheck:
    name = "card"

    def run(self, ctx: CheckContext) -> list[Marginalium]:
        if ctx.card is None:
            return [Marginalium(
                check=self.name, severity="note", scene_ref=ctx.draft.ref,
                message="no chapter card supplied, so card compliance stood down",
                fixes=["pass --card plan/<book>/cards/<chapter>.json"],
            )]

        out: list[Marginalium] = []
        low = ctx.draft.text.lower()

        for reveal in ctx.card.reveals:
            what = reveal.get("what", "")
            if not what:
                continue
            kws = _keywords(what)
            hits = sum(1 for k in kws if k in low)
            if kws and hits / len(kws) < 0.4:
                out.append(Marginalium(
                    check=self.name, severity="hard", scene_ref=ctx.draft.ref,
                    message=(f"carded reveal is missing from the draft: {what!r}"
                             + (f" (to {reveal['to_whom']})" if reveal.get("to_whom") else "")),
                    fixes=["write the reveal into the scene",
                           "drop it from the card and re-plan the chapter"],
                    metric={"keyword_coverage": round(hits / len(kws), 3)},
                ))

        for seed in ctx.card.seeds_paid:
            row = ctx.graph.conn.execute(
                "SELECT summary FROM promises WHERE promise_id=?", (seed,)
            ).fetchone()
            label = row["summary"] if row else seed
            kws = _keywords(label)
            if kws and sum(1 for k in kws if k in low) / len(kws) < 0.3:
                out.append(Marginalium(
                    check=self.name, severity="soft", scene_ref=ctx.draft.ref,
                    message=f"card says this chapter pays promise {seed} ({label!r}), but the text does not show it",
                    fixes=["pay it on the page", "move the payoff to a later card"],
                ))

        # The other half of the contract: reveals the card did not authorise.
        carded = " ".join(r.get("what", "") for r in ctx.card.reveals).lower()
        for mention in ctx.mentions("event"):
            row = ctx.graph.event(mention.target_id)
            if row is None or not row["generated"]:
                continue
            # A generated (i.e. previously accepted) event is fair game; only
            # brand-new information needs a card. Uncarded *new* reveals are
            # caught by extraction at accept time, which is where the ledger
            # would be corrupted, so this is a soft nudge rather than a stop.
            if not any(k in carded for k in _keywords(row["summary"], 4)):
                out.append(Marginalium(
                    check=self.name, severity="soft", scene_ref=ctx.draft.ref, line=mention.line,
                    excerpt=mention.excerpt,
                    message=f"the scene surfaces {row['summary']!r}, which is not on the card",
                    fixes=["add it to the card if it is intended", "cut it"],
                ))

        if ctx.card.word_budget:
            actual = ctx.draft.word_count
            drift = abs(actual - ctx.card.word_budget) / ctx.card.word_budget
            if drift > 0.35:
                out.append(Marginalium(
                    check=self.name, severity="soft", scene_ref=ctx.draft.ref,
                    message=(f"{actual:,} words against a budget of {ctx.card.word_budget:,} "
                             f"({drift:+.0%})"),
                    fixes=["cut or expand to budget", "revise the card's budget"],
                    metric={"words": float(actual), "budget": float(ctx.card.word_budget)},
                ))
        return out
