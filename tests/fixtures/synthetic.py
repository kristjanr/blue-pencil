"""A synthetic series, built so the engine can be tested without a corpus.

Two reasons this is a synthetic world rather than a real one. First, the engine
is series-agnostic by construction, so a made-up series is a legitimate subject —
if the checkers need a *particular* series to work, they are broken. Second, the
test needs a world whose every distance, date and information path is known
exactly, so that a caught error and a false alarm can be told apart with
certainty.

"Longwater" is a light-lag space opera, chosen because light-lag makes the
epistemic check's arithmetic verifiable by hand: Sol to Vela is twelve light
years, so news of a Vela event reaches Sol twelve years later, and no amount of
narrative convenience changes that.
"""

from __future__ import annotations

from pathlib import Path

from bp.db import Graph, SceneRow
from bp.models import (
    BeliefRecord, Citation, Entity, Event, ObjectRecord, Promise, Report, Thread,
)
from bp.profile import SeriesProfile
from bp.textstats import estimate_tokens, spec_from_scenes, words

# Light-years between systems. Sol is the hub.
DISTANCES = {
    ("Sol", "Kepler"): 6.0,
    ("Sol", "Vela"): 12.0,
    ("Sol", "Thule"): 20.0,
    ("Kepler", "Vela"): 8.0,
    ("Kepler", "Thule"): 15.0,
    ("Vela", "Thule"): 10.0,
}

PROFILE_DICT = {
    "name": "longwater",
    "narration": {"mode": "first_person_rotating", "pov_from": "chapter_header"},
    "time": {"calendar": "gregorian"},
    "space": {"model": "interstellar", "travel_speed": "2c"},
    "information": {
        "channels": [
            {"name": "radio", "speed": "c"},
            # The instant channel arrives partway through the series — the case
            # that catches profiles which assume today's physics held always.
            {"name": "lattice", "speed": "instant", "available_from_date": "2190-01-01"},
        ]
    },
    "entities": {
        "aliases": {"Ana": ["Anaïs", "the Cartographer"], "Boro": ["Borograve"], "Ana-2": ["Ana Two"]},
        "clone_lineage": True,
        "revivable": False,
    },
    "style": {"units": "chapter"},
}


def make_profile() -> SeriesProfile:
    p = SeriesProfile.from_dict(PROFILE_DICT)
    for (a, b), ly in DISTANCES.items():
        p.space.pairs[tuple(sorted((a.casefold(), b.casefold())))] = ly
        p.space.places.update((a, b))
    return p


# --------------------------------------------------------------------- corpus
_PROSE = {
    "Ana": (
        "I ran the numbers twice before I let myself believe them. {beat} The console at {place} "
        "threw up the same answer either way, which is the sort of thing that stops being "
        "reassuring after the third pass.\n\n"
        "\"You could just accept it,\" said the desk, which had opinions.\n\n"
        "I did not accept it. I have never once accepted a first answer, and the one time I "
        "nearly did, a survey team spent two years chasing a rounding error across four "
        "systems. {beat2} So I checked it again, and the numbers held, and I sat with that for "
        "a while in the dark.\n\n"
        "Outside the port, the {place} traffic lanes were doing their slow arithmetic. "
        "Something in me relaxed at the sight of it. Order, even somebody else's order."
    ),
    "Boro": (
        "The vault at {place} does not like being opened and it likes being closed even less. "
        "{beat} I had my hands in it up to the elbow for nine hours before the seals took.\n\n"
        "Nine hours. I counted them, because counting is what you do when the alternative is "
        "thinking about what you are sealing in.\n\n"
        "\"Done?\" the foreman asked.\n\n"
        "\"Done,\" I said, and it was not a lie, exactly. {beat2} The stone went into the "
        "cradle and the cradle went into the wall and the wall closed over it like water. "
        "I have built things I was prouder of. I have never built anything I was more certain "
        "about."
    ),
    "Cyra": (
        "{place} came apart in eleven minutes. {beat}\n\n"
        "I want to be precise about this because precision is all I have left to offer. "
        "Eleven minutes from the first fault to the last carrier dropping out, and I logged "
        "every one of them, and the log went out on the long channel where it will arrive "
        "when it arrives.\n\n"
        "Years from now somebody at another star will read what I am writing and feel something "
        "about it. {beat2} That is the part I cannot hold in my head. My present is their "
        "history and there is nothing either of us can do about the gap."
    ),
    "Dael": (
        "Nobody comes to {place}. That is the entire appeal and I will not hear otherwise. "
        "{beat}\n\n"
        "I keep a garden here, under lamps, in defiance of every sensible argument. "
        "Tomatoes. At twenty light years from anywhere, tomatoes.\n\n"
        "\"They'll never ripen,\" said the last supply pilot who bothered.\n\n"
        "They ripened. {beat2} I sent him a photograph that will reach him in twenty years, "
        "by which time he will have forgotten the conversation and I will have made my point "
        "anyway."
    ),
}

_BEATS = [
    "The relay had been quiet for two days.", "Somebody had moved the chart tables.",
    "A courier drone was still bleeding coolant on the pad.", "The lamps were down to half.",
    "The long channel showed nothing at all.", "There was frost on the inside of the port.",
    "A child had drawn on the bulkhead in grease pencil.", "The tide charts were three years stale.",
]

#: (book, chapter, pov, place, date). The corpus the graph is built from.
SCENES = [
    ("LW1", 1, "Ana", "Sol", "2180-01-01"),
    ("LW1", 2, "Boro", "Kepler", "2180-02-01"),
    ("LW1", 3, "Cyra", "Vela", "2180-03-01"),
    ("LW1", 4, "Dael", "Thule", "2180-04-01"),
    ("LW1", 5, "Cyra", "Vela", "2180-06-01"),
    ("LW2", 1, "Boro", "Kepler", "2181-03-01"),
    ("LW2", 2, "Ana", "Sol", "2181-08-01"),
    ("LW2", 3, "Dael", "Thule", "2182-05-01"),
    ("LW2", 4, "Ana", "Sol", "2183-01-01"),
    ("LW3", 1, "Ana", "Sol", "2184-02-01"),
    ("LW3", 2, "Boro", "Kepler", "2184-09-01"),
    ("LW3", 3, "Cyra", "Vela", "2185-01-01"),
]


def scene_text(pov: str, place: str, i: int) -> str:
    return _PROSE[pov].format(place=place, beat=_BEATS[i % len(_BEATS)],
                              beat2=_BEATS[(i + 3) % len(_BEATS)])


CIT = lambda scene: [Citation(scene=scene, quote="…")]  # noqa: E731


def build(db_path: str | Path = ":memory:") -> tuple[Graph, SeriesProfile]:
    """A fully populated graph: scenes, entities, events with information paths,
    objects, promises, threads, and measured style specs."""
    profile = make_profile()
    graph = Graph(db_path, profile, create=True)

    for i, (book, chapter, pov, place, date) in enumerate(SCENES):
        graph.add_book(book, book, int(book[-1]))
        text = scene_text(pov, place, i)
        span = profile.calendar.parse(date)
        graph.add_scene(SceneRow(
            scene_id=f"{book}.{chapter:02d}.1", book_id=book, chapter=chapter, scene=1,
            pov=pov, date_text=date, day_lo=span.lo, day_hi=span.hi, place=place,
            cast=[pov], text=text, words=len(words(text)), tokens=estimate_tokens(text), ord=i,
        ))

    # Measured style layer, exactly as ingestion computes it.
    from collections import defaultdict

    by_pov: dict[str, list[str]] = defaultdict(list)
    for i, (_, _, pov, place, _) in enumerate(SCENES):
        by_pov[pov].append(scene_text(pov, place, i))
    for pov, texts in by_pov.items():
        graph.write_style(spec_from_scenes(pov, texts * 12))  # enough words to clear the floor
        from bp.textstats import Baseline
        import json

        b = Baseline.build(pov, texts * 12)
        graph.set_meta(f"baseline:{pov}", json.dumps(
            {"total_words": b.total_words, "rates": b.rates, "unigram_rates": b.unigram_rates}))

    # ------------------------------------------------------------- entities
    for eid, status, substrate, parent, fork in [
        ("Ana", "alive", "Cartographer's Rig", "", ""),
        ("Boro", "alive", "", "", ""),
        ("Cyra", "alive", "", "", ""),
        ("Dael", "dead", "", "", ""),
        ("Ana-2", "alive", "Second Rig", "Ana", "2183-01-01"),
    ]:
        graph.write_entity(Entity(
            entity_id=eid, name=eid, kind="character", status=status, substrate=substrate,
            parent_id=parent, forked_on=fork,
            aliases=profile.aliases.get(eid, []),
            citations=CIT("LW1.01.1"),
        ))

    # --------------------------------------------------------------- events
    # Every arrival below is COMPUTED by the engine from the distances above.
    # Nothing here states an arrival date, which is the point.
    events = [
        Event(event_id="E-001", summary="The Vela relay is destroyed by the Quiet",
              when="2180-06-01", when_certainty="canonical", where="Vela", observed_by=["Cyra"],
              reports=[Report(**{"from": "Cyra", "to": "Ana", "channel": "radio"})],
              beliefs=[BeliefRecord(character="Ana", state="unaware", as_of="2180-06-01")],
              consequences=["Vela is cut off from the long channel"],
              reader_state="shown directly", citations=CIT("LW1.05.1")),
        Event(event_id="E-002", summary="Boro seals the Kepler vault",
              when="2181-03-01", when_certainty="canonical", where="Kepler", observed_by=["Boro"],
              reports=[Report(**{"from": "Boro", "to": "Ana", "channel": "radio"})],
              consequences=["the Ledger Stone is unreachable"],
              citations=CIT("LW2.01.1")),
        Event(event_id="E-003", summary="Dael is killed by a hull breach at Thule",
              when="2182-05-01", when_certainty="canonical", where="Thule", observed_by=["Dael"],
              reports=[],   # nobody survives to send it — no path to anyone
              consequences=["the Thule garden is abandoned"],
              citations=CIT("LW2.03.1")),
        Event(event_id="E-004", summary="Ana forks a copy of herself, Ana-2",
              when="2183-01-01", when_certainty="canonical", where="Sol",
              observed_by=["Ana", "Ana-2"], citations=CIT("LW2.04.1")),
        Event(event_id="E-005", summary="The lattice comes online and abolishes the signal delay",
              when="2190-01-01", when_certainty="canonical", where="Sol", observed_by=["Ana"],
              reports=[Report(**{"from": "Ana", "to": "Boro", "channel": "lattice"})],
              citations=CIT("LW3.01.1")),
        Event(event_id="E-006", summary="Cyra maps the drift lanes beyond Vela",
              when="2184-01-01", when_certainty="canonical", where="Vela", observed_by=["Cyra"],
              reports=[Report(**{"from": "Cyra", "to": "Boro", "channel": "radio"})],
              citations=CIT("LW3.03.1")),
        Event(event_id="E-007", summary="Boro finds the second cradle empty",
              when="2184-09-01", when_certainty="canonical", where="Kepler", observed_by=["Boro"],
              reports=[Report(**{"from": "Boro", "to": "Ana", "channel": "radio"})],
              citations=CIT("LW3.02.1")),
        Event(event_id="E-008", summary="Thule stops answering the long channel",
              when="2182-06-01", when_certainty="canonical", where="Thule", observed_by=[],
              reports=[], citations=CIT("LW2.03.1")),
    ]
    for ev in events:
        graph.write_event(ev)

    # -------------------------------------------------------------- objects
    graph.write_object(ObjectRecord(
        object_id="O-001", name="Ledger Stone", aliases=["the Stone"], location="Kepler",
        holder="Boro", as_of="2181-03-01", significant=True, state="sealed in the vault",
        citations=CIT("LW2.01.1")))
    graph.write_object(ObjectRecord(
        object_id="O-002", name="Cartographer's Rig", location="Sol", holder="Ana",
        as_of="2180-01-01", significant=True, citations=CIT("LW1.01.1")))

    # ------------------------------------------------- promises and threads
    graph.write_promise(Promise(
        promise_id="P-001", summary="Cyra's log will reach Sol one day",
        kind="foreshadow", planted_in=["LW1.05.1"], owed_by=["Cyra"], weight=0.8,
        citations=CIT("LW1.05.1")))
    graph.write_promise(Promise(
        promise_id="P-002", summary="Whatever Boro sealed in the vault will come out",
        kind="setup", planted_in=["LW2.01.1"], owed_by=["Boro"], weight=0.9,
        citations=CIT("LW2.01.1")))
    graph.write_thread(Thread(
        thread_id="T-001", name="The Quiet", state="Vela is dark; nobody at Sol knows why yet",
        characters=["Cyra", "Ana"], last_scene="LW3.03.1", citations=CIT("LW1.05.1")))
    graph.write_thread(Thread(
        thread_id="T-002", name="The Kepler vault", state="sealed, contents unknown",
        characters=["Boro"], last_scene="LW3.02.1", citations=CIT("LW2.01.1")))

    graph.commit()
    return graph, profile


def write_corpus(directory: str | Path) -> Path:
    """The same series as plain text files, for testing ingestion end to end."""
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    by_book: dict[str, list[str]] = {}
    for i, (book, chapter, pov, place, date) in enumerate(SCENES):
        by_book.setdefault(book, []).append(
            f"Chapter {chapter}: {pov}\n\ndate: {date}\n\n{scene_text(pov, place, i)}\n"
        )
    for book, chapters in by_book.items():
        (d / f"{book}.txt").write_text("\n\n".join(chapters), encoding="utf-8")
    return d
