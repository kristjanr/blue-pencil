"""The series profile: everything true about a *series*, kept out of the engine.

The whole reason this file exists is the ``information.channels`` block. It is
what turns "could this character know this yet?" into a computation rather than
a judgment. For a light-lag space opera that is radio versus an instant relay;
for a court intrigue it is ravens, riders and roads; for a small-town novel it
is gossip, and gossip is instant.

Everything here is inferred from the corpus where it can be and confirmed by a
human. A profile that is confidently wrong makes every epistemic check
confidently wrong, so :meth:`SeriesProfile.evidence_report` exists to put the
inferred values in front of a person with the scenes behind them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .errors import ProfileError
from .space import INSTANT, SpaceModel, parse_speed
from .timeline import Calendar, Span

NARRATION_MODES = {
    "first_person_rotating",
    "first_person_single",
    "third_close_rotating",
    "third_close_single",
    "third_omniscient",
    "frame",
}


@dataclass
class Channel:
    """A way information travels, and how fast."""

    name: str
    speed: float                      # light-years per day; inf == instant
    available_from_book: str | None = None
    available_from_date: Span | None = None
    #: how much a message degrades per relay hop, 0..1; used by the belief graph
    distortion: float = 0.0
    #: for travel_table worlds: this carrier's speed as a multiple of the
    #: table's days. A raven at 0.35 covers a 30-day road in about 11.
    table_factor: float = 1.0

    @property
    def instant(self) -> bool:
        return self.speed == INSTANT

    def available_at(self, day: float) -> bool:
        """Whether this channel exists yet on this day.

        Unresolved availability is treated as available: refusing to reason is
        worse than a checker that a human can correct once.
        """
        if self.available_from_date is None:
            return True
        return day >= self.available_from_date.lo


@dataclass
class SeriesProfile:
    name: str
    narration_mode: str = "third_close_rotating"
    pov_from: str = "chapter_header"
    calendar: Calendar = field(default_factory=Calendar)
    space: SpaceModel = field(default_factory=SpaceModel)
    channels: list[Channel] = field(default_factory=list)
    #: canonical name -> every alias that should resolve to it
    aliases: dict[str, list[str]] = field(default_factory=dict)
    #: beliefs fork when a character is copied (clones, backups, split selves)
    clone_lineage: bool = False
    #: whether death is reversible in this world, and therefore whether the
    #: object-and-body checker should demand an on-page restore
    revivable: bool = False
    style_unit: str = "chapter"
    source: Path | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    # ---------------------------------------------------------------- loading
    @classmethod
    def load(cls, path: str | Path) -> "SeriesProfile":
        path = Path(path)
        if not path.exists():
            raise ProfileError(f"profile not found: {path}")
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls.from_dict(data, base=path.parent, source=path)

    @classmethod
    def from_dict(cls, data: dict, *, base: Path | None = None, source: Path | None = None) -> "SeriesProfile":
        base = base or Path(".")
        narration = data.get("narration", {}) or {}
        mode = narration.get("mode", "third_close_rotating")
        if mode not in NARRATION_MODES:
            raise ProfileError(f"unknown narration mode {mode!r}; expected one of {sorted(NARRATION_MODES)}")

        time_spec = data.get("time", {}) or {}
        cal_kind = time_spec.get("calendar", "gregorian")
        # 'gregorian_offset' in the plan's example is gregorian with a note that
        # the years are not ours; the arithmetic is identical.
        kind = "gregorian" if str(cal_kind).startswith("gregorian") else str(cal_kind)
        calendar = Calendar(
            kind=kind,
            epoch_label=str(time_spec.get("epoch_label", "")),
            days_per_year=float(time_spec.get("days_per_year", 365.25)),
        )

        space = SpaceModel.load(data.get("space", {}) or {}, base=base)

        channels: list[Channel] = []
        for entry in (data.get("information", {}) or {}).get("channels", []) or []:
            if "name" not in entry or "speed" not in entry:
                raise ProfileError(f"channel {entry!r} needs a name and a speed")
            channels.append(
                Channel(
                    name=str(entry["name"]),
                    speed=parse_speed(entry["speed"]),
                    available_from_book=(str(entry["available_from"]) if entry.get("available_from") else None),
                    available_from_date=calendar.parse(entry.get("available_from_date")),
                    distortion=float(entry.get("distortion", 0.0)),
                    table_factor=float(entry.get("table_factor", 1.0)),
                )
            )

        entities = data.get("entities", {}) or {}
        aliases = _load_aliases(entities.get("aliases"), base)

        return cls(
            name=str(data.get("name") or (source.stem if source else "unnamed")),
            narration_mode=mode,
            pov_from=str(narration.get("pov_from", "chapter_header")),
            calendar=calendar,
            space=space,
            channels=channels,
            aliases=aliases,
            clone_lineage=bool(entities.get("clone_lineage", False)),
            revivable=bool(entities.get("revivable", entities.get("clone_lineage", False))),
            style_unit=str((data.get("style", {}) or {}).get("units", "chapter")),
            source=source,
            raw=data,
        )

    # --------------------------------------------------------------- accessors
    def channel(self, name: str) -> Channel | None:
        low = name.strip().casefold()
        for ch in self.channels:
            if ch.name.casefold() == low:
                return ch
        return None

    def fastest_channel(self, day: float | None = None) -> Channel | None:
        live = [c for c in self.channels if day is None or c.available_at(day)]
        return max(live, key=lambda c: c.speed) if live else None

    def canonical(self, name: str) -> str:
        """Resolve an alias to its canonical entity name.

        Retrieval that misses a scene because the text says "Will" and the graph
        says "Riker" is the single most common silent failure in this design, so
        alias resolution is not optional and not fuzzy.
        """
        if not name:
            return name
        low = name.strip().casefold()
        for canon, alts in self.aliases.items():
            if low == canon.casefold() or any(low == a.casefold() for a in alts):
                return canon
        return name.strip()

    def alias_set(self, name: str) -> list[str]:
        canon = self.canonical(name)
        return [canon, *self.aliases.get(canon, [])]

    def resolve_channel_availability(self, book_start_days: dict[str, float]) -> None:
        """Turn ``available_from: "book 2"`` into a date, once ingestion knows one.

        The profile is written by a human in book terms; the arithmetic needs
        days. This is called after ingestion, which is the first moment the
        mapping exists.
        """
        for ch in self.channels:
            if ch.available_from_date is not None or not ch.available_from_book:
                continue
            key = ch.available_from_book.strip().casefold()
            for book, start in book_start_days.items():
                if book.casefold() == key or book.casefold().endswith(key) or key in book.casefold():
                    ch.available_from_date = Span.at(start)
                    break
        # Transit shortcuts resolve the same way, and must: a wormhole network
        # that appears in book 5 makes an over-long journey unpriceable from
        # that date on, and only from that date on.
        for name, from_book in self.space.transit_from:
            if name in self.space.transit_from_day or not from_book:
                continue
            key = from_book.strip().casefold()
            for book, start in book_start_days.items():
                if book.casefold() == key or book.casefold().endswith(key) or key in book.casefold():
                    self.space.transit_from_day[name] = start
                    break

    def evidence_report(self) -> list[str]:
        """Human-readable summary of what the profile asserts, for confirmation."""
        lines = [
            f"series: {self.name}",
            f"narration: {self.narration_mode} (POV from {self.pov_from})",
            f"calendar: {self.calendar.kind}" + (f" [{self.calendar.epoch_label}]" if self.calendar.epoch_label else ""),
            f"space: {self.space.model} ({len(self.space.pairs)} distances, {len(self.space.places)} places)",
            f"clone lineage: {self.clone_lineage} · death reversible: {self.revivable}",
        ]
        for ch in self.channels:
            when = ""
            if ch.available_from_book:
                when = f" from {ch.available_from_book}"
            if self.space.model == "travel_table":
                speed = f"×{ch.table_factor:g} of the road table"
            else:
                speed = "instant" if ch.instant else f"{ch.speed:.6g} ly/day"
            lines.append(f"channel: {ch.name} @ {speed}{when}")
        if not self.channels:
            lines.append("channel: (none declared — epistemic path checks will abstain)")
        return lines


def _load_aliases(spec: Any, base: Path) -> dict[str, list[str]]:
    if not spec:
        return {}
    if isinstance(spec, dict):
        data = spec
    else:
        path = base / str(spec)
        if not path.exists():
            raise ProfileError(f"alias file not found: {path}")
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: dict[str, list[str]] = {}
    for canon, alts in data.items():
        if isinstance(alts, str):
            alts = [alts]
        out[str(canon)] = [str(a) for a in (alts or [])]
    return out
