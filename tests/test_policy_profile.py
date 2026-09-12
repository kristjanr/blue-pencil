"""Configuration: what's true about the series vs. what the user wants this run."""

import pytest
import yaml

from bp.errors import PolicyError, ProfileError
from bp.policy import RunPolicy
from bp.profile import SeriesProfile
from bp.timeline import Span


def test_gate_modes_decide_when_a_human_is_asked():
    p = RunPolicy.from_dict({"gates": {"chapter": "auto_if_clean", "act": "manual", "plan": "auto"}})
    assert p.gate("chapter").stops(clean=False) and not p.gate("chapter").stops(clean=True)
    assert p.gate("act").stops(clean=True)
    assert not p.gate("plan").stops(clean=False)


def test_every_n_gate():
    gate = RunPolicy.from_dict({"gates": {"chapter": {"every_n": 3}}}).gate("chapter")
    assert [gate.stops(clean=True) for _ in range(6)] == [False, False, True, False, False, True]


def test_check_severity_is_run_policy_not_checker():
    p = RunPolicy.from_dict({"checks": {"repetition": {"severity": "hard", "budget": "corpus_baseline"}}})
    assert p.check("repetition").severity == "hard"
    assert p.check("repetition").options["budget"] == "corpus_baseline"


def test_disabled_check_reports_as_stood_down(world):
    from bp.checks import CheckContext, run_checks
    from bp.draftdoc import Draft

    graph, profile = world
    policy = RunPolicy.from_dict({"checks": {"epistemic": "off"}})
    sc = run_checks(CheckContext(draft=Draft.parse("Some prose."), graph=graph,
                                 profile=profile, policy=policy))
    assert "epistemic" in sc.skipped


def test_bad_severity_is_rejected_loudly():
    with pytest.raises(PolicyError):
        RunPolicy.from_dict({"checks": {"epistemic": "quite important"}})


def test_context_budget_must_be_coherent():
    with pytest.raises(PolicyError):
        RunPolicy.from_dict({"context": {"start_tokens": 50_000, "max_tokens": 10_000}})


def test_profile_rejects_an_unknown_narration_mode():
    with pytest.raises(ProfileError):
        SeriesProfile.from_dict({"narration": {"mode": "second_person_plural"}})


def test_channel_availability_resolves_from_book_dates():
    """`available_from: "book 2"` is written in book terms and has to become a
    date before any arithmetic can use it."""
    profile = SeriesProfile.from_dict({
        "information": {"channels": [{"name": "relay", "speed": "instant", "available_from": "LW2"}]},
        "time": {"calendar": "gregorian"},
    })
    assert profile.channel("relay").available_from_date is None
    profile.resolve_channel_availability({"LW1": 0.0, "LW2": 4000.0})
    assert profile.channel("relay").available_from_date == Span.at(4000.0)
    assert not profile.channel("relay").available_at(3999.0)
    assert profile.channel("relay").available_at(4001.0)


def test_aliases_resolve_both_ways(world):
    _, profile = world
    assert profile.canonical("Anaïs") == "Ana"
    assert profile.canonical("the Cartographer") == "Ana"
    assert set(profile.alias_set("Ana")) >= {"Ana", "Anaïs", "the Cartographer"}


def test_evidence_report_is_human_readable(world):
    _, profile = world
    text = "\n".join(profile.evidence_report())
    assert "radio" in text and "lattice" in text and "interstellar" in text


def test_example_profile_and_run_in_repo_parse():
    from bp.cli import _EXAMPLE_PROFILE, _EXAMPLE_RUN

    RunPolicy.from_dict(yaml.safe_load(_EXAMPLE_RUN))
    data = yaml.safe_load(_EXAMPLE_PROFILE)
    data.pop("space")          # its distance file is a placeholder path
    data.pop("entities")
    SeriesProfile.from_dict(data)


def test_yaml_bare_off_is_a_severity_not_a_boolean():
    """YAML 1.1 turns `off` into False. The docs use `discriminator: off`."""
    p = RunPolicy.from_dict(yaml.safe_load("checks:\n  discriminator: off\n  panel: on\n"))
    assert p.check("discriminator").severity == "off"
    assert not p.check("discriminator").enabled
    assert p.check("panel").severity == "hard"


def test_shipped_profiles_and_runs_all_load():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    profiles = sorted((root / "profiles").glob("*.yaml"))
    runs = sorted((root / "runs").glob("*.yaml"))
    assert profiles and runs
    for f in profiles:
        SeriesProfile.load(f)
    for f in runs:
        RunPolicy.load(f)


def test_travel_table_and_interstellar_profiles_use_the_same_checkers():
    """The point of the profile: the engine does not change between genres."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    interstellar = SeriesProfile.load(root / "profiles" / "bobiverse.yaml")
    court = SeriesProfile.load(root / "profiles" / "court-intrigue.yaml")
    assert interstellar.space.model == "interstellar"
    assert court.space.model == "travel_table"
    # Same question, both worlds, same call.
    raven = court.channel("raven")
    assert court.space.signal_days("Kings Landing", "Winterfell", raven.speed,
                                   table_factor=raven.table_factor) > 0
    radio = interstellar.channel("radio")
    assert interstellar.space.signal_days("Sol", "Epsilon Eridani", radio.speed) > 3000
