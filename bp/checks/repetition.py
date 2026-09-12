"""The signature AI failure, with a budget computed from the corpus.

Every long series has its own "cold, wind, darkness and blood" — the phrase set
its author genuinely leans on. A repetition auditor with a hard-coded threshold
either flags the author's own voice or misses the engine's tic, depending on the
series. So the budget is *measured*: an n-gram's allowed rate is the rate canon
uses it at, per ten thousand words, for that POV.

Two memories, because there are two failures:

*Against canon* — the draft leans on something harder than the author ever did.
*Against recent chapters* — the phrase ledger. The engine has started saying the
same thing every chapter, which no single chapter reveals.
"""

from __future__ import annotations

import json
from collections import Counter

from ..models import Marginalium
from ..textstats import Baseline, ngrams, words
from . import CheckContext, register


@register("repetition")
class RepetitionAuditor:
    name = "repetition"

    #: A phrase must clear this rate before over-budget means anything; below it
    #: we are measuring noise in a 3,000-word sample.
    MIN_RATE = 2.0
    #: How far over the canon rate is a finding.
    TOLERANCE = 2.5

    def run(self, ctx: CheckContext) -> list[Marginalium]:
        text = ctx.draft.text
        n_words = len(words(text))
        if n_words < 200:
            return []
        scale = 10_000 / n_words
        out: list[Marginalium] = []

        baseline = self._baseline(ctx)
        counts: Counter = ngrams(text, 4)
        for phrase, count in counts.most_common(200):
            rate = count * scale
            if count < 2 or rate < self.MIN_RATE:
                continue
            canon = baseline.rate(phrase) if baseline else 0.0
            allowed = max(canon * self.TOLERANCE, self.MIN_RATE * 1.5)
            if rate <= allowed:
                continue
            idx = text.lower().find(phrase.split()[0])
            out.append(Marginalium(
                check=self.name, severity="soft", scene_ref=ctx.draft.ref,
                line=ctx.draft.line_of(max(0, idx)), excerpt=phrase,
                message=(f"“{phrase}” appears {count}× ({rate:.1f} per 10k words); "
                         + (f"canon runs it at {canon:.1f} for this POV" if canon
                            else "it does not appear in this POV's canon at all")),
                fixes=["vary it", "cut all but the strongest instance"],
                metric={"rate": round(rate, 2), "canon_rate": round(canon, 2), "count": float(count)},
            ))

        out.extend(self._against_ledger(ctx, text, scale))
        out.extend(self._motifs(ctx, text, scale, baseline))
        return out[:25]

    def _baseline(self, ctx: CheckContext) -> Baseline | None:
        raw = ctx.graph.get_meta(f"baseline:{ctx.pov}")
        if not raw:
            return None
        data = json.loads(raw)
        b = Baseline(pov=ctx.pov, total_words=data.get("total_words", 0))
        b.rates = data.get("rates", {})
        b.unigram_rates = data.get("unigram_rates", {})
        return b

    def _against_ledger(self, ctx: CheckContext, text: str, scale: float) -> list[Marginalium]:
        """Phrases the last 40k words already used. One chapter never shows this."""
        ledger = set(ctx.retriever.phrase_ledger(before_ord=10**9))
        if not ledger:
            return []
        counts = ngrams(text, 4)
        repeats = [p for p in counts if p in ledger]
        if len(repeats) < 3:
            return []
        return [Marginalium(
            check=self.name, severity="soft", scene_ref=ctx.draft.ref,
            message=(f"{len(repeats)} distinctive phrases here also appear in the last 40k words: "
                     + ", ".join(f"“{p}”" for p in repeats[:5])),
            fixes=["rewrite the repeated phrases", "check whether the scene is re-treading a beat"],
            metric={"repeated_phrases": float(len(repeats))},
        )]

    def _motifs(self, ctx: CheckContext, text: str, scale: float, baseline: Baseline | None) -> list[Marginalium]:
        """Single words leaned on far harder than canon leans on them."""
        if baseline is None or not baseline.unigram_rates:
            return []
        counts = Counter(w.lower() for w in words(text))
        flagged: list[tuple[str, float, float]] = []
        for word, count in counts.most_common(400):
            if count < 4 or len(word) < 4:
                continue
            rate = count * scale
            canon = baseline.word_rate(word)
            if canon and rate > canon * 3.0 and rate > 8:
                flagged.append((word, rate, canon))
        if not flagged:
            return []
        flagged.sort(key=lambda t: -(t[1] / max(t[2], 0.1)))
        listed = ", ".join(f"{w} ({r:.0f}/10k vs canon {c:.0f})" for w, r, c in flagged[:6])
        return [Marginalium(
            check=self.name, severity="soft", scene_ref=ctx.draft.ref,
            message=f"motif overuse against this POV's canon baseline: {listed}",
            fixes=["vary the vocabulary", "check whether one image is doing all the work"],
            metric={"flagged_motifs": float(len(flagged))},
        )]
