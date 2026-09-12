"""Path queries over the event-and-belief graph.

Continuity checking is mostly this module. The question a continuity editor
actually needs answered is not "is this true?" but **"is there an information
path from this event to this character by this date?"** — and that is a shortest-
path problem on a graph whose edge weights come from the profile's distances and
channel speeds.

The wrinkle that makes it more than plain Dijkstra: a relay cannot forward what
it does not yet know. An edge from A to B is only usable at a departure time at
or after A's own earliest knowledge. So the relaxation is over *earliest arrival
time*, and the graph is time-dependent. Ordinary Dijkstra is still correct here
because arrival time is non-decreasing in departure time (no channel gets you
there sooner by leaving later).

Three sources put knowledge into a character's hands:

1. **Observation.** You were there.
2. **A report.** Somebody told you, over a channel, and it took as long as it
   took.
3. **Inheritance.** For series where minds are copied, a clone inherits the
   parent's beliefs as of the moment of the fork and diverges from there.

Everything else — including "obviously they'd have heard by now" — is not a path,
and the checker says so rather than assuming.
"""

from __future__ import annotations

import heapq
import json
import math
from dataclasses import dataclass, field
from typing import Iterable

from .db import Graph
from .errors import UnknownDistance
from .timeline import Span


@dataclass
class Hop:
    """One step in an information path, for explaining a verdict to a human."""

    kind: str          # observed | report | inherited
    sender: str
    recipient: str
    channel: str
    depart_day: float | None
    arrive_day: float
    computed: bool = False
    note: str = ""


@dataclass
class Knowledge:
    """When a character could first know an event, and by what route."""

    character: str
    event_id: str
    day: float | None                    # None == no path found
    path: list[Hop] = field(default_factory=list)
    #: True when the graph lacks the data to answer (unknown place, undated
    #: event). The checker must abstain rather than accuse.
    indeterminate: bool = False
    reason: str = ""

    @property
    def reachable(self) -> bool:
        return self.day is not None

    def knows_by(self, when: Span) -> bool:
        """Could they know it by this date? Uses the *late* end of the window,
        so an uncertain scene date is given the benefit of the doubt."""
        return self.day is not None and self.day <= when.hi


class KnowledgeGraph:
    """Read-only reasoning over what the store already contains."""

    def __init__(self, graph: Graph):
        self.g = graph
        self.profile = graph.profile

    # ------------------------------------------------------------------ lookup
    def _canon(self, name: str) -> str:
        return self.profile.canonical(name) if self.profile else name.strip()

    def _event_day(self, event_id: str) -> float | None:
        row = self.g.event(event_id)
        return row["day_lo"] if row and row["day_lo"] is not None else None

    def _explicit_belief(self, event_id: str, character: str) -> tuple[str, float | None] | None:
        row = self.g.conn.execute(
            """SELECT state, as_of_day FROM beliefs
               WHERE event_id=? AND character=? COLLATE NOCASE
               ORDER BY as_of_day IS NULL, as_of_day LIMIT 1""",
            (event_id, character),
        ).fetchone()
        return (row["state"], row["as_of_day"]) if row else None

    # ------------------------------------------------------- the core query
    def earliest_knowledge(self, event_id: str, character: str) -> Knowledge:
        """Earliest day ``character`` could know about ``event_id``."""
        character = self._canon(character)
        row = self.g.event(event_id)
        if row is None:
            return Knowledge(character, event_id, None, indeterminate=True, reason="no such event")

        event_day = row["day_lo"]
        if event_day is None:
            return Knowledge(character, event_id, None, indeterminate=True,
                             reason="event has no in-world date; cannot compute transit")

        observers = {self._canon(x) for x in json.loads(row["observed_by"] or "[]")}
        participants = {self._canon(x) for x in json.loads(row["participants"] or "[]")}
        # Being present is observing unless the extraction says otherwise.
        sources = observers | participants

        best: dict[str, float] = {}
        via: dict[str, Hop] = {}
        heap: list[tuple[float, str]] = []
        for who in sources:
            best[who] = event_day
            via[who] = Hop("observed", who, who, "presence", None, event_day, note="present at the event")
            heapq.heappush(heap, (event_day, who))

        # An explicitly recorded "knows" belief is canon and can beat any path:
        # the author said so, and the author outranks our arithmetic.
        for b in self.g.conn.execute(
            "SELECT character, state, as_of_day FROM beliefs WHERE event_id=? AND state='knows'", (event_id,)
        ):
            if b["as_of_day"] is None:
                continue
            who = self._canon(b["character"])
            if b["as_of_day"] < best.get(who, math.inf):
                best[who] = b["as_of_day"]
                via[who] = Hop("stated", who, who, "text", None, b["as_of_day"], note="belief stated in canon")
                heapq.heappush(heap, (b["as_of_day"], who))

        reports = list(self.g.conn.execute("SELECT * FROM reports WHERE event_id=?", (event_id,)))
        by_sender: dict[str, list] = {}
        for rep in reports:
            by_sender.setdefault(self._canon(rep["sender"]), []).append(rep)

        forks = self._fork_edges()

        while heap:
            day, who = heapq.heappop(heap)
            if day > best.get(who, math.inf):
                continue
            if who == character:
                break
            for rep in by_sender.get(who, []):
                arrive = self._relax_report(rep, sender_knows=day, event_row=row)
                if arrive is None:
                    continue
                arrive_day, hop = arrive
                target = self._canon(rep["recipient"])
                if arrive_day < best.get(target, math.inf):
                    best[target] = arrive_day
                    via[target] = hop
                    heapq.heappush(heap, (arrive_day, target))
            # A copy made after its parent knew inherits the knowledge, at the
            # moment of the fork.
            for child, fork_day in forks.get(who, []):
                if fork_day is None or fork_day < day:
                    continue
                if fork_day < best.get(child, math.inf):
                    best[child] = fork_day
                    via[child] = Hop("inherited", who, child, "fork", day, fork_day,
                                     note="copy inherits parent's beliefs at the fork")
                    heapq.heappush(heap, (fork_day, child))

        if character not in best:
            explicit = self._explicit_belief(event_id, character)
            if explicit and explicit[0] in {"knows", "believes_false", "misinformed"} and explicit[1] is None:
                return Knowledge(character, event_id, None, indeterminate=True,
                                 reason=f"canon records state {explicit[0]!r} but gives no date")
            return Knowledge(character, event_id, None, reason="no information path")

        return Knowledge(character, event_id, best[character], path=self._path_to(character, via))

    def _relax_report(self, rep, *, sender_knows: float, event_row) -> tuple[float, Hop] | None:
        """Earliest this report can land, given the sender only just found out."""
        sender = self._canon(rep["sender"])
        recipient = self._canon(rep["recipient"])
        stated_depart = rep["depart_day"]
        stated_arrive = rep["arrive_day"]

        depart = max(sender_knows, stated_depart) if stated_depart is not None else sender_knows

        # Case 1: the text states an arrival. Trust it, unless it is impossible.
        if stated_arrive is not None and not rep["computed"]:
            if stated_arrive < sender_knows:
                return None  # the sender could not have sent it yet
            return stated_arrive, Hop("report", sender, recipient, rep["channel"],
                                      stated_depart, stated_arrive, note="arrival stated in canon")

        # Case 2: compute it. distance / speed.
        transit = self._transit_days(rep, event_row, depart)
        if transit is None:
            if stated_arrive is not None:
                return stated_arrive, Hop("report", sender, recipient, rep["channel"],
                                          stated_depart, stated_arrive, computed=True)
            return None
        arrive = depart + transit
        return arrive, Hop("report", sender, recipient, rep["channel"], depart, arrive, computed=True)

    def _transit_days(self, rep, event_row, depart: float) -> float | None:
        if self.profile is None:
            return None
        channel = self.profile.channel(rep["channel"]) or self.profile.fastest_channel(depart)
        if channel is None:
            return None
        if not channel.available_at(depart):
            channel = self.profile.fastest_channel(depart)
            if channel is None:
                return None
        sender, recipient = self._canon(rep["sender"]), self._canon(rep["recipient"])
        at_event = sender in {
            self._canon(x)
            for x in json.loads(event_row["observed_by"] or "[]") + json.loads(event_row["participants"] or "[]")
        }
        origin = self.location_of(sender, depart) or (event_row["where_place"] if at_event else "")
        if at_event and event_row["where_place"]:
            origin = event_row["where_place"]
        dest = self.location_of(recipient, depart)
        if not origin or not dest:
            return None
        try:
            return self.profile.space.signal_days(origin, dest, channel.speed,
                                                  table_factor=channel.table_factor)
        except UnknownDistance:
            return None

    def _fork_edges(self) -> dict[str, list[tuple[str, float | None]]]:
        if self.profile is None or not self.profile.clone_lineage:
            return {}
        out: dict[str, list[tuple[str, float | None]]] = {}
        for row in self.g.conn.execute("SELECT entity_id,parent_id,fork_day FROM entities WHERE parent_id != ''"):
            out.setdefault(self._canon(row["parent_id"]), []).append((self._canon(row["entity_id"]), row["fork_day"]))
        return out

    @staticmethod
    def _path_to(character: str, via: dict[str, Hop]) -> list[Hop]:
        path, seen, cur = [], set(), character
        while cur in via and cur not in seen:
            seen.add(cur)
            hop = via[cur]
            path.append(hop)
            if hop.kind in {"observed", "stated"}:
                break
            cur = hop.sender
        return list(reversed(path))

    # ---------------------------------------------------------------- location
    def location_of(self, character: str, day: float, *, nearest: bool = True) -> str:
        """Where a character was, as of a day, from the last thing that placed them.

        Scenes place a character more reliably than events do (a scene names its
        POV and its setting), so scenes win ties.

        With ``nearest``, a character whose every recorded placement falls after
        ``day`` resolves to their earliest one instead of to nothing. That is not
        a fudge: over a ten-year signal transit the recipient's position at the
        moment of *departure* is not knowable from the text either, and the
        nearest placement the graph can cite is the honest approximation. The
        alternative — returning nothing — silently disables the check, which is
        the worse failure.
        """
        canon = self._canon(character)
        names = self.profile.alias_set(canon) if self.profile else [canon]
        best_day, best_place = -math.inf, ""

        for row in self.g.conn.execute(
            "SELECT place, day_lo, cast_json, pov FROM scenes WHERE day_lo IS NOT NULL AND day_lo <= ? ORDER BY day_lo DESC LIMIT 400",
            (day,),
        ):
            if not row["place"]:
                continue
            cast = {self._canon(c) for c in json.loads(row["cast_json"] or "[]")}
            if self._canon(row["pov"] or "") == canon or canon in cast or any(n in cast for n in names):
                if row["day_lo"] > best_day:
                    best_day, best_place = row["day_lo"], row["place"]
                break

        for row in self.g.conn.execute(
            """SELECT where_place, day_lo, participants, observed_by FROM events
               WHERE day_lo IS NOT NULL AND day_lo <= ? AND where_place != ''
               ORDER BY day_lo DESC LIMIT 400""",
            (day,),
        ):
            people = {self._canon(x) for x in json.loads(row["participants"] or "[]")} | {
                self._canon(x) for x in json.loads(row["observed_by"] or "[]")
            }
            if canon in people:
                if row["day_lo"] > best_day:
                    best_day, best_place = row["day_lo"], row["where_place"]
                break

        if best_place or not nearest:
            return best_place
        return self._earliest_placement(canon, names)

    def _earliest_placement(self, canon: str, names: list[str]) -> str:
        """First place the corpus ever puts this character. Used only as the
        fallback above."""
        for row in self.g.conn.execute(
            "SELECT place, cast_json, pov FROM scenes WHERE place != '' ORDER BY day_lo IS NULL, day_lo LIMIT 500"
        ):
            cast = {self._canon(c) for c in json.loads(row["cast_json"] or "[]")}
            if self._canon(row["pov"] or "") == canon or canon in cast or any(n in cast for n in names):
                return row["place"]
        for row in self.g.conn.execute(
            """SELECT where_place, participants, observed_by FROM events
               WHERE where_place != '' ORDER BY day_lo IS NULL, day_lo LIMIT 500"""
        ):
            people = {self._canon(x) for x in json.loads(row["participants"] or "[]")} | {
                self._canon(x) for x in json.loads(row["observed_by"] or "[]")
            }
            if canon in people:
                return row["where_place"]
        return ""

    def last_placement(self, character: str, before_day: float) -> tuple[str, float] | None:
        """The most recent (place, day) the graph can vouch for. For the
        geography check, which needs the day as well as the place."""
        canon = self._canon(character)
        rows = self.g.conn.execute(
            """SELECT place, day_lo, cast_json, pov FROM scenes
               WHERE day_lo IS NOT NULL AND day_lo <= ? AND place != ''
               ORDER BY day_lo DESC LIMIT 500""",
            (before_day,),
        ).fetchall()
        for row in rows:
            cast = {self._canon(c) for c in json.loads(row["cast_json"] or "[]")}
            if self._canon(row["pov"] or "") == canon or canon in cast:
                return row["place"], row["day_lo"]
        return None

    # ----------------------------------------------------------------- summary
    def belief_snapshot(self, character: str, day: float) -> dict[str, str]:
        """What this character knows, as of a day.

        'What did every POV believe going into chapter 47' is a checkout, not a
        guess — this is the function that makes that true.
        """
        out: dict[str, str] = {}
        for row in self.g.events():
            if row["day_lo"] is not None and row["day_lo"] > day:
                continue
            explicit = self._explicit_belief(row["event_id"], self._canon(character))
            if explicit and explicit[0] in {"believes_false", "misinformed", "suspects"}:
                if explicit[1] is None or explicit[1] <= day:
                    out[row["event_id"]] = explicit[0]
                    continue
            k = self.earliest_knowledge(row["event_id"], character)
            if k.reachable and k.day is not None and k.day <= day:
                out[row["event_id"]] = "knows"
            elif not k.indeterminate:
                out[row["event_id"]] = "unaware"
        return out

    def who_could_know(self, event_id: str, day: float) -> list[tuple[str, float]]:
        """Everyone with a path to this event by this day — the raw material for
        the checker's second fix suggestion: route the news through someone who
        could have it."""
        out = []
        for row in self.g.entities("character"):
            k = self.earliest_knowledge(event_id, row["entity_id"])
            if k.reachable and k.day is not None and k.day <= day:
                out.append((row["entity_id"], k.day))
        return sorted(out, key=lambda t: t[1])

    def explain(self, k: Knowledge) -> str:
        cal = self.profile.calendar if self.profile else None
        fmt = (lambda d: cal.format(d)) if cal else (lambda d: f"day {d:.0f}")
        if k.indeterminate:
            return f"indeterminate: {k.reason}"
        if not k.reachable:
            return f"no information path to {k.character}"
        steps = []
        for hop in k.path:
            if hop.kind in {"observed", "stated"}:
                steps.append(f"{hop.recipient} {hop.note} ({fmt(hop.arrive_day)})")
            elif hop.kind == "inherited":
                steps.append(f"{hop.sender} → {hop.recipient} at fork ({fmt(hop.arrive_day)})")
            else:
                tag = " computed" if hop.computed else ""
                steps.append(f"{hop.sender} → {hop.recipient} via {hop.channel}{tag}, arrives {fmt(hop.arrive_day)}")
        return " · ".join(steps)
