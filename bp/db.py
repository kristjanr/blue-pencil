"""The graph store: one SQLite file, FTS5 for retrieval, citations enforced.

SQLite rather than a service, for one reason that matters more than any
benchmark: a single file is trivially branchable with git. An alternate
continuation is a branch; a wrong turn in chapter thirty is a reset, not a
rewrite.

The write path enforces the invariant the whole design rests on: **a claim with
no citation does not get written**. Extraction can stage anything it likes; it
cannot commit a claim it cannot point at.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from .errors import UncitedClaim
from .models import (
    Citation,
    ChapterCard,
    Contradiction,
    Entity,
    Event,
    ObjectRecord,
    Promise,
    Relationship,
    TechniqueSpec,
    Thread,
)
from .profile import SeriesProfile
from .timeline import Span

SCHEMA_VERSION = 4

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS books (
    book_id     TEXT PRIMARY KEY,
    title       TEXT,
    ord         INTEGER,
    start_day   REAL,
    end_day     REAL,
    words       INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS scenes (
    scene_id    TEXT PRIMARY KEY,
    book_id     TEXT NOT NULL,
    chapter     INTEGER,
    scene       INTEGER,
    chapter_title TEXT DEFAULT '',
    pov         TEXT DEFAULT '',
    date_text   TEXT DEFAULT '',
    day_lo      REAL,
    day_hi      REAL,
    place       TEXT DEFAULT '',
    cast_json   TEXT DEFAULT '[]',
    text        TEXT NOT NULL,
    words       INTEGER DEFAULT 0,
    tokens      INTEGER DEFAULT 0,
    ord         INTEGER,
    generated   INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_scenes_pov  ON scenes(pov);
CREATE INDEX IF NOT EXISTS idx_scenes_ord  ON scenes(ord);
CREATE INDEX IF NOT EXISTS idx_scenes_book ON scenes(book_id);

CREATE VIRTUAL TABLE IF NOT EXISTS scenes_fts USING fts5(
    scene_id UNINDEXED, pov UNINDEXED, text, tokenize='porter unicode61'
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id    TEXT PRIMARY KEY,
    scene_id    TEXT NOT NULL REFERENCES scenes(scene_id) ON DELETE CASCADE,
    ord         INTEGER,
    text        TEXT NOT NULL,
    tokens      INTEGER,
    tags        TEXT DEFAULT '[]',
    vec         BLOB
);
CREATE INDEX IF NOT EXISTS idx_chunks_scene ON chunks(scene_id);

CREATE TABLE IF NOT EXISTS entities (
    entity_id   TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    kind        TEXT,
    aliases     TEXT DEFAULT '[]',
    description TEXT DEFAULT '',
    status      TEXT DEFAULT 'unknown',
    substrate   TEXT DEFAULT '',
    parent_id   TEXT DEFAULT '',
    forked_on   TEXT DEFAULT '',
    fork_day    REAL,
    claim_type  TEXT DEFAULT 'explicit',
    confidence  REAL DEFAULT 1.0
);

CREATE TABLE IF NOT EXISTS events (
    event_id    TEXT PRIMARY KEY,
    summary     TEXT NOT NULL,
    when_text   TEXT DEFAULT '',
    day_lo      REAL,
    day_hi      REAL,
    when_certainty TEXT DEFAULT 'inferred',
    where_place TEXT DEFAULT '',
    participants TEXT DEFAULT '[]',
    observed_by TEXT DEFAULT '[]',
    reader_state TEXT DEFAULT 'shown directly',
    significance REAL DEFAULT 0.5,
    claim_type  TEXT DEFAULT 'explicit',
    confidence  REAL DEFAULT 1.0,
    alternatives TEXT DEFAULT '[]',
    generated   INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_events_day ON events(day_lo);

CREATE TABLE IF NOT EXISTS reports (
    report_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
    sender      TEXT NOT NULL,
    recipient   TEXT NOT NULL,
    channel     TEXT DEFAULT 'unknown',
    departs_text TEXT DEFAULT '',
    depart_day  REAL,
    arrives_text TEXT DEFAULT '',
    arrive_day  REAL,
    computed    INTEGER DEFAULT 0,
    status      TEXT DEFAULT 'explicit',
    confidence  REAL DEFAULT 1.0,
    distortion  TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_reports_event ON reports(event_id);
CREATE INDEX IF NOT EXISTS idx_reports_to    ON reports(recipient);

CREATE TABLE IF NOT EXISTS beliefs (
    belief_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
    character   TEXT NOT NULL,
    state       TEXT NOT NULL,
    as_of_text  TEXT DEFAULT '',
    as_of_day   REAL,
    detail      TEXT DEFAULT '',
    confidence  REAL DEFAULT 1.0,
    -- The scene this belief was read out of. Without it two beliefs about one
    -- event are indistinguishable, and a disagreement between them cannot be
    -- told from an extraction slip inside a single scene.
    scene_id    TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_beliefs_char ON beliefs(character, event_id);

CREATE TABLE IF NOT EXISTS event_edges (
    src         TEXT NOT NULL,
    dst         TEXT NOT NULL,
    kind        TEXT NOT NULL,
    PRIMARY KEY (src, dst, kind)
);

CREATE TABLE IF NOT EXISTS consequences (
    consequence_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
    text        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS objects (
    object_id   TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    aliases     TEXT DEFAULT '[]',
    location    TEXT DEFAULT '',
    holder      TEXT DEFAULT '',
    as_of_text  TEXT DEFAULT '',
    as_of_day   REAL,
    significant INTEGER DEFAULT 0,
    state       TEXT DEFAULT '',
    claim_type  TEXT DEFAULT 'explicit',
    confidence  REAL DEFAULT 1.0
);

CREATE TABLE IF NOT EXISTS promises (
    promise_id  TEXT PRIMARY KEY,
    summary     TEXT NOT NULL,
    kind        TEXT DEFAULT 'setup',
    planted_in  TEXT DEFAULT '[]',
    owed_by     TEXT DEFAULT '[]',
    status      TEXT DEFAULT 'open',
    paid_in     TEXT DEFAULT '',
    weight      REAL DEFAULT 0.5,
    claim_type  TEXT DEFAULT 'explicit',
    confidence  REAL DEFAULT 1.0
);

CREATE TABLE IF NOT EXISTS threads (
    thread_id   TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    state       TEXT DEFAULT '',
    characters  TEXT DEFAULT '[]',
    last_scene  TEXT DEFAULT '',
    status      TEXT DEFAULT 'open',
    claim_type  TEXT DEFAULT 'explicit',
    confidence  REAL DEFAULT 1.0
);

CREATE TABLE IF NOT EXISTS relationships (
    rel_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,
    target      TEXT NOT NULL,
    kind        TEXT NOT NULL,
    sentiment   REAL DEFAULT 0.0,
    since_text  TEXT DEFAULT '',
    until_text  TEXT DEFAULT '',
    detail      TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS style (
    pov         TEXT PRIMARY KEY,
    spec_json   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS exemplars (
    exemplar_id INTEGER PRIMARY KEY AUTOINCREMENT,
    scene_id    TEXT NOT NULL,
    pov         TEXT,
    situation   TEXT DEFAULT '',
    techniques  TEXT DEFAULT '[]',
    excerpt     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_exemplar_pov ON exemplars(pov);

CREATE TABLE IF NOT EXISTS contradictions (
    contradiction_id TEXT PRIMARY KEY,
    subject     TEXT NOT NULL,
    reading_a   TEXT NOT NULL,
    reading_b   TEXT NOT NULL,
    cites_a     TEXT DEFAULT '[]',
    cites_b     TEXT DEFAULT '[]',
    note        TEXT DEFAULT '',
    resolved    INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS citations (
    citation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_kind TEXT NOT NULL,
    record_id   TEXT NOT NULL,
    scene_id    TEXT NOT NULL,
    quote       TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_cit_record ON citations(record_kind, record_id);

CREATE TABLE IF NOT EXISTS cards (
    card_id     TEXT PRIMARY KEY,
    book        TEXT,
    chapter     INTEGER,
    json        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS phrase_ledger (
    phrase      TEXT NOT NULL,
    scene_id    TEXT NOT NULL,
    ord         INTEGER,
    PRIMARY KEY (phrase, scene_id)
);

-- An entity merge deletes `old_id` after rewriting every in-graph reference to
-- `new_id`, which is right for the graph's own foreign keys but leaves nothing
-- for an OUTSIDE reference to redirect through -- a citation kept in someone's
-- notes, an eval fixture, a URL. This table is that redirect, kept forever.
CREATE TABLE IF NOT EXISTS entity_merges (
    old_id      TEXT PRIMARY KEY,
    new_id      TEXT NOT NULL,
    merged_at   TEXT DEFAULT (datetime('now'))
);
"""


def _j(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _unj(value: str | None, default: Any = None) -> Any:
    if not value:
        return default if default is not None else []
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default if default is not None else []


@dataclass
class SceneRow:
    scene_id: str
    book_id: str
    chapter: int
    scene: int
    pov: str
    date_text: str
    day_lo: float | None
    day_hi: float | None
    place: str
    cast: list[str]
    text: str
    words: int
    tokens: int
    ord: int
    generated: bool = False
    chapter_title: str = ""

    @property
    def span(self) -> Span | None:
        if self.day_lo is None or self.day_hi is None:
            return None
        return Span(self.day_lo, self.day_hi)


class Graph:
    """Handle on the story graph. Every write goes through here."""

    def __init__(self, path: str | Path, profile: SeriesProfile | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.profile = profile
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.set_meta("schema_version", str(SCHEMA_VERSION))
        if profile is not None and profile.source:
            self.set_meta("profile", str(profile.source))

    # ------------------------------------------------------------------ basics
    def close(self) -> None:
        self.conn.commit()
        self.conn.close()

    def __enter__(self) -> "Graph":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def commit(self) -> None:
        self.conn.commit()

    def _migrate(self) -> None:
        """Bring an older graph up to the current schema.

        `CREATE TABLE IF NOT EXISTS` never alters a table that already exists,
        so a graph built under an earlier version silently lacks new columns.
        Each step is idempotent and additive: no rebuild, no data loss.
        """
        have = {r[1] for r in self.conn.execute("PRAGMA table_info(beliefs)")}
        if have and "scene_id" not in have:
            self.conn.execute("ALTER TABLE beliefs ADD COLUMN scene_id TEXT DEFAULT ''")
            self.conn.commit()

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, value))

    def get_meta(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    # ------------------------------------------------------------------- dates
    def _span(self, text: str | None) -> Span | None:
        if not text or self.profile is None:
            return None
        try:
            return self.profile.calendar.parse(text)
        except Exception:
            return None

    # ------------------------------------------------------------- citations
    @staticmethod
    def _require(kind: str, record_id: str, citations: Sequence[Citation]) -> None:
        if not citations:
            raise UncitedClaim(
                f"{kind} {record_id!r} has no citation. Every claim in the graph must "
                f"point at the scene that supports it."
            )

    def _write_citations(self, kind: str, record_id: str, citations: Iterable[Citation]) -> None:
        self.conn.execute("DELETE FROM citations WHERE record_kind=? AND record_id=?", (kind, record_id))
        self.conn.executemany(
            "INSERT INTO citations(record_kind,record_id,scene_id,quote) VALUES(?,?,?,?)",
            [(kind, record_id, c.scene, c.quote) for c in citations],
        )

    def citations_for(self, kind: str, record_id: str) -> list[Citation]:
        rows = self.conn.execute(
            "SELECT scene_id, quote FROM citations WHERE record_kind=? AND record_id=?", (kind, record_id)
        ).fetchall()
        return [Citation(scene=r["scene_id"], quote=r["quote"]) for r in rows]

    # -------------------------------------------------------------- ingestion
    def add_book(self, book_id: str, title: str, ord_: int) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO books(book_id,title,ord) VALUES(?,?,?)", (book_id, title, ord_)
        )

    def add_scene(self, row: SceneRow) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO scenes
               (scene_id,book_id,chapter,scene,chapter_title,pov,date_text,day_lo,day_hi,place,
                cast_json,text,words,tokens,ord,generated)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (row.scene_id, row.book_id, row.chapter, row.scene, row.chapter_title, row.pov, row.date_text,
             row.day_lo, row.day_hi, row.place, _j(row.cast), row.text, row.words, row.tokens, row.ord,
             int(row.generated)),
        )
        self.conn.execute("DELETE FROM scenes_fts WHERE scene_id=?", (row.scene_id,))
        self.conn.execute(
            "INSERT INTO scenes_fts(scene_id,pov,text) VALUES(?,?,?)", (row.scene_id, row.pov, row.text)
        )

    def scene(self, scene_id: str) -> SceneRow | None:
        row = self.conn.execute("SELECT * FROM scenes WHERE scene_id=?", (scene_id,)).fetchone()
        return self._scene_row(row) if row else None

    def scenes(self, *, pov: str | None = None, book: str | None = None, generated: bool | None = None) -> list[SceneRow]:
        sql, args = "SELECT * FROM scenes WHERE 1=1", []
        if pov:
            sql, _ = sql + " AND pov=?", args.append(pov)
        if book:
            sql, _ = sql + " AND book_id=?", args.append(book)
        if generated is not None:
            sql, _ = sql + " AND generated=?", args.append(int(generated))
        sql += " ORDER BY ord"
        return [self._scene_row(r) for r in self.conn.execute(sql, args)]

    @staticmethod
    def _scene_row(r: sqlite3.Row) -> SceneRow:
        return SceneRow(
            scene_id=r["scene_id"], book_id=r["book_id"], chapter=r["chapter"], scene=r["scene"],
            pov=r["pov"], date_text=r["date_text"], day_lo=r["day_lo"], day_hi=r["day_hi"],
            place=r["place"], cast=_unj(r["cast_json"]), text=r["text"], words=r["words"],
            tokens=r["tokens"], ord=r["ord"], generated=bool(r["generated"]),
            chapter_title=r["chapter_title"] or "",
        )

    def book_start_days(self) -> dict[str, float]:
        rows = self.conn.execute(
            "SELECT book_id, MIN(day_lo) AS start FROM scenes WHERE day_lo IS NOT NULL GROUP BY book_id"
        ).fetchall()
        return {r["book_id"]: r["start"] for r in rows if r["start"] is not None}

    # ------------------------------------------------------------------ writes
    def write_entity(self, e: Entity) -> None:
        self._require("entity", e.entity_id, e.citations)
        self.conn.execute(
            """INSERT OR REPLACE INTO entities
               (entity_id,name,kind,aliases,description,status,substrate,parent_id,forked_on,fork_day,claim_type,confidence)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (e.entity_id, e.name, e.kind, _j(e.aliases), e.description, e.status, e.substrate,
             e.parent_id, e.forked_on, (self._span(e.forked_on).lo if self._span(e.forked_on) else None),
             e.claim_type, e.confidence),
        )
        self._write_citations("entity", e.entity_id, e.citations)

    def write_event(self, ev: Event, *, generated: bool = False) -> None:
        """Write an event, computing every report arrival that the text left open.

        The computation is the point. Extraction records *that* Milo's last
        transmission went to Bill by radio; this method works out *when* it
        landed, from the profile's distance table and channel speed. A model is
        never asked to do arithmetic it would only approximate.
        """
        self._require("event", ev.event_id, ev.citations)
        span = self._span(ev.when)
        self.conn.execute(
            """INSERT OR REPLACE INTO events
               (event_id,summary,when_text,day_lo,day_hi,when_certainty,where_place,participants,
                observed_by,reader_state,significance,claim_type,confidence,alternatives,generated)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ev.event_id, ev.summary, ev.when, span.lo if span else None, span.hi if span else None,
             ev.when_certainty, ev.where, _j(ev.participants), _j(ev.observed_by), ev.reader_state,
             ev.significance, ev.claim_type, ev.confidence, _j(ev.alternatives), int(generated)),
        )
        self._write_citations("event", ev.event_id, ev.citations)

        self.conn.execute("DELETE FROM reports WHERE event_id=?", (ev.event_id,))
        for rep in ev.reports:
            depart = self._span(rep.departs)
            arrive = self._span(rep.arrives)
            computed = 0
            if arrive is None:
                arrive, computed = self._compute_arrival(ev, rep, depart or span)
            self.conn.execute(
                """INSERT INTO reports
                   (event_id,sender,recipient,channel,departs_text,depart_day,arrives_text,arrive_day,
                    computed,status,confidence,distortion)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (ev.event_id, self._canon(rep.from_), self._canon(rep.to), rep.channel, rep.departs,
                 depart.lo if depart else None, rep.arrives, arrive.lo if arrive else None,
                 computed, rep.status, rep.confidence, rep.distortion),
            )

        self.conn.execute("DELETE FROM beliefs WHERE event_id=?", (ev.event_id,))
        for b in ev.beliefs:
            as_of = self._span(b.as_of)
            self.conn.execute(
                """INSERT INTO beliefs(event_id,character,state,as_of_text,as_of_day,detail,
                                         confidence,scene_id)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (ev.event_id, self._canon(b.character), b.state, b.as_of,
                 as_of.lo if as_of else None, b.detail, b.confidence,
                 ev.citations[0].scene if ev.citations else ""),
            )

        self.conn.execute("DELETE FROM consequences WHERE event_id=?", (ev.event_id,))
        self.conn.executemany(
            "INSERT INTO consequences(event_id,text) VALUES(?,?)", [(ev.event_id, c) for c in ev.consequences]
        )
        self.conn.executemany(
            "INSERT OR IGNORE INTO event_edges(src,dst,kind) VALUES(?,?,'causes')",
            [(cause, ev.event_id) for cause in ev.causes],
        )

    def _compute_arrival(self, ev: Event, rep, depart: Span | None) -> tuple[Span | None, int]:
        """distance / speed — the arithmetic the plan says replaces a judgment call."""
        if self.profile is None or depart is None:
            return None, 0
        channel = self.profile.channel(rep.channel)
        if channel is None:
            channel = self.profile.fastest_channel(depart.lo)
        if channel is None:
            return None, 0
        sender_place = ev.where or self._place_of(rep.from_, depart.lo)
        recipient_place = self._place_of(rep.to, depart.lo)
        if not sender_place or not recipient_place:
            return None, 0
        if not self.profile.space.known(sender_place, recipient_place):
            return None, 0
        if not channel.available_at(depart.lo):
            live = self.profile.fastest_channel(depart.lo)
            if live is None:
                return None, 0
            channel = live
        days = self.profile.space.signal_days(sender_place, recipient_place, channel.speed,
                                              table_factor=channel.table_factor)
        return depart.shifted(days), 1

    def _place_of(self, character: str, day: float) -> str:
        """Best-known location of a character on a day.

        Scenes place a character more reliably than events do — a scene names
        its POV and its setting — so they are consulted first, then events, then
        the character's earliest recorded placement. This is deliberately the
        same resolution order :meth:`bp.knowledge.KnowledgeGraph.location_of`
        uses: the arrival stored here and the arrival the checker computes must
        never be able to disagree.
        """
        canon = self._canon(character)
        row = self.conn.execute(
            """SELECT place FROM scenes
               WHERE day_lo IS NOT NULL AND day_lo <= ? AND place != ''
                 AND (pov = ? COLLATE NOCASE OR cast_json LIKE ?)
               ORDER BY day_lo DESC LIMIT 1""",
            (day, canon, f'%"{canon}"%'),
        ).fetchone()
        if row:
            return row["place"]
        row = self.conn.execute(
            """SELECT where_place FROM events
               WHERE day_lo IS NOT NULL AND day_lo <= ?
                 AND (participants LIKE ? OR observed_by LIKE ?)
                 AND where_place != ''
               ORDER BY day_lo DESC LIMIT 1""",
            (day, f'%"{canon}"%', f'%"{canon}"%'),
        ).fetchone()
        if row:
            return row["where_place"]
        # Over a multi-year transit the recipient's position at the moment of
        # departure is not knowable from the text either; the nearest placement
        # the graph can cite is the honest approximation, and returning nothing
        # would silently disable the computation.
        row = self.conn.execute(
            """SELECT place FROM scenes WHERE place != ''
                 AND (pov = ? COLLATE NOCASE OR cast_json LIKE ?)
               ORDER BY day_lo IS NULL, day_lo LIMIT 1""",
            (canon, f'%"{canon}"%'),
        ).fetchone()
        return row["place"] if row else ""

    def _canon(self, name: str) -> str:
        return self.profile.canonical(name) if self.profile else name.strip()

    def write_object(self, o: ObjectRecord) -> None:
        self._require("object", o.object_id, o.citations)
        as_of = self._span(o.as_of)
        self.conn.execute(
            """INSERT OR REPLACE INTO objects
               (object_id,name,aliases,location,holder,as_of_text,as_of_day,significant,state,claim_type,confidence)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (o.object_id, o.name, _j(o.aliases), o.location, self._canon(o.holder), o.as_of,
             as_of.lo if as_of else None, int(o.significant), o.state, o.claim_type, o.confidence),
        )
        self._write_citations("object", o.object_id, o.citations)

    def write_promise(self, p: Promise) -> None:
        self._require("promise", p.promise_id, p.citations)
        self.conn.execute(
            """INSERT OR REPLACE INTO promises
               (promise_id,summary,kind,planted_in,owed_by,status,paid_in,weight,claim_type,confidence)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (p.promise_id, p.summary, p.kind, _j(p.planted_in), _j(p.owed_by), p.status, p.paid_in,
             p.weight, p.claim_type, p.confidence),
        )
        self._write_citations("promise", p.promise_id, p.citations)

    def write_thread(self, t: Thread) -> None:
        self._require("thread", t.thread_id, t.citations)
        self.conn.execute(
            """INSERT OR REPLACE INTO threads
               (thread_id,name,state,characters,last_scene,status,claim_type,confidence)
               VALUES(?,?,?,?,?,?,?,?)""",
            (t.thread_id, t.name, t.state, _j(t.characters), t.last_scene, t.status, t.claim_type, t.confidence),
        )
        self._write_citations("thread", t.thread_id, t.citations)

    def write_relationship(self, r: Relationship) -> None:
        rid = f"{r.source}->{r.target}:{r.kind}"
        self._require("relationship", rid, r.citations)
        self.conn.execute(
            """INSERT INTO relationships(source,target,kind,sentiment,since_text,until_text,detail)
               VALUES(?,?,?,?,?,?,?)""",
            (self._canon(r.source), self._canon(r.target), r.kind, r.sentiment, r.since, r.until, r.detail),
        )
        self._write_citations("relationship", rid, r.citations)

    def write_contradiction(self, c: Contradiction) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO contradictions
               (contradiction_id,subject,reading_a,reading_b,cites_a,cites_b,note,resolved)
               VALUES(?,?,?,?,?,?,?,?)""",
            (c.contradiction_id, c.subject, c.reading_a, c.reading_b,
             _j([x.model_dump() for x in c.citations_a]), _j([x.model_dump() for x in c.citations_b]),
             c.note, int(c.resolved)),
        )

    def write_style(self, spec: TechniqueSpec) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO style(pov,spec_json) VALUES(?,?)",
            (spec.pov, spec.model_dump_json()),
        )

    def style(self, pov: str) -> TechniqueSpec | None:
        row = self.conn.execute("SELECT spec_json FROM style WHERE pov=?", (pov,)).fetchone()
        return TechniqueSpec.model_validate_json(row["spec_json"]) if row else None

    def write_card(self, card: ChapterCard) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO cards(card_id,book,chapter,json) VALUES(?,?,?,?)",
            (card.card_id, card.book, card.chapter, card.model_dump_json()),
        )

    def card(self, card_id: str) -> ChapterCard | None:
        row = self.conn.execute("SELECT json FROM cards WHERE card_id=?", (card_id,)).fetchone()
        return ChapterCard.model_validate_json(row["json"]) if row else None

    # ------------------------------------------------------------------- reads
    def event(self, event_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()

    def events(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM events ORDER BY day_lo IS NULL, day_lo"))

    def objects(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM objects"))

    def entities(self, kind: str | None = None) -> list[sqlite3.Row]:
        if kind:
            return list(self.conn.execute("SELECT * FROM entities WHERE kind=?", (kind,)))
        return list(self.conn.execute("SELECT * FROM entities"))

    def entity(self, name: str) -> sqlite3.Row | None:
        canon = self._canon(name)
        row = self.conn.execute(
            "SELECT * FROM entities WHERE entity_id=? OR name=? COLLATE NOCASE", (canon, canon)
        ).fetchone()
        if row is not None:
            return row
        resolved = self.resolve_entity_id(canon)
        if resolved == canon:
            return None
        return self.conn.execute("SELECT * FROM entities WHERE entity_id=?", (resolved,)).fetchone()

    def resolve_entity_id(self, entity_id: str) -> str:
        """Follow a chain of entity merges to the id that is still live.

        A merge deletes the loser's row after rewriting the graph's own
        references (citations, participants, holders, ...), so nothing
        inside the graph needs the old id any more. Outside the graph is a
        different story -- a citation kept in someone's notes, an eval
        fixture, a URL -- so every merge is recorded here as a permanent
        redirect. Returns ``entity_id`` unchanged if it was never merged
        (including if it never existed).
        """
        seen = {entity_id}
        current = entity_id
        while True:
            row = self.conn.execute(
                "SELECT new_id FROM entity_merges WHERE old_id=?", (current,)).fetchone()
            if row is None or row["new_id"] in seen:
                return current
            current = row["new_id"]
            seen.add(current)

    def promises(self, status: str | None = None) -> list[sqlite3.Row]:
        if status:
            return list(self.conn.execute("SELECT * FROM promises WHERE status=?", (status,)))
        return list(self.conn.execute("SELECT * FROM promises"))

    def threads(self, status: str | None = None) -> list[sqlite3.Row]:
        if status:
            return list(self.conn.execute("SELECT * FROM threads WHERE status=?", (status,)))
        return list(self.conn.execute("SELECT * FROM threads"))

    def contradictions(self, *, unresolved_only: bool = False) -> list[sqlite3.Row]:
        sql = "SELECT * FROM contradictions"
        if unresolved_only:
            sql += " WHERE resolved=0"
        return list(self.conn.execute(sql))

    def counts(self) -> dict[str, int]:
        tables = ["books", "scenes", "chunks", "entities", "events", "reports", "beliefs", "objects",
                  "promises", "threads", "relationships", "contradictions", "citations", "style", "exemplars"]
        out = {}
        for t in tables:
            out[t] = self.conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
        return out
