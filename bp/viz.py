"""A local, read-only window on the graph — the half a static export cannot be.

`bp export` serves the shareable views: entities, derived edges, promises,
beliefs. Two things are deliberately missing from it and cannot be added by
drawing harder.

**The information path.** `reports` is not exported, and even if it were, the
question a reader actually has — *could this character know this yet?* — is not
a lookup. It is light-lag arithmetic over the series' space model, channel
availability windows that open partway through the series, and belief inherited
across a clone fork. That logic lives in :class:`bp.knowledge.KnowledgeGraph`.
Re-implementing it in JavaScript would mean two copies of the epistemic rules
that can silently disagree, which is the drift the checkers exist to catch. So
this serves the real function and draws its answer.

**Provenance.** Citations point at scenes, and the audit question is whether the
quoted line supports the claim. That needs the quote and the scene prose, which
the export excludes on purpose so it can be handed to a service the corpus must
never reach. Here the corpus is already local, so the check is a click.

The database is opened through a `mode=ro` URI. Not "we are careful not to
write" — the connection cannot. `Graph.__init__` runs `executescript(SCHEMA)`
and stamps `schema_version`, taking a write lock merely to look, so this uses a
read-only shim exposing the five members `KnowledgeGraph` actually touches
(`profile`, `conn`, `event`, `events`, `entities`). A viewer that can corrupt
the thing it views is a viewer you have to run `backup.sh` before.
"""

from __future__ import annotations

import json
import sqlite3
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .profile import SeriesProfile

PAGE = Path(__file__).resolve().parent.parent / "viz" / "path.html"


class ReadOnlyGraph:
    """Everything `KnowledgeGraph` needs, over a connection that cannot write."""

    def __init__(self, path: str | Path, profile: SeriesProfile | None = None):
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(f"no graph at {self.path}")
        # immutable=0: a concurrent extraction may be mid-write, and WAL lets a
        # reader through. mode=ro is the guarantee; nothing here can take a
        # write lock, so this is safe to leave open during a run.
        self.conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.profile = profile
        self._restore_channel_dates()

    def get_meta(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def _restore_channel_dates(self) -> None:
        """Re-apply the channel availability that ingest resolved into `meta`.

        `Workspace.open_graph` does this for every other command, and skipping it
        here would not fail loudly — it would answer epistemic questions with an
        instant relay available from book one, which is precisely the case the
        profile exists to stop a naive checker getting confidently wrong. A
        viewer that quietly disagrees with `bp check` is worse than no viewer.
        """
        if self.profile is None:
            return
        from .timeline import Span

        for ch in self.profile.channels:
            raw = self.get_meta(f"channel_from:{ch.name}")
            if raw and ch.available_from_date is None:
                ch.available_from_date = Span.at(float(raw))

    def event(self, event_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()

    def events(self) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM events ORDER BY day_lo IS NULL, day_lo"))

    def entities(self, kind: str | None = None) -> list[sqlite3.Row]:
        if kind:
            return list(self.conn.execute("SELECT * FROM entities WHERE kind=?", (kind,)))
        return list(self.conn.execute("SELECT * FROM entities"))

    def close(self) -> None:
        self.conn.close()


def _rows(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, args)]


# --------------------------------------------------------------------- queries

def summary(g: ReadOnlyGraph) -> dict:
    n = lambda t: g.conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
    return {
        "database": str(g.path),
        "profile": g.profile.name if g.profile else "",
        "counts": {t: n(t) for t in
                   ("books", "scenes", "entities", "events", "reports", "beliefs",
                    "promises", "threads", "objects", "citations", "contradictions")},
        # The number that says whether the information path is answerable at all.
        "reports_with_arrival": g.conn.execute(
            "SELECT COUNT(*) c FROM reports WHERE arrive_day IS NOT NULL").fetchone()["c"],
    }


def path_of(g: ReadOnlyGraph, event_id: str, character: str) -> dict:
    """The information path, rendered from the real epistemic engine."""
    from .knowledge import KnowledgeGraph

    kg = KnowledgeGraph(g)
    k = kg.earliest_knowledge(event_id, character)
    cal = g.profile.calendar if g.profile else None
    fmt = (lambda d: cal.format(d)) if cal else (lambda d: f"day {d:.0f}")
    ev = g.event(event_id)
    return {
        "character": k.character,
        "event_id": k.event_id,
        "event_summary": ev["summary"] if ev else "",
        "event_day": ev["day_lo"] if ev else None,
        "event_day_hi": ev["day_hi"] if ev else None,
        "event_where": ev["where_place"] if ev else "",
        "day": k.day,
        "day_text": fmt(k.day) if k.day is not None else None,
        "reachable": k.reachable,
        "indeterminate": k.indeterminate,
        "reason": k.reason,
        "explain": kg.explain(k),
        "hops": [{
            "kind": h.kind, "sender": h.sender, "recipient": h.recipient,
            "channel": h.channel, "depart_day": h.depart_day, "arrive_day": h.arrive_day,
            "arrive_text": fmt(h.arrive_day) if h.arrive_day is not None else None,
            # The distinction that makes the checker deterministic: an arrival
            # computed from the space model is a different claim from one the
            # text stated, and must never be drawn the same.
            "computed": bool(h.computed), "note": h.note,
        } for h in k.path],
    }


def who_knows(g: ReadOnlyGraph, event_id: str, day: float) -> list[dict]:
    from .knowledge import KnowledgeGraph

    kg = KnowledgeGraph(g)
    return [{"character": c, "day": d} for c, d in kg.who_could_know(event_id, day)]


def event_detail(g: ReadOnlyGraph, event_id: str) -> dict:
    ev = g.event(event_id)
    if ev is None:
        return {"error": "no such event"}
    d = dict(ev)
    for f in ("participants", "observed_by", "alternatives"):
        try:
            d[f] = json.loads(d.get(f) or "[]")
        except (TypeError, ValueError):
            d[f] = []
    d["reports"] = _rows(g.conn, "SELECT * FROM reports WHERE event_id=? ORDER BY arrive_day", (event_id,))
    d["beliefs"] = _rows(g.conn, "SELECT * FROM beliefs WHERE event_id=?", (event_id,))
    d["citations"] = citations_for(g, "event", event_id)
    return d


def citations_for(g: ReadOnlyGraph, kind: str, record_id: str) -> list[dict]:
    """Citations with the quote and the scene around it — the audit view.

    This is the half of the record check the export cannot carry: whether the
    line actually supports the claim.
    """
    out = []
    for c in g.conn.execute(
            "SELECT * FROM citations WHERE record_kind=? AND record_id=?", (kind, record_id)):
        scene = g.conn.execute(
            "SELECT scene_id, book_id, chapter, scene, chapter_title, pov, date_text, place, text"
            "  FROM scenes WHERE scene_id=?", (c["scene_id"],)).fetchone()
        quote = c["quote"] or ""
        found = bool(quote) and scene is not None and quote in scene["text"]
        out.append({
            "scene_id": c["scene_id"],
            "quote": quote,
            # Whether the quote is literally in the scene it names. `bp ground`
            # answers this across the graph; here it is answered per citation,
            # because a record that cannot prove itself is the finding.
            "quote_found": found,
            "scene": {k: scene[k] for k in
                      ("book_id", "chapter", "scene", "chapter_title", "pov", "date_text", "place")}
                     if scene else None,
            "excerpt": _excerpt(scene["text"], quote) if scene else "",
        })
    return out


def _excerpt(text: str, quote: str, window: int = 420) -> str:
    if quote and quote in text:
        i = text.index(quote)
        return text[max(0, i - window): i + len(quote) + window]
    return text[:window * 2]


def search(g: ReadOnlyGraph, q: str, limit: int = 25) -> dict:
    like = f"%{q}%"
    return {
        "entities": _rows(g.conn,
            "SELECT entity_id, name, kind, status FROM entities"
            " WHERE name LIKE ? OR entity_id LIKE ? OR aliases LIKE ? LIMIT ?",
            (like, like, like, limit)),
        "events": _rows(g.conn,
            "SELECT event_id, summary, day_lo, day_hi, where_place FROM events"
            " WHERE summary LIKE ? OR event_id LIKE ? ORDER BY day_lo IS NULL, day_lo LIMIT ?",
            (like, like, limit)),
    }


# ---------------------------------------------------------------------- server

class Handler(BaseHTTPRequestHandler):
    graph: ReadOnlyGraph = None  # set per-server below

    def log_message(self, *a): pass  # the terminal is for the user, not the access log

    def _send(self, body: bytes, ctype: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: Any, code: int = 200) -> None:
        self._send(json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"),
                   "application/json; charset=utf-8", code)

    def do_GET(self) -> None:
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        g = self.graph
        try:
            if u.path in ("/", "/index.html"):
                if PAGE.exists():
                    self._send(PAGE.read_bytes(), "text/html; charset=utf-8")
                else:
                    self._send(b"viz/path.html is missing", "text/plain; charset=utf-8", 404)
            elif u.path == "/api/summary":
                self._json(summary(g))
            elif u.path == "/api/search":
                self._json(search(g, q.get("q", "")))
            elif u.path == "/api/path":
                if not q.get("event") or not q.get("character"):
                    self._json({"error": "need ?event= and ?character="}, 400); return
                self._json(path_of(g, q["event"], q["character"]))
            elif u.path == "/api/who-knows":
                if not q.get("event"):
                    self._json({"error": "need ?event="}, 400); return
                day = float(q["day"]) if q.get("day") else None
                if day is None:
                    ev = g.event(q["event"])
                    day = (ev["day_hi"] if ev and ev["day_hi"] is not None else 1e9)
                self._json(who_knows(g, q["event"], day))
            elif u.path == "/api/event":
                self._json(event_detail(g, q.get("id", "")))
            elif u.path == "/api/citations":
                self._json(citations_for(g, q.get("kind", "event"), q.get("id", "")))
            else:
                self._json({"error": "no such endpoint"}, 404)
        except sqlite3.OperationalError as e:  # the read-only guarantee, made visible
            self._json({"error": f"database refused the query: {e}"}, 500)
        except Exception as e:
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)


def serve(db: str | Path, profile: SeriesProfile | None = None,
          *, port: int = 8900, host: str = "127.0.0.1") -> None:
    """Serve until interrupted. Binds loopback only — this is a local instrument."""
    g = ReadOnlyGraph(db, profile)
    handler = type("BoundHandler", (Handler,), {"graph": g})
    srv = ThreadingHTTPServer((host, port), handler)
    print(f"  read-only on {g.path}")
    print(f"  http://{host}:{port}  —  ctrl-c to stop")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped")
    finally:
        srv.server_close()
        g.close()
