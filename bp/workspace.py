"""Where things live. One convention, so no command needs paths spelled out."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .db import Graph
from .errors import ProfileError
from .policy import RunPolicy
from .profile import SeriesProfile


@dataclass
class Workspace:
    root: Path

    @classmethod
    def find(cls, start: str | Path = ".") -> "Workspace":
        p = Path(start).resolve()
        for candidate in (p, *p.parents):
            if (candidate / "profiles").is_dir() or (candidate / "graph").is_dir():
                return cls(candidate)
        return cls(p)

    @property
    def profiles(self) -> Path:
        return self.root / "profiles"

    @property
    def runs(self) -> Path:
        return self.root / "runs"

    @property
    def graph_dir(self) -> Path:
        return self.root / "graph"

    @property
    def corpus(self) -> Path:
        return self.root / "corpus"

    @property
    def drafts(self) -> Path:
        return self.root / "drafts"

    @property
    def accepted(self) -> Path:
        return self.root / "accepted"

    @property
    def plan(self) -> Path:
        return self.root / "plan"

    @property
    def eval(self) -> Path:
        return self.root / "eval"

    def db_path(self, profile_name: str) -> Path:
        return self.graph_dir / f"{profile_name}.sqlite"

    def available_profiles(self) -> list[str]:
        return sorted(p.stem for p in self.profiles.glob("*.yaml")) if self.profiles.is_dir() else []

    def load_profile(self, name: str | None) -> SeriesProfile:
        """Load a profile by name or path, or pick the only one there is.

        ``name`` of None means the caller did not say. That is answerable when
        the workspace holds exactly one profile and genuinely ambiguous when it
        holds several — and guessing there is how a command ends up reporting
        confidently on the wrong series. Requiring the flag in the
        single-profile case would be friction protecting against nothing, so
        the rule keys on ambiguity rather than on the flag being absent.
        """
        available = self.available_profiles()
        if name is None:
            if len(available) == 1:
                return self.load_profile(available[0])
            raise ProfileError(
                f"--profile is required: this workspace has {len(available)} profiles "
                f"({', '.join(available) or 'none'}). Name the one you mean."
                if available else
                "no profiles in this workspace — see profiles/, or run `bp init`"
            )
        for candidate in (Path(name), self.profiles / name, self.profiles / f"{name}.yaml"):
            if candidate.is_file():
                return SeriesProfile.load(candidate)
        raise ProfileError(f"no profile {name!r}; available: {available or '(none — see profiles/)'}")

    def load_policy(self, name: str | None) -> RunPolicy:
        if not name:
            return RunPolicy.from_dict({})
        for candidate in (Path(name), self.runs / name, self.runs / f"{name}.yaml"):
            if candidate.is_file():
                return RunPolicy.load(candidate)
        return RunPolicy.load(name)

    def open_graph(self, profile: SeriesProfile, *, db: str | Path | None = None,
                   create: bool = False) -> Graph:
        path = Path(db) if db else self.db_path(profile.name)
        g = Graph(path, profile, create=create)
        # Channel availability resolved at ingest is stored in meta; restore it
        # so every later command reasons with the same channel dates.
        from .timeline import Span

        for ch in profile.channels:
            raw = g.get_meta(f"channel_from:{ch.name}")
            if raw and ch.available_from_date is None:
                ch.available_from_date = Span.at(float(raw))
        return g

    def scaffold(self) -> list[Path]:
        made = []
        for d in (self.profiles, self.runs, self.graph_dir, self.corpus, self.drafts,
                  self.accepted, self.plan, self.eval):
            if not d.exists():
                d.mkdir(parents=True)
                made.append(d)
        return made
