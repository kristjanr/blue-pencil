"""Can this character physically be here, on this date?

Same arithmetic as the epistemic check, pointed at bodies instead of news: take
where the last accepted chapter left them, take the profile's space model, and
see whether the journey fits in the time available.

Travel-table worlds and light-year worlds differ only in which number the
profile supplies. That is the payoff of putting space in the profile rather than
in the checker.
"""

from __future__ import annotations

from ..errors import UnknownDistance
from ..models import Citation, Marginalium
from . import CheckContext, register


@register("geography")
class GeographyCheck:
    name = "geography"

    def run(self, ctx: CheckContext) -> list[Marginalium]:
        if ctx.profile.space.model == "none":
            return []
        when = ctx.date
        place = ctx.draft.place or (ctx.card.location if ctx.card else "")
        if when is None or not place:
            return [Marginalium(
                check=self.name, severity="note", scene_ref=ctx.draft.ref,
                message="chapter lacks a date or a location, so geography checks stood down",
                fixes=["add `date:` and `place:` to the front matter"],
            )]

        out: list[Marginalium] = []
        people = {ctx.pov} | {ctx.profile.canonical(c) for c in ctx.draft.cast}
        people.discard("")
        for who in sorted(people):
            last = ctx.kg.last_placement(who, when.lo)
            if last is None:
                continue
            from_place, from_day = last
            if from_place.casefold() == place.casefold():
                continue
            if not ctx.profile.space.known(from_place, place):
                out.append(Marginalium(
                    check=self.name, severity="note", scene_ref=ctx.draft.ref,
                    message=(f"no distance recorded between {from_place!r} and {place!r}, "
                             f"so {who}'s journey cannot be checked"),
                    fixes=[f"add {from_place},{place},<distance> to the profile's distance file"],
                ))
                continue
            try:
                needed = ctx.profile.space.travel_days(from_place, place)
            except UnknownDistance:
                continue
            available = when.hi - from_day
            if needed <= available:
                continue
            # Once the series has a transit mechanism the profile cannot
            # price, an over-long journey is no longer something this check can
            # demonstrate to be impossible. It carries no topology on purpose:
            # we do not know which systems a wormhole network joins or when,
            # and a guessed topology would clear journeys that really are
            # impossible — the expensive direction. So it abstains and says
            # why, and stays hard for everything before that date.
            shortcut = ctx.profile.space.shortcut_at(when.hi)
            out.append(Marginalium(
                check=self.name,
                severity="note" if shortcut else "hard",
                scene_ref=ctx.draft.ref, line=1 + ctx.draft.body_offset,
                message=(f"{who} was at {from_place} on {ctx.fmt(from_day)} and is at {place} here "
                         f"({ctx.fmt(when.hi)}). That journey needs {needed:,.0f} days; only "
                         f"{available:,.0f} are available."
                         + (f" Cannot be decided: {shortcut} exists by this date and the profile "
                            f"does not say where it reaches." if shortcut else "")),
                fixes=[f"date the chapter no earlier than {ctx.fmt(from_day + needed)}",
                       "put the journey on the page, or use a faster conveyance the profile knows about",
                       "set the chapter somewhere reachable"],
                metric={"needed_days": float(needed), "available_days": float(available)},
            ))
        return out
