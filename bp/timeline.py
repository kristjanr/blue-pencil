"""In-world time, normalised to a single float axis so it can be done arithmetic on.

Every date in the system — an event's date, a scene's best-estimate date, a
report's arrival — is stored as an interval of *day numbers* on one axis. The
interval, rather than a point, is the honest representation: extracted dates
carry uncertainty, and a checker that pretends otherwise will fire false alarms.

The profile declares which calendar the surface text uses; this module converts
between that surface form and the axis. Nothing downstream knows or cares what
calendar a series counts in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from .errors import CalendarError

DAYS_PER_YEAR = 365.25
#: One light-year of travel at c, expressed on the day axis.
DAYS_PER_LIGHT_YEAR = DAYS_PER_YEAR

#: Month names as chapter headers print them, keyed by their first three letters.
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


@dataclass(frozen=True, order=True)
class Span:
    """A closed interval of day numbers: ``lo <= true value <= hi``."""

    lo: float
    hi: float

    def __post_init__(self) -> None:
        if self.hi < self.lo:
            raise CalendarError(f"span end {self.hi} precedes start {self.lo}")

    @classmethod
    def at(cls, day: float) -> "Span":
        return cls(day, day)

    @property
    def midpoint(self) -> float:
        return (self.lo + self.hi) / 2.0

    @property
    def certain(self) -> bool:
        return self.lo == self.hi

    def shifted(self, days: float) -> "Span":
        return Span(self.lo + days, self.hi + days)

    def widened(self, days: float) -> "Span":
        return Span(self.lo - days, self.hi + days)

    def overlaps(self, other: "Span") -> bool:
        return self.lo <= other.hi and other.lo <= self.hi

    def definitely_before(self, other: "Span") -> bool:
        """True only if every point in self precedes every point in other."""
        return self.hi < other.lo

    def definitely_after(self, other: "Span") -> bool:
        return self.lo > other.hi


class Calendar:
    """Converts between a series' surface date strings and the day axis.

    Three kinds cover every series shape encountered so far:

    ``gregorian``
        Real dates: ``2145-03-04``, ``2145-03``, ``2145``. Day 0 is 1970-01-01.
    ``year_label``
        A regnal or era year with a suffix — ``297 AC``, ``Y12``. Resolution is
        one year unless a day-of-year is given as ``297 AC d100``.
    ``elapsed``
        Bare day numbers, for series that count from a launch or a landing.

    A calendar always accepts ``day <n>`` and a bare float as an escape hatch,
    because extraction sometimes produces an axis value directly.
    """

    _EPOCH = date(1970, 1, 1)

    def __init__(self, kind: str = "gregorian", *, epoch_label: str = "", days_per_year: float = DAYS_PER_YEAR):
        self.kind = kind
        self.epoch_label = epoch_label
        self.days_per_year = days_per_year

    # ---------------------------------------------------------------- parsing
    def parse(self, text: str | float | int | None) -> Span | None:
        """Parse a surface date into a Span, or return None for 'unknown'.

        Unknown is a first-class answer. A checker that cannot date a scene must
        abstain rather than guess; see :mod:`bp.checks.epistemic`.
        """
        if text is None:
            return None
        if isinstance(text, (int, float)):
            return Span.at(float(text))
        raw = str(text).strip()
        if not raw or raw.lower() in {"unknown", "?", "n/a", "none"}:
            return None

        if (m := re.fullmatch(r"day\s+(-?\d+(?:\.\d+)?)", raw, re.I)):
            return Span.at(float(m.group(1)))
        if (m := re.fullmatch(r"-?\d+(?:\.\d+)?", raw)) and self.kind == "elapsed":
            return Span.at(float(raw))
        # An explicit range: "2145-03..2145-06"
        if ".." in raw:
            lo_s, hi_s = raw.split("..", 1)
            lo, hi = self.parse(lo_s), self.parse(hi_s)
            if lo is None or hi is None:
                raise CalendarError(f"cannot parse range {raw!r}")
            return Span(lo.lo, hi.hi)

        if self.kind == "gregorian":
            return self._parse_gregorian(raw)
        if self.kind == "year_label":
            return self._parse_year_label(raw)
        if self.kind == "elapsed":
            raise CalendarError(f"cannot parse {raw!r} as an elapsed-day count")
        raise CalendarError(f"unknown calendar kind {self.kind!r}")

    def _parse_gregorian(self, raw: str) -> Span:
        if (m := re.fullmatch(r"(-?\d{1,6})-(\d{1,2})-(\d{1,2})", raw)):
            y, mo, d = (int(g) for g in m.groups())
            return Span.at(self._to_day(y, mo, d))
        if (m := re.fullmatch(r"(-?\d{1,6})-(\d{1,2})", raw)):
            y, mo = int(m.group(1)), int(m.group(2))
            lo = self._to_day(y, mo, 1)
            nxt = self._to_day(y + (mo == 12), 1 if mo == 12 else mo + 1, 1)
            return Span(lo, nxt - 1)
        if (m := re.fullmatch(r"(-?\d{1,6})", raw)):
            y = int(m.group(1))
            return Span(self._to_day(y, 1, 1), self._to_day(y + 1, 1, 1) - 1)
        # Prose dates, as chapter headers actually print them: "June 25, 2133"
        # and the month-precision "February 2167". The second is a span, not a
        # point — the uncertainty is real and the checkers need it kept.
        if (m := re.fullmatch(r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(-?\d{1,6})", raw)):
            if (mo := _MONTHS.get(m.group(1)[:3].lower())):
                return Span.at(self._to_day(int(m.group(3)), mo, int(m.group(2))))
        if (m := re.fullmatch(r"([A-Za-z]{3,9})\.?\s+(-?\d{1,6})", raw)):
            if (mo := _MONTHS.get(m.group(1)[:3].lower())):
                y = int(m.group(2))
                lo = self._to_day(y, mo, 1)
                nxt = self._to_day(y + (mo == 12), 1 if mo == 12 else mo + 1, 1)
                return Span(lo, nxt - 1)
        raise CalendarError(f"cannot parse {raw!r} as a gregorian date")

    def _to_day(self, y: int, mo: int, d: int) -> float:
        try:
            return float((date(y, mo, d) - self._EPOCH).days)
        except ValueError as exc:  # out of range, e.g. year 12000
            raise CalendarError(f"date {y}-{mo}-{d} out of range: {exc}") from exc

    def _parse_year_label(self, raw: str) -> Span:
        m = re.fullmatch(r"(-?\d+)\s*([A-Za-z]*)\s*(?:d(\d+))?", raw)
        if not m:
            raise CalendarError(f"cannot parse {raw!r} in calendar {self.epoch_label or 'year_label'}")
        year = int(m.group(1))
        label, doy = m.group(2), m.group(3)
        if self.epoch_label and label and label.upper() != self.epoch_label.upper():
            raise CalendarError(f"date {raw!r} uses era {label!r}, profile declares {self.epoch_label!r}")
        start = year * self.days_per_year
        if doy is not None:
            return Span.at(start + float(doy))
        return Span(start, start + self.days_per_year - 1)

    # --------------------------------------------------------------- printing
    def format(self, day: float) -> str:
        if self.kind == "gregorian":
            try:
                return (self._EPOCH + timedelta(days=round(day))).isoformat()
            except OverflowError:
                return f"day {day:.0f}"
        if self.kind == "year_label":
            year, rem = divmod(day, self.days_per_year)
            suffix = f" {self.epoch_label}" if self.epoch_label else ""
            return f"{int(year)}{suffix} d{int(rem)}"
        return f"day {day:.0f}"

    def format_span(self, span: Span | None) -> str:
        if span is None:
            return "unknown"
        if span.certain:
            return self.format(span.lo)
        return f"{self.format(span.lo)}..{self.format(span.hi)}"
