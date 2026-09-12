"""In-world time. Uncertainty is represented, not rounded away."""

import pytest

from bp.errors import CalendarError
from bp.timeline import Calendar, Span


def test_gregorian_precision_becomes_span_width():
    cal = Calendar("gregorian")
    assert cal.parse("2145-03-04").certain
    month = cal.parse("2145-03")
    assert not month.certain and month.hi - month.lo == 30
    year = cal.parse("2145")
    assert year.hi - year.lo == 364


def test_year_label_calendar():
    cal = Calendar("year_label", epoch_label="AC")
    year = cal.parse("297 AC")
    assert not year.certain
    assert cal.parse("297 AC d100").certain
    with pytest.raises(CalendarError):
        cal.parse("297 BC")          # wrong era for this profile


def test_unknown_is_a_first_class_answer():
    cal = Calendar("gregorian")
    assert cal.parse(None) is None
    assert cal.parse("unknown") is None
    assert cal.format_span(None) == "unknown"


def test_explicit_range():
    cal = Calendar("gregorian")
    span = cal.parse("2145-01-01..2145-12-31")
    assert span.lo < span.hi


def test_ordering_only_when_certain():
    a, b = Span(0, 10), Span(5, 20)
    assert a.overlaps(b)
    assert not a.definitely_before(b)      # they overlap; no honest ordering
    assert Span(0, 4).definitely_before(b)


def test_round_trip():
    cal = Calendar("gregorian")
    assert cal.format(cal.parse("2145-03-04").lo) == "2145-03-04"
