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
    _bp("init", str(tmp_path), cwd=tmp_path)
    out = _bp("extract", cwd=tmp_path, env=_env(ANTHROPIC_API_KEY=None))
    assert out.returncode == 3, out.stderr
    assert "ANTHROPIC_API_KEY" in out.stderr
