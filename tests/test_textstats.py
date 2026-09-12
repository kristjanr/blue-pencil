"""Measured prose. The ruler has to be stable, not correct."""

from bp.textstats import Baseline, distinctive_phrases, measure, moving_ttr, ngrams


def test_moving_ttr_is_length_independent():
    """Raw type-token ratio falls with length, which made a short draft look
    wildly more varied than its own canon. The windowed version does not."""
    short = ["word%d" % (i % 200) for i in range(300)]
    long = ["word%d" % (i % 200) for i in range(30_000)]
    assert abs(moving_ttr(short) - moving_ttr(long)) < 0.05
    raw_short = len(set(short)) / len(short)
    raw_long = len(set(long)) / len(long)
    assert raw_short - raw_long > 0.5      # the failure the window fixes


def test_dialogue_and_interiority_are_measured_separately():
    dialogue = '"Hello," she said. "How are you?" he asked. "Fine," she said.'
    interior = "I thought about it. I knew what she meant. I remembered the day."
    assert measure(dialogue).dialogue_ratio > measure(interior).dialogue_ratio
    assert measure(interior).interiority_ratio > measure(dialogue).interiority_ratio


def test_ngrams_drop_pure_function_word_phrases():
    grams = ngrams("and then it was and then it was the lanterns guttered low", n=4)
    assert "and then it was" not in grams        # every word is a function word
    assert "was the lanterns guttered" in grams  # this one carries content


def test_baseline_computes_a_rate_not_a_threshold():
    canon = ["the cold wind and the dark blood " * 50]
    b = Baseline.build("X", canon, n=4)
    assert b.rate("the cold wind and") > 0
    assert b.rate("a phrase never used") == 0.0


def test_distinctive_phrases_feed_the_do_not_reuse_ledger():
    text = "The lanterns guttered against the seawall. The lanterns guttered against the seawall."
    assert any("lanterns guttered" in p for p in distinctive_phrases(text, n=3))
