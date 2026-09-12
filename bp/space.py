"""Where things are, and how long it takes anything — a ship or a signal — to cross.

This is half of the epistemic check. The other half is :mod:`bp.timeline`.
Between them, "could this character know this yet?" stops being a judgment call
and becomes division.

Four space models cover the range the profile needs to express:

``interstellar``
    Named systems with light-year distances. Signal and travel times are
    distance over speed.
``travel_table``
    A table of days between named places — the right model for a world of roads
    and ravens where the terrain, not the distance, sets the time.
``single_city``
    Everything is reachable within a day; distances are zero.
``none``
    No spatial reasoning. Geography and epistemic path checks stand down.
"""

from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ProfileError, UnknownDistance
from .timeline import DAYS_PER_LIGHT_YEAR

INSTANT = math.inf


def parse_speed(spec: str | float | int) -> float:
    """Parse a channel or travel speed into light-years per day.

    Accepts ``instant``, ``c``, a multiple of c (``0.5c``, ``10c``), or an
    explicit ``<n>ly/day``. Returning infinity for ``instant`` means the arrival
    arithmetic needs no special case: ``distance / inf == 0``.
    """
    if isinstance(spec, (int, float)):
        return float(spec)
    s = str(spec).strip().lower()
    if s in {"instant", "instantaneous", "inf"}:
        return INSTANT
    if s == "c":
        return 1.0 / DAYS_PER_LIGHT_YEAR
    if (m := re.fullmatch(r"([\d.]+)\s*c", s)):
        return float(m.group(1)) / DAYS_PER_LIGHT_YEAR
    if (m := re.fullmatch(r"([\d.]+)\s*ly\s*/\s*day", s)):
        return float(m.group(1))
    if (m := re.fullmatch(r"([\d.]+)\s*ly\s*/\s*year", s)):
        return float(m.group(1)) / DAYS_PER_LIGHT_YEAR
    raise ProfileError(f"cannot parse speed {spec!r} (try: instant, c, 0.5c, 2 ly/day)")


@dataclass
class SpaceModel:
    """Distances between named places, plus the arithmetic over them."""

    model: str = "none"
    #: symmetric pair -> light-years (interstellar) or days (travel_table)
    pairs: dict[tuple[str, str], float] = field(default_factory=dict)
    #: places seen in the distance file, for alias/typo diagnostics
    places: set[str] = field(default_factory=set)
    default_travel_speed: float = 0.0

    # ------------------------------------------------------------- construction
    @classmethod
    def load(cls, spec: dict, *, base: Path | None = None) -> "SpaceModel":
        model = str(spec.get("model", "none"))
        if model not in {"interstellar", "travel_table", "single_city", "none"}:
            raise ProfileError(f"unknown space model {model!r}")
        sm = cls(model=model)
        path = spec.get("distances")
        if path:
            sm._load_pairs(Path(base or ".") / path)
        if (speed := spec.get("travel_speed")) is not None:
            sm.default_travel_speed = parse_speed(speed)
        elif model == "interstellar":
            # A conservative default: a tenth of c. Profiles that care set it.
            sm.default_travel_speed = parse_speed("0.1c")
        return sm

    def _load_pairs(self, path: Path) -> None:
        if not path.exists():
            raise ProfileError(f"distance file not found: {path}")
        with path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            required = {"interstellar": "light_years", "travel_table": "days"}.get(self.model, "light_years")
            if reader.fieldnames is None or required not in reader.fieldnames:
                raise ProfileError(f"{path} must have columns from,to,{required}")
            for line_no, row in enumerate(reader, start=2):
                a, b, raw = row.get("from"), row.get("to"), row.get(required)
                # Distance files are hand-maintained, so tolerate comments and
                # blank lines rather than dying on them.
                if not a or not b or a.lstrip().startswith("#"):
                    continue
                if raw is None or not str(raw).strip():
                    raise ProfileError(f"{path}:{line_no}: row {a}->{b} has no {required}")
                try:
                    value = float(raw)
                except ValueError:
                    raise ProfileError(f"{path}:{line_no}: {required}={raw!r} is not a number") from None
                a, b = a.strip(), b.strip()
                self.pairs[self._key(a, b)] = value
                self.places.update((a, b))

    @staticmethod
    def _key(a: str, b: str) -> tuple[str, str]:
        return tuple(sorted((a.strip().casefold(), b.strip().casefold())))  # type: ignore[return-value]

    # -------------------------------------------------------------- arithmetic
    def known(self, a: str | None, b: str | None) -> bool:
        if self.model in {"single_city", "none"}:
            return True
        if not a or not b:
            return False
        return a.strip().casefold() == b.strip().casefold() or self._key(a, b) in self.pairs

    def separation(self, a: str, b: str) -> float:
        """Light-years (interstellar) or days (travel_table) between two places."""
        if self.model in {"single_city", "none"}:
            return 0.0
        if a.strip().casefold() == b.strip().casefold():
            return 0.0
        try:
            return self.pairs[self._key(a, b)]
        except KeyError:
            raise UnknownDistance(f"no distance recorded between {a!r} and {b!r}") from None

    def signal_days(self, a: str, b: str, speed_ly_per_day: float, *, table_factor: float = 1.0) -> float:
        """Days for a message on a channel of this speed to cross from a to b.

        The two space models read the channel differently, and they have to.
        In an interstellar world a channel has a *speed* and transit is distance
        over speed. In a travel-table world the table already encodes how long
        the journey takes, and a channel is faster or slower than a traveller by
        a factor — a raven beats a rider, a rumour lags both. Reading a
        travel-table channel as a speed makes every carrier instantaneous, which
        silently disables the epistemic check in exactly the genre that needs it
        most.
        """
        if self.model == "none":
            return 0.0
        sep = self.separation(a, b)
        if self.model == "travel_table":
            return sep * max(0.0, table_factor)
        if speed_ly_per_day == INSTANT:
            return 0.0
        if speed_ly_per_day <= 0:
            raise ProfileError("channel speed must be positive")
        return sep / speed_ly_per_day

    def travel_days(self, a: str, b: str, speed_ly_per_day: float | None = None) -> float:
        """Days for a body (not a signal) to get from a to b."""
        if self.model == "none":
            return 0.0
        sep = self.separation(a, b)
        if self.model == "travel_table":
            return sep
        speed = speed_ly_per_day if speed_ly_per_day is not None else self.default_travel_speed
        if not speed:
            raise ProfileError("no travel speed configured for an interstellar profile")
        if speed == INSTANT:
            return 0.0
        return sep / speed
