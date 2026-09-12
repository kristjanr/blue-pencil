"""Three readers who want different things. Their disagreements are the output.

A first-time reader, a wiki-editing superfan, and a line editor score the same
scene. Averaging them would destroy the signal: a chapter the superfan loves and
the first-timer finds impenetrable is a specific, fixable problem, and one
number hides it.

The superfan persona has a second job — catching graph errors that propagated
into the prose, which is the failure mode the whole design is most exposed to.

Model-backed. With no client it stands down rather than faking an opinion.
"""

from __future__ import annotations

from ..models import Marginalium
from . import CheckContext, register

PERSONAS = {
    "first-time reader": (
        "You have not read this series. Score boredom, confusion, and whether you would "
        "turn the page. Say plainly where you lost the thread."
    ),
    "wiki-editing superfan": (
        "You know every detail of this series and you edit its wiki. Score continuity, "
        "character fidelity, and whether anything contradicts what you know. Quote the "
        "line that is wrong."
    ),
    "line editor": (
        "You edit prose for a living. Score sentence-level craft, exposition handling, "
        "and repetition. Quote the worst sentence and say why."
    ),
}


@register("panel")
class ReaderPanel:
    name = "panel"

    def run(self, ctx: CheckContext) -> list[Marginalium]:
        if ctx.client is None:
            return [Marginalium(
                check=self.name, severity="note", scene_ref=ctx.draft.ref,
                message="reader panel needs a model; stood down (run with --llm)",
            )]
        from ..llm import reader_panel

        try:
            reports = reader_panel(
                ctx.client, ctx.policy.model_for("judge"), ctx.draft.text[:12000], PERSONAS
            )
        except Exception as exc:
            return [Marginalium(check=self.name, severity="note", scene_ref=ctx.draft.ref,
                                message=f"reader panel unavailable: {exc}")]

        out: list[Marginalium] = []
        for persona, report in reports.items():
            scores = ", ".join(f"{k} {v}" for k, v in sorted(report.get("scores", {}).items()))
            out.append(Marginalium(
                check=self.name, severity="soft", scene_ref=ctx.draft.ref,
                excerpt=report.get("quote", ""),
                message=f"{persona}: {scores} — {report.get('comment', '')}",
                metric={k: float(v) for k, v in report.get("scores", {}).items()
                        if isinstance(v, (int, float))},
            ))

        # The disagreement is the finding.
        spread = self._spread(reports)
        if spread:
            out.append(Marginalium(
                check=self.name, severity="soft", scene_ref=ctx.draft.ref,
                message="the panel disagrees sharply on: " + ", ".join(spread)
                        + " — that gap is usually where the real problem is",
                fixes=["read the three comments against each other before revising"],
            ))
        return out

    @staticmethod
    def _spread(reports: dict) -> list[str]:
        by_metric: dict[str, list[float]] = {}
        for report in reports.values():
            for k, v in (report.get("scores") or {}).items():
                if isinstance(v, (int, float)):
                    by_metric.setdefault(k, []).append(float(v))
        return [k for k, vals in by_metric.items() if len(vals) > 1 and max(vals) - min(vals) >= 4]
