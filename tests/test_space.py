"""Distances and speeds — the other half of the epistemic arithmetic."""

import pytest

from bp.errors import ProfileError, UnknownDistance
from bp.space import INSTANT, SpaceModel, parse_speed
from bp.timeline import DAYS_PER_LIGHT_YEAR


def test_speed_forms():
    assert parse_speed("instant") == INSTANT
    assert parse_speed("c") * DAYS_PER_LIGHT_YEAR == pytest.approx(1.0)
    assert parse_speed("0.5c") * DAYS_PER_LIGHT_YEAR == pytest.approx(0.5)
    assert parse_speed("2 ly/day") == 2.0
    with pytest.raises(ProfileError):
        parse_speed("quite fast")


def test_ten_light_years_at_c_takes_ten_years():
    sm = SpaceModel(model="interstellar", pairs={("a", "b"): 10.0})
    assert sm.signal_days("A", "B", parse_speed("c")) == pytest.approx(10 * DAYS_PER_LIGHT_YEAR)


def test_instant_channel_costs_nothing():
    sm = SpaceModel(model="interstellar", pairs={("a", "b"): 10.0})
    assert sm.signal_days("A", "B", INSTANT) == 0.0


def test_travel_table_reports_days_directly():
    sm = SpaceModel(model="travel_table", pairs={("kings landing", "winterfell"): 30.0})
    assert sm.travel_days("Kings Landing", "Winterfell") == 30.0


def test_unknown_distance_raises_rather_than_guessing():
    sm = SpaceModel(model="interstellar", pairs={})
    assert not sm.known("A", "B")
    with pytest.raises(UnknownDistance):
        sm.separation("A", "B")


def test_no_space_model_disables_the_arithmetic():
    sm = SpaceModel(model="none")
    assert sm.signal_days("anywhere", "elsewhere", parse_speed("c")) == 0.0


def test_travel_table_channels_are_multiples_of_the_road():
    """A raven beats a rider; a rumour lags both. Reading a travel-table channel
    as a speed made every carrier instantaneous — which silently disabled the
    epistemic check in exactly the genre that needs it most."""
    sm = SpaceModel(model="travel_table", pairs={("kings landing", "winterfell"): 30.0})
    speed = parse_speed("c")
    assert sm.signal_days("Kings Landing", "Winterfell", speed, table_factor=1.0) == 30.0
    assert sm.signal_days("Kings Landing", "Winterfell", speed, table_factor=0.35) == pytest.approx(10.5)
    assert sm.signal_days("Kings Landing", "Winterfell", speed, table_factor=2.5) == 75.0


def test_distance_files_tolerate_comments_and_reject_bad_numbers(tmp_path):
    path = tmp_path / "d.csv"
    path.write_text("from,to,light_years\n# a comment row\n\nSol,Vela,12\n")
    sm = SpaceModel.load({"model": "interstellar", "distances": "d.csv"}, base=tmp_path)
    assert sm.separation("Sol", "Vela") == 12.0

    path.write_text("from,to,light_years\nSol,Vela,not-a-number\n")
    with pytest.raises(ProfileError):
        SpaceModel.load({"model": "interstellar", "distances": "d.csv"}, base=tmp_path)


def test_a_transit_shortcut_downgrades_an_impossible_journey_to_a_note(world):
    """Book 5 introduces wormholes and `space` had no way to say so: a single
    travel_speed cannot express "and then this became possible". Once a transit
    mechanism exists that the profile cannot price, an over-long journey is
    something the checker cannot demonstrate is impossible — so it abstains and
    names the reason, rather than reporting a contradiction it cannot show.

    Deliberately carries no topology. A guessed one would clear journeys that
    really are impossible, which is the expensive direction.
    """
    from bp.checks.geography import GeographyCheck
    from bp.draftdoc import Draft
    from bp.checks import CheckContext
    from bp.policy import RunPolicy
    from bp.timeline import Span

    graph, profile = world
    far = max(profile.space.pairs.values())
    assert far > 0

    def run():
        draft = Draft.parse("Cyra is here, impossibly.\n")
        draft.meta = {"pov": "Ana", "date": "2185-06-01", "place": "Sol"}
        ctx = CheckContext(draft=draft, graph=graph, profile=profile,
                           policy=RunPolicy.from_dict({}))
        return GeographyCheck().run(ctx)

    before = run()
    profile.space.transit_from = [("the gate network", "book 1")]
    profile.space.transit_from_day = {"the gate network": 0.0}
    after = run()

    assert {m.severity for m in before} <= {"hard", "note"}
    if any(m.severity == "hard" for m in before):
        assert all(m.severity == "note" for m in after), \
            "with an unpriceable shortcut in play the check must abstain"
        assert any("cannot be decided" in m.message.lower() for m in after)


def test_a_shortcut_that_does_not_exist_yet_changes_nothing():
    """It abstains from the date the mechanism appears, and not before —
    otherwise books 1-4, where an over-long journey really is a contradiction,
    would stop being checked."""
    from bp.space import SpaceModel

    sm = SpaceModel(model="interstellar")
    sm.transit_from = [("wormholes", "book 5")]
    sm.transit_from_day = {"wormholes": 1000.0}
    assert sm.shortcut_at(999.0) == ""
    assert sm.shortcut_at(1000.0) == "wormholes"
    assert sm.shortcut_at(None) == ""
