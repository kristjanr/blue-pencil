"""The run policy: everything about *how* a run behaves.

The design note behind this file: nearly everything an earlier draft of the plan
called "a decision to make before building" is really a setting. How much the
human judges, how many candidates per scene, which model runs which stage, how
big a context pack gets, how severe each checker is — all of it can change
between runs and, for gates, mid-book. An editor should not have to decide their
working style before the tool exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from .errors import PolicyError

Severity = Literal["hard", "soft", "floor", "off"]
GateMode = Literal["manual", "auto_if_clean", "auto"]

#: Model defaults per stage. Judgment-heaviest work gets the strongest model;
#: volume work with strict schemas gets a cheaper one and runs through batch.
DEFAULT_MODELS = {
    "plan": "claude-fable-5-1",
    "draft": "claude-opus-5",
    "extract": "claude-sonnet-5",
    "checks": "claude-haiku-4-5-20251001",
    "judge": "claude-sonnet-5",
    "probe": "claude-opus-5",
}

DEFAULT_CHECKS: dict[str, Any] = {
    "epistemic": "hard",
    "geography": "hard",
    "objects": "hard",
    "card": "hard",
    "repetition": {"severity": "soft", "budget": "corpus_baseline"},
    "voice": "soft",
    "consequence": "soft",
    "discriminator": "floor",
    "panel": "soft",
}


def _severity(value: object) -> str:
    """Normalise a severity written in YAML.

    YAML 1.1 turns a bare ``off`` into the boolean ``False`` (and ``on`` into
    ``True``), so ``discriminator: off`` — which this project's own docs use —
    would otherwise fail with a baffling message about the severity 'False'.
    """
    if value is False:
        return "off"
    if value is True:
        return "hard"
    return str(value).strip().lower()


@dataclass
class Gate:
    """When a stage stops for a human."""

    mode: GateMode = "manual"
    every_n: int | None = None
    _seen: int = 0

    @classmethod
    def parse(cls, spec: Any) -> "Gate":
        if isinstance(spec, dict):
            if "every_n" in spec:
                return cls(mode="manual", every_n=int(spec["every_n"]))
            spec = spec.get("mode", "manual")
        spec = str(spec)
        if spec.startswith("every_n"):
            return cls(mode="manual", every_n=int(spec.split(":")[-1]))
        if spec not in ("manual", "auto_if_clean", "auto"):
            raise PolicyError(f"unknown gate mode {spec!r}")
        return cls(mode=spec)  # type: ignore[arg-type]

    def stops(self, *, clean: bool) -> bool:
        """Whether this gate stops for a human on this occasion."""
        self._seen += 1
        if self.every_n:
            return self._seen % self.every_n == 0
        if self.mode == "manual":
            return True
        if self.mode == "auto":
            return False
        return not clean  # auto_if_clean


@dataclass
class CheckConfig:
    severity: Severity = "soft"
    options: dict[str, Any] = field(default_factory=dict)

    @property
    def enabled(self) -> bool:
        return self.severity != "off"


@dataclass
class RunPolicy:
    name: str = "default"
    gates: dict[str, Gate] = field(default_factory=dict)
    candidates_per_scene: int = 3
    revision_rounds: int = 2
    context_start_tokens: int = 30_000
    context_max_tokens: int = 50_000
    models: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_MODELS))
    checks: dict[str, CheckConfig] = field(default_factory=dict)
    max_usd: float | None = None
    thinking_effort: str = "high"
    seed_notes: str = ""
    source: Path | None = None

    @classmethod
    def load(cls, path: str | Path | None) -> "RunPolicy":
        if path is None:
            return cls.from_dict({})
        path = Path(path)
        if not path.exists():
            raise PolicyError(f"run policy not found: {path}")
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls.from_dict(data, source=path)

    @classmethod
    def from_dict(cls, data: dict, *, source: Path | None = None) -> "RunPolicy":
        gates = {k: Gate.parse(v) for k, v in (data.get("gates") or {}).items()}
        for stage, default in (("plan", "manual"), ("chapter", "manual"), ("act", "manual")):
            gates.setdefault(stage, Gate.parse(default))

        candidates = data.get("candidates") or {}
        context = data.get("context") or {}
        models = dict(DEFAULT_MODELS) | {k: str(v) for k, v in (data.get("models") or {}).items()}

        checks: dict[str, CheckConfig] = {}
        merged = dict(DEFAULT_CHECKS) | dict(data.get("checks") or {})
        for name, spec in merged.items():
            if isinstance(spec, dict):
                sev = _severity(spec.get("severity", "soft"))
                opts = {k: v for k, v in spec.items() if k != "severity"}
            else:
                sev, opts = _severity(spec), {}
            if sev not in ("hard", "soft", "floor", "off"):
                raise PolicyError(f"check {name!r} has unknown severity {sev!r}")
            checks[name] = CheckConfig(severity=sev, options=opts)  # type: ignore[arg-type]

        start = int(context.get("start_tokens", 30_000))
        maximum = int(context.get("max_tokens", 50_000))
        if maximum < start:
            raise PolicyError("context.max_tokens must be >= context.start_tokens")

        return cls(
            name=str(data.get("name") or (source.stem if source else "default")),
            gates=gates,
            candidates_per_scene=int(candidates.get("per_scene", 3)),
            revision_rounds=int(candidates.get("revision_rounds", 2)),
            context_start_tokens=start,
            context_max_tokens=maximum,
            models=models,
            checks=checks,
            max_usd=(float(data["budget"]["max_usd"]) if (data.get("budget") or {}).get("max_usd") else None),
            thinking_effort=str((data.get("models") or {}).get("thinking_effort", "high")),
            seed_notes=str(data.get("notes", "")),
            source=source,
        )

    # --------------------------------------------------------------- accessors
    def gate(self, name: str) -> Gate:
        return self.gates.get(name, Gate(mode="manual"))

    def check(self, name: str) -> CheckConfig:
        return self.checks.get(name, CheckConfig())

    def model_for(self, stage: str) -> str:
        return self.models.get(stage, DEFAULT_MODELS.get(stage, DEFAULT_MODELS["draft"]))
