"""Does anyone act on news that could not have reached them yet?

The classic AI-continuation failure, and the reason the graph stores events with
information paths rather than facts. A character reacting to a destruction still
years out in transit is not a matter of taste; it is a shortest-path query with
an answer.

The checker is careful in one direction on purpose. It uses the *late* end of an
uncertain scene date and the *earliest* possible arrival, so it only fires when
the gap is real under the most generous reading available. An editor forgives a
missed error; they stop reading a tool that cries wolf.
"""

from __future__ import annotations

from ..models import Citation, Marginalium
from . import CheckContext, register


def _acts_on_page(text: str, name: str) -> bool:
    """Does this character do something here, or is she only mentioned?"""
    import re

    passive = {"is", "was", "had", "would", "died", "will", "has", "were", "used", "never", "once"}
    for m in re.finditer(rf"\b{re.escape(name)}\b(?:'s)?\s+([a-z]+)", text):
        if m.group(1) not in passive:
            return True
    return bool(re.search(rf'"\s*[^"]{{0,200}}"\s*,?\s*(?:said|asked|told)\s+{re.escape(name)}\b', text, re.I))


@register("epistemic")
class EpistemicCheck:
    name = "epistemic"

    def run(self, ctx: CheckContext) -> list[Marginalium]:
        out: list[Marginalium] = []
        when = ctx.date
        if when is None:
            return [Marginalium(
                check=self.name, severity="note", scene_ref=ctx.draft.ref,
                message="no in-world date on this chapter, so information-path checks stood down",
                fixes=["add `date:` to the chapter's front matter, or a date to its card"],
            )]
        if not ctx.profile.channels:
            return [Marginalium(
                check=self.name, severity="note", scene_ref=ctx.draft.ref,
                message="the series profile declares no information channels; path checks stood down",
                fixes=["add an information.channels block to the profile"],
            )]

        # A channel whose availability never resolved counts as available
        # everywhere (Channel.available_at). That is the right default for a
        # profile that never said — but when the profile *did* say and the
        # corpus could not supply the date, an instant channel switches on for
        # the whole series and answers every information-path question "yes".
        # The check would then pass everything and report green. Refuse instead:
        # a silent pass is worse than no check at all, because green is trusted.
        unresolved = [c for c in ctx.profile.channels
                      if c.available_from_book and c.available_from_date is None]
        if unresolved:
            named = ", ".join(f"{c.name} (from {c.available_from_book!r})" for c in unresolved)
            return [Marginalium(
                check=self.name, severity="hard", scene_ref=ctx.draft.ref,
                message=(
                    f"channel {named} never resolved to a date, so it would count as available for "
                    "the whole series and pass every information-path question. Standing down rather "
                    "than reporting a green this check cannot support."
                ),
                fixes=[
                    "run `bp ingest` — availability resolves from the first dated scene of the named book",
                    "run `bp profile --check` to see what each channel resolved to",
                    "or set an explicit `available_from_date` on the channel in the profile",
                ],
            )]

        # Who is on the page *as an agent*. A character who is merely talked
        # about cannot violate anything by being talked about; folding them in
        # produced three findings per error, which is how a margin becomes
        # unreadable and a gate becomes a rubber stamp.
        people = self._agents(ctx)

        for mention in ctx.mentions("event"):
            row = ctx.graph.event(mention.target_id)
            if row is None:
                continue
            # An event that has not happened yet is a different failure.
            if row["day_lo"] is not None and row["day_lo"] > when.hi:
                out.append(self._not_yet(ctx, mention, row, when))
                continue

            blocked: list[tuple[str, object]] = []
            for who in sorted(people):
                k = ctx.kg.earliest_knowledge(mention.target_id, who)
                if k.indeterminate:
                    out.append(Marginalium(
                        check=self.name, severity="note", scene_ref=ctx.draft.ref, line=mention.line,
                        excerpt=mention.excerpt,
                        message=(f"cannot decide whether {who} could know about {row['summary']!r}: {k.reason}"),
                        fixes=["date the event in the graph", "add a distance for the places involved"],
                    ))
                    continue
                if not k.knows_by(when):
                    blocked.append((who, k))
            if blocked:
                out.append(self._violation(ctx, mention, row, blocked, when))
        return out

    @staticmethod
    def _agents(ctx: CheckContext) -> set[str]:
        """The POV, the declared cast, and anyone the text shows acting.

        Not everyone named. 'Dael is dead,' Ana said — Dael is named, but Dael
        is not the one who could be violating an information path here.
        """
        people = {ctx.pov} | {ctx.profile.canonical(c) for c in ctx.draft.cast}
        for m in ctx.mentions("entity"):
            row = ctx.graph.entity(m.target_id)
            if row is not None and row["status"] == "dead":
                continue  # the object-and-body checker owns this case
            if _acts_on_page(ctx.draft.text, m.label):
                people.add(m.target_id)
        people.discard("")
        return people

    def _violation(self, ctx: CheckContext, mention, row, blocked: list, when) -> Marginalium:
        """One mark per reference, naming everyone it is wrong for."""
        cites = ctx.graph.citations_for("event", row["event_id"])
        reachable = [(who, k) for who, k in blocked if k.reachable]
        soonest = min((k.day for _, k in reachable), default=None)

        who_list = ", ".join(who for who, _ in blocked)
        if soonest is not None:
            first_who, first_k = min(reachable, key=lambda t: t[1].day)
            gap = soonest - when.hi
            why = (f"the earliest any of them could know is {ctx.fmt(soonest)} "
                   f"({first_who}, {gap:,.0f} days after this chapter) — {ctx.kg.explain(first_k)}")
        else:
            why = "there is no recorded information path to any of them"

        couriers = [c for c, _ in ctx.kg.who_could_know(row["event_id"], when.hi)
                    if c not in {w for w, _ in blocked}][:4]
        fixes = [
            (f"move the line to a chapter after {ctx.fmt(soonest)}" if soonest is not None
             else "move the line to a later chapter, once a path exists"),
            (f"route the news through someone who could have it by now "
             f"({', '.join(couriers)}) and put that on the page") if couriers else
            "add a report to the graph showing how the news travelled, and put it on the page",
            "cut it",
        ]
        return Marginalium(
            check=self.name, severity="hard", scene_ref=ctx.draft.ref, line=mention.line,
            excerpt=mention.excerpt,
            message=(f"{who_list} refer{'s' if len(blocked) == 1 else ''} to {row['summary']!r} "
                     f"(event {row['event_id']}), but {why}."),
            citations=[Citation(scene=c.scene, quote=c.quote) for c in cites[:2]],
            fixes=fixes,
            metric={"earliest_arrival_day": float(soonest) if soonest is not None else -1.0,
                    "chapter_day": float(when.hi), "characters_affected": float(len(blocked))},
        )

    def _not_yet(self, ctx: CheckContext, mention, row, when) -> Marginalium:
        return Marginalium(
            check=self.name, severity="hard", scene_ref=ctx.draft.ref, line=mention.line,
            excerpt=mention.excerpt,
            message=(f"this chapter is dated {ctx.fmt(when.hi)} but refers to {row['summary']!r} "
                     f"(event {row['event_id']}), which happens on {ctx.fmt(row['day_lo'])} — "
                     f"it has not happened yet"),
            citations=[Citation(scene=c.scene, quote=c.quote)
                       for c in ctx.graph.citations_for("event", row["event_id"])[:2]],
            fixes=["re-date the chapter", "re-date the event if the graph has it wrong", "cut the reference"],
            metric={"event_day": float(row["day_lo"]), "chapter_day": float(when.hi)},
        )
