"""The review surface and the command line."""

import json
import subprocess
import sys
from pathlib import Path

from bp.checks import CheckContext, run_checks
from bp.draftdoc import Draft
from bp.policy import RunPolicy
from bp.review import render_html

CHAPTERS = Path(__file__).parent / "fixtures" / "chapters"


def test_review_page_puts_hard_findings_first(world):
    graph, profile = world
    draft = Draft.load(CHAPTERS / "planted.md")
    sc = run_checks(CheckContext(draft=draft, graph=graph, profile=profile,
                                 policy=RunPolicy.from_dict({})))
    html = render_html(draft, sc)
    assert html.index('class="mark hard"') < html.index('class="mark soft"')
    assert "flag-hard" in html                    # the offending line is marked
    assert f"{len(sc.hard)}</b> hard" in html     # the tally is visible before scrolling


def test_review_page_is_theme_aware_and_escapes_content(world):
    graph, profile = world
    draft = Draft.parse("A line with <script>alert(1)</script> in it.")
    draft.meta = {"pov": "Ana", "date": "2185-01-01", "place": "Sol"}
    html = render_html(draft, run_checks(CheckContext(
        draft=draft, graph=graph, profile=profile, policy=RunPolicy.from_dict({}))))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "prefers-color-scheme:dark" in html


def test_serve_mode_renders_a_note_box_but_a_static_page_does_not(world):
    """The static `--html` page has nowhere to send a POST, so the note box —
    and the accept/revise/reject buttons — only belong on `--serve`."""
    graph, profile = world
    draft = Draft.load(CHAPTERS / "planted.md")
    sc = run_checks(CheckContext(draft=draft, graph=graph, profile=profile,
                                 policy=RunPolicy.from_dict({})))
    served = render_html(draft, sc, serve=True)
    static = render_html(draft, sc, serve=False)
    assert 'id="note"' in served and "revise (v)" in served
    assert 'id="note"' not in static and "revise (v)" not in static


def test_front_matter_is_excluded_from_the_prose_but_counted_in_line_numbers():
    draft = Draft.load(CHAPTERS / "clean.md")
    assert draft.pov == "Ana" and draft.place == "Sol"
    assert not draft.text.lstrip().startswith("---")
    assert draft.body_offset > 0
    assert draft.line_of(0) == draft.body_offset + 1


REPO = Path(__file__).resolve().parent.parent


def _env(**overrides):
    import os

    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    for k, v in overrides.items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = v
    return env


def _bp(*args, cwd, env=None):
    return subprocess.run([sys.executable, "-m", "bp.cli", *args], cwd=cwd,
                          capture_output=True, text=True, timeout=180, env=env or _env())


def test_cli_check_reports_json_and_exit_codes(tmp_path):
    import synthetic

    root = Path(__file__).parent.parent
    (tmp_path / "profiles").mkdir()
    (tmp_path / "graph").mkdir()

    # a workspace with the synthetic world in it
    profile_yaml = tmp_path / "profiles" / "longwater.yaml"
    import yaml

    spec = dict(synthetic.PROFILE_DICT)
    spec["space"] = {"model": "interstellar", "travel_speed": "2c", "distances": "distances.csv"}
    profile_yaml.write_text(yaml.safe_dump(spec))
    rows = ["from,to,light_years"] + [f"{a},{b},{ly}" for (a, b), ly in synthetic.DISTANCES.items()]
    (tmp_path / "profiles" / "distances.csv").write_text("\n".join(rows))
    synthetic.build(tmp_path / "graph" / "longwater.sqlite")[0].close()

    out = _bp("check", str(CHAPTERS / "clean.md"), "--profile", "longwater", "--json", cwd=tmp_path)
    assert out.returncode == 0, out.stderr
    payload = json.loads(out.stdout)
    assert payload["clean"] and payload["hard"] == 0

    out = _bp("check", str(CHAPTERS / "planted.md"), "--profile", "longwater",
              "--card", str(CHAPTERS / "card.json"), "--json", cwd=tmp_path)
    assert out.returncode == 2                     # dirty chapters exit non-zero
    payload = json.loads(out.stdout)
    assert payload["hard"] >= 10
    assert {m["check"] for m in payload["marginalia"]} >= {"epistemic", "geography", "objects", "card"}


def test_cli_init_scaffolds_a_workspace(tmp_path):
    out = _bp("init", str(tmp_path), cwd=tmp_path)
    assert out.returncode == 0
    for d in ("profiles", "runs", "graph", "corpus", "drafts", "accepted", "plan", "eval"):
        assert (tmp_path / d).is_dir()
    assert (tmp_path / "profiles" / "example.yaml").exists()


def test_cli_reports_missing_credentials_clearly(tmp_path):
    """A model-backed stage with no key must fail clearly, not degrade into
    something that looks like it worked."""
    import synthetic

    _bp("init", str(tmp_path), cwd=tmp_path)
    # The graph has to exist for the run to reach the credential check at all —
    # a missing one is now its own error, deliberately.
    synthetic.build(tmp_path / "graph" / "example.sqlite")[0].close()
    out = _bp("extract", cwd=tmp_path, env=_env(ANTHROPIC_API_KEY=None))
    assert out.returncode == 3, out.stderr
    assert "ANTHROPIC_API_KEY" in out.stderr


def test_cli_refuses_to_invent_a_graph_that_does_not_exist(tmp_path):
    """The bug this test exists for: Graph() used to create a missing database,
    so a mistyped --profile analysed a brand-new empty graph and reported a
    clean bill of health. Every command but init and ingest must refuse, and
    name the path it looked for."""
    _bp("init", str(tmp_path), cwd=tmp_path)
    out = _bp("ground", cwd=tmp_path)
    assert out.returncode != 0
    assert "no graph at" in out.stderr
    assert not (tmp_path / "graph" / "example.sqlite").exists(), "and it must not have made one"


def test_cli_requires_a_profile_when_the_workspace_has_several(tmp_path):
    """Defaulting is only safe when there is nothing to choose between."""
    import yaml

    import synthetic

    _bp("init", str(tmp_path), cwd=tmp_path)
    (tmp_path / "profiles" / "second.yaml").write_text(
        yaml.safe_dump(dict(synthetic.PROFILE_DICT)), encoding="utf-8")

    out = _bp("ground", cwd=tmp_path)
    assert out.returncode != 0
    assert "--profile is required" in out.stderr
    assert "second" in out.stderr and "example" in out.stderr
