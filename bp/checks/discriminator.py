"""A floor against obviously machine-made prose. Never a target.

A judge is shown canon and generated passages unlabeled and asked which is
which. High confidence flags a scene. Low confidence proves nothing — bland
prose is also hard to tell from mediocre canon, and a discriminator you optimise
against produces beige. So this check reports and never gates, and the run
policy's ``floor`` severity says exactly that.

Without a model available it falls back to a small set of surface tells that are
cheap and honest about being surface tells.
"""

from __future__ import annotations

import re

from ..models import Marginalium
from ..textstats import measure, sentences
from . import CheckContext, register

#: Constructions that show up far more often in generated prose than in print.
_TELLS = {
    r"\bit was as if\b": "as-if simile",
    r"\ba mixture of \w+ and \w+\b": "'a mixture of X and Y'",
    r"\bcouldn't help but\b": "'couldn't help but'",
    r"\bsomething (?:like )?(?:akin to|approaching) \w+": "'something akin to'",
    r"\bin that moment\b": "'in that moment'",
    r"\bhe (?:knew|felt) that (?:he|she|they) (?:had|would)\b": "flat interior report",
    r"\ba testament to\b": "'a testament to'",
    r"\bthe weight of \w+ (?:settled|pressed|hung)\b": "'the weight of X settled'",
    r"\bnot \w+, but \w+\b": "'not X, but Y' antithesis",
    r"\bwith a mix of\b": "'with a mix of'",
}


@register("discriminator")
class DiscriminatorCheck:
    name = "discriminator"

    def run(self, ctx: CheckContext) -> list[Marginalium]:
        if ctx.client is not None:
            verdict = self._model_verdict(ctx)
            if verdict is not None:
                return verdict
        return self._surface_tells(ctx)

    def _model_verdict(self, ctx: CheckContext) -> list[Marginalium] | None:
        from ..llm import discriminate

        canon = ctx.retriever.exemplars(ctx.pov, limit=3)
        if not canon:
            return None
        try:
            confidence, reason = discriminate(
                ctx.client, ctx.policy.model_for("judge"),
                candidate=ctx.draft.text[:6000],
                canon=[h.text[:2000] for h in canon],
            )
        except Exception:
            return None
        if confidence < 0.75:
            return []
        return [Marginalium(
            check=self.name, severity="floor", scene_ref=ctx.draft.ref,
            message=(f"a blind judge picked this out as generated with confidence {confidence:.2f}: {reason}"),
            fixes=["revise the passages named above",
                   "note: do not optimise against this check — it is a floor, not a target"],
            metric={"confidence": round(confidence, 3)},
        )]

    def _surface_tells(self, ctx: CheckContext) -> list[Marginalium]:
        text = ctx.draft.text
        found: list[str] = []
        for pattern, label in _TELLS.items():
            hits = len(re.findall(pattern, text, re.I))
            if hits:
                found.append(f"{label} ×{hits}")

        m = measure(text)
        ss = sentences(text)
        # Uniform sentence openings are the tell no single sentence reveals.
        openers = [s.split()[0].lower() for s in ss if s.split()]
        repeated_open = max((openers.count(o) for o in set(openers)), default=0)
        uniform = len(ss) > 12 and repeated_open / len(ss) > 0.28

        if not found and not uniform:
            return []
        parts = []
        if found:
            parts.append("surface tells: " + ", ".join(found[:6]))
        if uniform:
            parts.append(f"{repeated_open}/{len(ss)} sentences open with the same word")
        return [Marginalium(
            check=self.name, severity="floor", scene_ref=ctx.draft.ref,
            message="; ".join(parts) + " (heuristic pass — no judge model available)",
            fixes=["rewrite the flagged constructions",
                   "run with --llm for the blind-judge version of this check"],
            metric={"tells": float(len(found)), "sd_sentence_len": round(m.sd_sentence_len, 2)},
        )]
