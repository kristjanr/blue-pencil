"""Measuring prose, so the voice and repetition checkers report distance, not opinion.

Everything here is deterministic and cheap. That is the point: the plan spends
its model budget on judgment, and a sentence-length distribution is not a
judgment. A checker that can compute its finding should never ask a model for it
— the model is slower, costlier, and less consistent at exactly this.

The sensory and interiority lexicons are small and deliberately visible. They
are not meant to be a theory of prose; they are meant to be a *stable ruler*.
A ruler that is slightly wrong but identical across canon and draft still
measures drift correctly, which is all the voice checker claims to do.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from .models import TechniqueSpec

_SENT_SPLIT = re.compile(r"(?<=[.!?…])[\"'”’]?\s+(?=[\"'“‘(\[]?[A-Z0-9])")
_WORD = re.compile(r"[A-Za-z']+")
_DIALOGUE = re.compile(r"[\"“][^\"”“]{2,}[\"”]")
_PARA = re.compile(r"\n\s*\n")

#: Words that mark perception rather than action.
SENSORY = frozenset("""
saw see seen look looked looking watch watched glance glanced stare stared glimpse
heard hear hearing sound sounded listen listened noise silence loud quiet echo
smell smelled scent stink reek fragrance odour odor
taste tasted bitter sweet sour salt salty
felt feel feeling touch touched cold hot warm chill damp rough smooth ache pressure
bright dark dim glow shadow gleam flicker shimmer colour color red blue green
""".split())

#: Words that mark a narrator turning inward. In first person these are the
#: spine of the voice; in close third they are the distance dial.
INTERIORITY = frozenset("""
thought think thinking wondered wonder knew know realise realised realize realized
remembered remember felt feel believed believe suspected suspect decided decide
supposed suppose considered consider hoped hope feared fear doubted doubt
understood understand guessed guess figured meant mean assumed assume
""".split())

_STOP = frozenset("""
a an and the of to in is was it that he she they i we you his her their my our your
for on with as at by from but or not be been are were had has have do did this these
those there here then than so if when what which who whom how why all any some no
""".split())


def words(text: str) -> list[str]:
    return _WORD.findall(text)


def sentences(text: str) -> list[str]:
    body = re.sub(r"\s+", " ", text).strip()
    if not body:
        return []
    return [s for s in _SENT_SPLIT.split(body) if s.strip()]


def estimate_tokens(text: str) -> int:
    """Cheap, provider-agnostic token estimate.

    Deliberately not a real tokenizer: this is used for context budgeting and
    corpus arithmetic, where being within a few percent is enough and a network
    call per scene is not acceptable.
    """
    return max(1, round(len(text) / 3.8))


@dataclass
class TextMetrics:
    words: int = 0
    sentences: int = 0
    mean_sentence_len: float = 0.0
    sd_sentence_len: float = 0.0
    dialogue_ratio: float = 0.0
    interiority_ratio: float = 0.0
    sensory_density: float = 0.0
    paragraph_len: float = 0.0
    type_token_ratio: float = 0.0
    exclaim_ratio: float = 0.0
    question_ratio: float = 0.0

    def as_dict(self) -> dict[str, float]:
        return {k: float(v) for k, v in self.__dict__.items()}


def moving_ttr(tokens: list[str], window: int = 300) -> float:
    """Mean type-token ratio over fixed windows.

    Raw TTR falls as text gets longer — a 300-word scene and a 200,000-word
    corpus are not comparable on it, and comparing them anyway produces a voice
    "finding" of several thousand percent that is pure arithmetic artefact.
    Averaging over equal windows removes the length dependence, which is the
    only way the number means anything as a drift measure.
    """
    if not tokens:
        return 0.0
    if len(tokens) <= window:
        return len(set(tokens)) / len(tokens)
    ratios = []
    for start in range(0, len(tokens) - window + 1, max(1, window // 4)):
        chunk = tokens[start : start + window]
        ratios.append(len(set(chunk)) / len(chunk))
    return sum(ratios) / len(ratios)


def measure(text: str) -> TextMetrics:
    ws = words(text)
    ss = sentences(text)
    n = len(ws)
    if n == 0 or not ss:
        return TextMetrics()

    lengths = [len(words(s)) for s in ss] or [0]
    mean = sum(lengths) / len(lengths)
    var = sum((x - mean) ** 2 for x in lengths) / len(lengths)

    dialogue_words = sum(len(words(m.group(0))) for m in _DIALOGUE.finditer(text))
    lowered = [w.lower() for w in ws]
    paragraphs = [p for p in _PARA.split(text.strip()) if p.strip()] or [text]

    return TextMetrics(
        words=n,
        sentences=len(ss),
        mean_sentence_len=mean,
        sd_sentence_len=math.sqrt(var),
        dialogue_ratio=dialogue_words / n,
        interiority_ratio=sum(1 for w in lowered if w in INTERIORITY) / n,
        sensory_density=sum(1 for w in lowered if w in SENSORY) / n,
        paragraph_len=n / len(paragraphs),
        type_token_ratio=moving_ttr(lowered),
        exclaim_ratio=text.count("!") / max(1, len(ss)),
        question_ratio=text.count("?") / max(1, len(ss)),
    )


def ngrams(text: str, n: int = 4, *, drop_stopword_only: bool = True) -> Counter:
    """Content n-grams, for the repetition auditor.

    N-grams made entirely of function words ("out of the way") are dropped:
    every English writer repeats those, and flagging them buries the finding
    that matters under noise.
    """
    ws = [w.lower() for w in words(text)]
    out: Counter = Counter()
    for i in range(len(ws) - n + 1):
        gram = tuple(ws[i : i + n])
        if drop_stopword_only and all(w in _STOP for w in gram):
            continue
        out[" ".join(gram)] += 1
    return out


def distinctive_phrases(text: str, *, n: int = 4, limit: int = 60) -> list[str]:
    """Phrases worth adding to the do-not-reuse ledger for the next chapter."""
    counts = ngrams(text, n)
    scored = [(g, c) for g, c in counts.items() if sum(1 for w in g.split() if w not in _STOP) >= 3]
    scored.sort(key=lambda t: (-t[1], t[0]))
    return [g for g, _ in scored[:limit]]


@dataclass
class Baseline:
    """Per-POV n-gram rates from canon, used as the repetition budget.

    'Every series has its cold, wind, darkness and blood.' The auditor computes
    the budget from the corpus rather than guessing it, so a motif the author
    genuinely leans on is not flagged, and a motif the *engine* has started
    leaning on is.
    """

    pov: str = ""
    total_words: int = 0
    rates: dict[str, float] = field(default_factory=dict)   # phrase -> per 10k words
    unigram_rates: dict[str, float] = field(default_factory=dict)

    @classmethod
    def build(cls, pov: str, texts: list[str], *, n: int = 4) -> "Baseline":
        total = 0
        grams: Counter = Counter()
        unis: Counter = Counter()
        for t in texts:
            total += len(words(t))
            grams.update(ngrams(t, n))
            unis.update(w.lower() for w in words(t) if w.lower() not in _STOP)
        scale = 10_000 / total if total else 0.0
        return cls(
            pov=pov,
            total_words=total,
            rates={g: c * scale for g, c in grams.items() if c > 1},
            unigram_rates={w: c * scale for w, c in unis.items() if c > 2},
        )

    def rate(self, phrase: str) -> float:
        return self.rates.get(phrase, 0.0)

    def word_rate(self, word: str) -> float:
        return self.unigram_rates.get(word.lower(), 0.0)


def spec_from_scenes(pov: str, texts: list[str]) -> TechniqueSpec:
    """The measured half of a POV's technique spec. The mechanism half is
    model-authored and merged in by :mod:`bp.extract`."""
    joined = "\n\n".join(texts)
    m = measure(joined)
    return TechniqueSpec(
        pov=pov,
        scenes=len(texts),
        words=m.words,
        mean_sentence_len=m.mean_sentence_len,
        sd_sentence_len=m.sd_sentence_len,
        dialogue_ratio=m.dialogue_ratio,
        interiority_ratio=m.interiority_ratio,
        sensory_density=m.sensory_density,
        paragraph_len=m.paragraph_len,
        type_token_ratio=m.type_token_ratio,
        exclaim_ratio=m.exclaim_ratio,
        question_ratio=m.question_ratio,
    )


def zscore(value: float, mean: float, sd: float) -> float:
    if sd <= 1e-9:
        return 0.0 if abs(value - mean) < 1e-9 else math.copysign(9.9, value - mean)
    return (value - mean) / sd
