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
    #: Transit shortcuts that come into existence partway through a series — a
    #: wormhole network, a jump gate. Shaped like an information channel and
    #: for the same reason: `travel_speed` alone cannot express "and then, in
    #: book 5, this became possible". Deliberately carries no topology. We do
    #: not know which places a WormNet connects or when, and a guessed topology
    #: would be worse than none — it would clear journeys that really are
    #: impossible, which is the expensive direction. Its only job is to say
    #: that after this date an over-long journey is something the checker
    #: cannot demonstrate to be impossible.
    transit_from: list[tuple[str, str]] = field(default_factory=list)
    transit_from_day: dict[str, float] = field(default_factory=dict)
    #: canonical place -> (x, y, z) in light-years. A pairwise file needs N^2
    #: rows, and a real series names dozens of places; catalogue coordinates
    #: give every separation from N rows.
    coords: dict[str, tuple[float, float, float]] = field(default_factory=dict)
    #: surface name (casefolded) -> canonical place. A planet resolves to the
    #: system holding it: nothing travels between Vulcan and 40 Eridani.
    place_aliases: dict[str, str] = field(default_factory=dict)
    #: places that are not physical — VR, a council session, "the wormhole
    #: network". A character who was in VR did not travel, so how far they went
    #: is the wrong question rather than an unanswered one.
    nonphysical: set[str] = field(default_factory=set)

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
        if (places_path := spec.get("places")):
            sm._load_places(Path(base or ".") / places_path)
        for entry in spec.get("channels") or []:
            if isinstance(entry, dict) and entry.get("name"):
                sm.transit_from.append((str(entry["name"]),
                                        str(entry.get("available_from") or "")))
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

    def _load_places(self, path: Path) -> None:
        """A catalogue: name, sky position, distance — plus aliases and the
        places that are not physical at all.

        Columns: name, ra_hours, dec_degrees, distance_ly, alias_of, kind.
        A row with ``alias_of`` is a surface name for another place; a row with
        ``kind: virtual`` is somewhere nothing has to travel to.
        """
        import math

        if not path.exists():
            raise ProfileError(f"places file not found: {path}")
        with path.open(newline="", encoding="utf-8") as fh:
            for line_no, row in enumerate(csv.DictReader(fh), start=2):
                name = (row.get("name") or "").strip()
                if not name or name.startswith("#"):
                    continue
                self.places.add(name)
                if (alias_of := (row.get("alias_of") or "").strip()):
                    self.place_aliases[name.casefold()] = alias_of
                    continue
                if (row.get("kind") or "").strip().casefold() == "virtual":
                    self.nonphysical.add(name.casefold())
                    continue
                ra, dec, dist = (row.get(k) or "" for k in ("ra_hours", "dec_degrees", "distance_ly"))
                if not str(dist).strip():
                    continue  # named, but not catalogued: pairs may still cover it
                try:
                    ra_h, dec_d, d_ly = float(ra or 0), float(dec or 0), float(dist)
                except ValueError:
                    raise ProfileError(f"{path}:{line_no}: {name} has non-numeric coordinates") from None
                ra_rad, dec_rad = math.radians(ra_h * 15.0), math.radians(dec_d)
                self.coords[name.casefold()] = (
                    d_ly * math.cos(dec_rad) * math.cos(ra_rad),
                    d_ly * math.cos(dec_rad) * math.sin(ra_rad),
                    d_ly * math.sin(dec_rad),
                )

    def resolve(self, name: str) -> str:
        """Surface name -> the place the arithmetic is done on."""
        key = (name or "").strip().casefold()
        return self.place_aliases.get(key, name).strip()

    def is_nonphysical(self, name: str) -> bool:
        return self.resolve(name).casefold() in self.nonphysical

    @staticmethod
    def _key(a: str, b: str) -> tuple[str, str]:
        return tuple(sorted((a.strip().casefold(), b.strip().casefold())))  # type: ignore[return-value]

    # -------------------------------------------------------------- arithmetic
    def known(self, a: str | None, b: str | None) -> bool:
        if self.model in {"single_city", "none"}:
            return True
        if not a or not b:
            return False
        if self.is_nonphysical(a) or self.is_nonphysical(b):
            return True
        a, b = self.resolve(a), self.resolve(b)
        if a.casefold() == b.casefold() or self._key(a, b) in self.pairs:
            return True
        return a.casefold() in self.coords and b.casefold() in self.coords

    def separation(self, a: str, b: str) -> float:
        """Light-years (interstellar) or days (travel_table) between two places."""
        if self.model in {"single_city", "none"}:
            return 0.0
        # Nothing crosses space to reach VR. Treating it as an unknown distance
        # would make the checker abstain on a quarter of this series' scenes.
        if self.is_nonphysical(a) or self.is_nonphysical(b):
            return 0.0
        a, b = self.resolve(a), self.resolve(b)
        if a.casefold() == b.casefold():
            return 0.0
        # An explicit pair wins: it is a deliberate assertion about a place the
        # catalogue does not have coordinates for.
        if (pair := self._key(a, b)) in self.pairs:
            return self.pairs[pair]
        pa, pb = self.coords.get(a.casefold()), self.coords.get(b.casefold())
        if pa is not None and pb is not None:
            return sum((x - y) ** 2 for x, y in zip(pa, pb)) ** 0.5
        raise UnknownDistance(f"no distance recorded between {a!r} and {b!r}")

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

    def shortcut_at(self, day: float | None) -> str:
        """The first transit shortcut that exists by this day, if any.

        Names it rather than returning a bool so the finding can say which
        mechanism it cannot price.
        """
        if day is None:
            return ""
        for name, _book in self.transit_from:
            start = self.transit_from_day.get(name)
            if start is None or day >= start:
                return name
        return ""

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
