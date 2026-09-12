"""Voice drift, reported as a distance rather than an opinion.

The checker measures the draft against this POV's technique spec — sentence
length distribution, dialogue ratio, interiority, sensory density — and says how
far off it is. It does not say whether that is bad. A chapter can legitimately
run long-sentenced and interior; what it cannot do is drift without anyone
noticing, chapter after chapter, until the POV is somebody else.

Deliberately soft. A hard gate on style metrics optimises for the metric, and
the metric is a proxy.
"""

from __future__ import annotations

from ..models import Marginalium
from ..textstats import measure, zscore
from . import CheckContext, register

#: Which measured features to compare, and how far off is worth mentioning.
FEATURES: dict[str, tuple[str, float]] = {
    "mean_sentence_len": ("mean sentence length", 0.30),
    "dialogue_ratio": ("dialogue ratio", 0.40),
    "interiority_ratio": ("interiority", 0.45),
    "sensory_density": ("sensory density", 0.50),
    "paragraph_len": ("paragraph length", 0.45),
    "type_token_ratio": ("lexical variety", 0.20),
}


@register("voice")
class VoiceDriftCheck:
    name = "voice"

    def run(self, ctx: CheckContext) -> list[Marginalium]:
        spec = ctx.graph.style(ctx.pov)
        if spec is None or spec.words < 2000:
            return [Marginalium(
                check=self.name, severity="note", scene_ref=ctx.draft.ref,
                message=(f"no technique spec for POV {ctx.pov!r} with enough canon behind it; "
                         f"voice drift stood down"),
                fixes=["run `bp ingest` so the measured style layer exists",
                       "check the POV name matches the corpus"],
            )]

        m = measure(ctx.draft.text)
        drift: list[tuple[str, float, float, float]] = []
        for field_, (label, tol) in FEATURES.items():
            actual = getattr(m, field_)
            expected = getattr(spec, field_)
            if not expected:
                continue
            rel = (actual - expected) / expected
            if abs(rel) > tol:
                drift.append((label, actual, expected, rel))

        out: list[Marginalium] = []
        for label, actual, expected, rel in sorted(drift, key=lambda t: -abs(t[3])):
            out.append(Marginalium(
                check=self.name, severity="soft", scene_ref=ctx.draft.ref,
                message=(f"{label}: {actual:.3g} against {ctx.pov}'s canon {expected:.3g} ({rel:+.0%})"),
                fixes=[self._fix_for(label, rel)],
                metric={"actual": round(actual, 4), "canon": round(expected, 4), "relative": round(rel, 3)},
            ))

        # Sentence-length *variety* is a separate failure from sentence length,
        # and the one that reads as machine-made: uniform rhythm.
        if spec.sd_sentence_len and m.sd_sentence_len < spec.sd_sentence_len * 0.6:
            out.append(Marginalium(
                check=self.name, severity="soft", scene_ref=ctx.draft.ref,
                message=(f"sentence rhythm is flatter than canon: spread {m.sd_sentence_len:.1f} "
                         f"against {spec.sd_sentence_len:.1f}. Uniform sentence length is the most "
                         f"reliable tell of generated prose."),
                fixes=["break up or fuse sentences to restore variation"],
                metric={"sd": round(m.sd_sentence_len, 2), "canon_sd": round(spec.sd_sentence_len, 2),
                        "z": round(zscore(m.sd_sentence_len, spec.sd_sentence_len, spec.sd_sentence_len * 0.3), 2)},
            ))
        return out

    @staticmethod
    def _fix_for(label: str, rel: float) -> str:
        direction = "too much" if rel > 0 else "too little"
        return {
            "mean sentence length": "shorten" if rel > 0 else "lengthen" ,
            "dialogue ratio": f"{direction} dialogue — rebalance against narration",
            "interiority": f"{direction} interiority — this POV discloses thought at a different rate",
            "sensory density": f"{direction} sensory detail for this POV",
            "paragraph length": "break up the paragraphs" if rel > 0 else "let paragraphs run longer",
            "lexical variety": "vary the vocabulary" if rel < 0 else "the register may be drifting upward",
        }.get(label, "bring it back toward the spec")
