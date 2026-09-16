"""``bp`` — the command line. Boring on purpose.

No web app until the CLI proves the loop. Every command works on a workspace
laid out by ``bp init``, and every command that needs a model says so and fails
clearly without one, rather than degrading into something that looks like it
worked.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .errors import BluePencilError, NoCredentials
from .workspace import Workspace


def _client_or_none(want_llm: bool):
    if not want_llm:
        return None
    from .llm import make_client

    return make_client()


def _echo(msg: str = "") -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------------- init
def cmd_init(args) -> int:
    ws = Workspace(Path(args.directory).resolve())
    made = ws.scaffold()
    for d in made:
        _echo(f"created {d.relative_to(ws.root)}/")
    example = ws.profiles / "example.yaml"
    if not example.exists():
        example.write_text(_EXAMPLE_PROFILE, encoding="utf-8")
        _echo(f"created {example.relative_to(ws.root)}")
    run = ws.runs / "default.yaml"
    if not run.exists():
        run.write_text(_EXAMPLE_RUN, encoding="utf-8")
        _echo(f"created {run.relative_to(ws.root)}")
    # The example profile references these; a scaffold that cannot be loaded is
    # a paper cut on the very first command anyone runs.
    data = ws.profiles / "data"
    data.mkdir(exist_ok=True)
    for name, body in (("distances.csv", _EXAMPLE_DISTANCES), ("aliases.yaml", _EXAMPLE_ALIASES)):
        f = data / name
        if not f.exists():
            f.write_text(body, encoding="utf-8")
            _echo(f"created {f.relative_to(ws.root)}")
    _echo("\nnext: put EPUBs in corpus/, edit a profile, then `bp ingest`")
    return 0


# -------------------------------------------------------------------- profile
def cmd_profile(args) -> int:
    ws = Workspace.find()
    profile = ws.load_profile(args.profile)
    _echo("\n".join(profile.evidence_report()))
    if args.check:
        graph = ws.open_graph(profile)
        starts = graph.book_start_days()
        profile.resolve_channel_availability(starts)
        _echo("")
        for ch in profile.channels:
            when = ch.available_from_date
            _echo(f"channel {ch.name}: available from "
                  + (profile.calendar.format(when.lo) if when else "the beginning"))
        unknown = [p for p in profile.space.places if p not in profile.space.places]
        if unknown:
            _echo(f"places with no distances: {unknown}")
    return 0


# --------------------------------------------------------------------- ingest
def cmd_ingest(args) -> int:
    from .ingest import ingest

    ws = Workspace.find()
    profile = ws.load_profile(args.profile)
    graph = ws.open_graph(profile, db=args.db, create=True)
    report = ingest(args.corpus or ws.corpus, graph, profile, max_scene_words=args.max_scene_words)
    _echo(report.render())
    _echo(f"\ngraph: {graph.path}")
    graph.close()
    return 0


# -------------------------------------------------------------------- extract
def cmd_extract(args) -> int:
    from .extract import extract

    ws = Workspace.find()
    profile = ws.load_profile(args.profile)
    policy = ws.load_policy(args.run)
    graph = ws.open_graph(profile, db=args.db)
    client = _client_or_none(True)
    scenes = graph.scenes()
    if args.limit:
        scenes = scenes[: args.limit]
    cap = args.max_usd if args.max_usd is not None else policy.max_usd
    report = extract(graph, profile, policy, client, scenes=scenes,
                     use_batch=args.batch, passes=args.passes or None or ("entities", "events", "ledger", "technique"),
                     max_usd=cap, progress=_echo)
    _echo(report.render())
    graph.close()
    return 2 if report.stopped else 0


def cmd_ground(args) -> int:
    from .grounding import check_grounding

    ws = Workspace.find()
    profile = ws.load_profile(args.profile)
    graph = ws.open_graph(profile, db=args.db)
    report = check_grounding(graph)
    if args.demote:
        from .grounding import demote_unproven

        counts = demote_unproven(graph, report)
        total = sum(counts.values())
        _echo(f"demoted {total:,} records that cannot prove themselves "
              f"(claim_type -> inferred, confidence capped at 0.5)")
        for kind, n in sorted(counts.items(), key=lambda t: -t[1]):
            _echo(f"  {kind}: {n:,}")
        _echo("")
    if args.json:
        _echo(json.dumps([f.__dict__ | {"severity": f.severity,
                                        "grounding": round(f.grounding, 3)}
                          for f in report.findings], indent=2))
    else:
        _echo(report.render(show=args.show))
    graph.close()
    return 1 if (report.quote_missing or report.ungrounded) else 0


def cmd_resolve(args) -> int:
    from .resolve import apply_verdicts, candidates

    ws = Workspace.find()
    profile = ws.load_profile(args.profile)
    graph = ws.open_graph(profile, db=args.db)

    if args.what == "candidates":
        groups = candidates(graph)
        _echo(f"{len(groups)} candidate group(s) — records some signal says may be one entity")
        for c in groups[: args.limit]:
            _echo(f"  [{', '.join(sorted(c.reasons)) or 'grouped'}] {len(c.members)} records")
            for m in sorted(c.members, key=lambda r: r["entity_id"]):
                _echo(f"      {m['entity_id']:36} {m['name']}")
    elif args.what == "adjudicate":
        from .llm import Usage
        from .resolve import adjudicate

        client = _client_or_none(True)
        usage = Usage()
        model = args.model or ws.load_policy(args.run).model_for("judge")
        groups = candidates(graph)[: args.limit]
        _echo(f"adjudicating {len(groups)} group(s) on {model}")
        for c in groups:
            v = adjudicate(client, model, c, graph, usage=usage)
            _echo(f"\n  {len(c.members)} records · needs_scene_text={v.needs_scene_text}")
            for cl in v.clusters:
                _echo(f"    one entity: {', '.join(cl.entity_ids)} — {cl.reason}")
            for d in v.defects:
                _echo(f"    DEFECT {d.entity_id}: {d.problem}")
        _echo(f"\n{usage.render()}")
    elif args.what == "apply":
        if not args.verdicts:
            _echo("--verdicts FILE is required for `bp resolve apply`")
            return 1
        report = apply_verdicts(graph, args.verdicts, apply=args.apply)
        _echo(report.render())
        if report.wrong_graph or report.errors:
            graph.close()
            return 1
        if not args.apply:
            _echo("\ndry run — nothing written. Re-run with --apply to merge.")
    graph.close()
    return 0


def cmd_settle(args) -> int:
    from .settle import candidate_index

    ws = Workspace.find()
    profile = ws.load_profile(args.profile)
    graph = ws.open_graph(profile, db=args.db)
    _echo(f"database: {graph.path}")
    if args.what == "index":
        index, report = candidate_index(graph, profile)
        _echo(report.render())
        if args.show:
            _echo("")
            for sid, pids in list(index.items())[: args.show]:
                _echo(f"  {sid}: {len(pids)} candidate promise(s)")
        graph.close()
        return 0

    from .settle import settle, settle_batch

    model = args.model or ws.load_policy(args.run).model_for("judge")
    scene_ids = None
    if args.book:
        # Settling runs book by book for the real pass: closing a promise early
        # shrinks every later listing, and the listing is 96% of the cost.
        scene_ids = [r["scene_id"] for r in graph.conn.execute(
            "SELECT scene_id FROM scenes WHERE book_id=? ORDER BY ord", (args.book,))]
        if not scene_ids:
            books = [r[0] for r in graph.conn.execute(
                "SELECT DISTINCT book_id FROM scenes ORDER BY book_id")]
            _echo(f"no book {args.book!r} in this graph; have: {', '.join(books)}")
            graph.close()
            return 1
    if args.sample:
        rows = [r["scene_id"] for r in graph.conn.execute(
            "SELECT scene_id FROM scenes ORDER BY ord")]
        step = max(1, len(rows) // args.sample)
        scene_ids = rows[::step][: args.sample]
    _echo(f"model {model} · cap ${args.max_usd:,.2f} · "
          f"{len(scene_ids) if scene_ids else 'all'} scenes"
          + ("" if args.apply else " · DRY RUN"))
    runner = settle_batch if args.batch else settle
    report = runner(graph, profile, _client_or_none(True), model=model,
                    scene_ids=scene_ids, max_usd=args.max_usd,
                    apply=args.apply, progress=_echo)
    _echo("")
    if args.json:
        Path(args.json).write_text(json.dumps(report.as_dict(), indent=2), encoding="utf-8")
        _echo(f"full result written to {args.json}")
    _echo(report.render())
    if not args.apply:
        _echo("\ndry run — nothing written. Re-run with --apply to close these.")
    graph.close()
    return 0


def cmd_batch(args) -> int:
    """Look in on a submitted batch without owning the poller.

    A batched run holds its own connection for hours; this is the way to ask
    "is it moving, or is it stuck?" from another terminal, which is exactly the
    question a multi-hour run raises and cannot answer about itself.
    """
    client = _client_or_none(True)
    if args.what == "list":
        for b in client.messages.batches.list(limit=args.limit).data:
            c = b.request_counts
            _echo(f"  {b.id}  {b.processing_status:10}  "
                  f"ok {c.succeeded} · err {c.errored} · left {c.processing}  {b.created_at}")
        return 0

    if not args.batch_id:
        _echo("give a batch id, or use `bp batch list`")
        return 1
    b = client.messages.batches.retrieve(args.batch_id)
    c = b.request_counts
    _echo(f"{b.id}\n  status     {b.processing_status}\n  created    {b.created_at}\n"
          f"  ended      {b.ended_at or '—'}\n  expires    {b.expires_at}\n"
          f"  succeeded  {c.succeeded}\n  errored    {c.errored}\n"
          f"  canceled   {c.canceled}\n  expired    {c.expired}\n  processing {c.processing}")
    return 0


def cmd_cast(args) -> int:
    from .extract import rebuild_scene_cast

    ws = Workspace.find()
    profile = ws.load_profile(args.profile)
    graph = ws.open_graph(profile, db=args.db)
    report = rebuild_scene_cast(graph, profile, apply=args.apply)
    _echo(report.render())
    if not args.apply:
        _echo("\ndry run — nothing written. Re-run with --apply to rebuild.")
    graph.close()
    return 0


def cmd_audit(args) -> int:
    from .extract import audit_sample

    ws = Workspace.find()
    profile = ws.load_profile(args.profile)
    graph = ws.open_graph(profile, db=args.db)
    rows = audit_sample(graph, args.n, seed=args.seed)
    if not rows:
        _echo("no citations in the graph yet — run `bp extract` first")
        return 1
    if args.json:
        _echo(json.dumps(rows, indent=2))
    else:
        _echo(f"Spot audit — {len(rows)} claims. Mark each ✓ or ✗ against the cited scene.\n")
        for i, r in enumerate(rows, 1):
            _echo(f"{i:3}. [{r['kind']}] {r['claim']}")
            _echo(f"     cited: {r['scene']}" + (f" — “{r['quote'][:100]}”" if r["quote"] else " — (no quote)"))
            _echo("     verdict: ___")
    graph.close()
    return 0


# ---------------------------------------------------------------------- check
def cmd_check(args) -> int:
    from .checks import CheckContext, run_checks
    from .draftdoc import Draft
    from .review import serve_review, write_page

    ws = Workspace.find()
    profile = ws.load_profile(args.profile)
    policy = ws.load_policy(args.run)
    graph = ws.open_graph(profile, db=args.db)
    client = _client_or_none(args.llm)

    draft = Draft.load(args.chapter)
    card = None
    if args.card:
        from .models import ChapterCard

        card = ChapterCard.model_validate_json(Path(args.card).read_text(encoding="utf-8"))
    elif draft.card_id:
        card = graph.card(draft.card_id)

    ctx = CheckContext(draft=draft, graph=graph, profile=profile, policy=policy, card=card, client=client)
    scorecard = run_checks(ctx, only=args.only or None)

    if args.json:
        _echo(json.dumps({
            "ref": draft.ref,
            "hard": len(scorecard.hard), "soft": len(scorecard.soft), "notes": len(scorecard.notes),
            "clean": scorecard.clean,
            "marginalia": [m.model_dump() for m in scorecard.marginalia],
            "skipped": scorecard.skipped,
        }, indent=2))
    else:
        _echo(f"{draft.ref} · {draft.word_count:,} words · POV {draft.pov or '?'} · {draft.date_text or 'undated'}")
        _echo(scorecard.render(verbose=not args.quiet))

    if args.serve:
        verdict, note = serve_review(draft, scorecard, card=card, port=args.port)
        _echo(f"verdict: {verdict}" + (f"\n  note: {note}" if note else ""))
        if verdict == "revise":
            if card is None:
                _echo("no chapter card — cannot revise without one (pass --card or set the draft's `card:` id)")
            else:
                from .draft import revise

                revise_client = client or _client_or_none(True)
                revised = revise(graph, profile, policy, revise_client, card,
                                 text=draft.text, marginalia=scorecard.hard + scorecard.soft,
                                 human_note=note)
                out_path = draft.path.with_name(draft.path.stem + ".revised" + draft.path.suffix) \
                    if draft.path else Path(f"{draft.ref}.revised.md")
                out_path.write_text(revised + "\n", encoding="utf-8")
                _echo(f"  revised draft: {out_path}")
    elif args.html:
        path = write_page(args.html, draft, scorecard, card=card)
        _echo(f"\nreview page: {path}")

    graph.close()
    return 0 if scorecard.clean else 2


# ----------------------------------------------------------------------- plan
def cmd_plan(args) -> int:
    from .planner import measure_skeleton, move_tournament, plan_chapter, propose_thesis, render_shortlist, shortlist

    ws = Workspace.find()
    profile = ws.load_profile(args.profile)
    policy = ws.load_policy(args.run)
    graph = ws.open_graph(profile, db=args.db)

    if args.what == "skeleton":
        skeleton = measure_skeleton(graph, book=args.book)
        _echo(skeleton.render(profile))
        graph.close()
        return 0

    client = _client_or_none(True)
    if args.what == "thesis":
        hypotheses = propose_thesis(graph, profile, policy, client, n=args.n)
        weights = {p["promise_id"]: p["weight"] for p in graph.promises("open")}
        for i, h in enumerate(hypotheses, 1):
            self_report = 0.2 * h.fits_author_statements + 0.2 * h.structural_symmetry
            _echo(f"[{i}] {h.summary}   (graph evidence {h.graph_evidence(weights):.2f} · "
                  f"self-report {self_report:.2f} · total {h.evidence_score(weights):.2f})")
            _echo(f"     pays {len(h.pays)} promises · orphans {len(h.orphans)}")
            _echo(f"     {h.rationale[:400]}\n")
        out = ws.plan / "thesis.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps([h.model_dump() for h in hypotheses], indent=2), encoding="utf-8")
        _echo(f"saved {out}")
    elif args.what == "moves":
        if not args.thread:
            _echo("--thread is required for a move tournament")
            return 1
        moves = move_tournament(graph, profile, policy, client, thread_id=args.thread,
                                breadth=args.breadth, depth=args.depth)
        _echo(render_shortlist(shortlist(moves, n=args.n)))
    elif args.what == "card":
        card = plan_chapter(graph, profile, policy, client, book=args.book or "book-next",
                            chapter=args.chapter or 1, pov=args.pov or "")
        out = ws.plan / (args.book or "book-next") / "cards" / f"ch{card.chapter:02d}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(card.model_dump_json(indent=2), encoding="utf-8")
        _echo(card.model_dump_json(indent=2))
        _echo(f"\nsaved {out}")
    graph.close()
    return 0


# ---------------------------------------------------------------------- draft
def cmd_draft(args) -> int:
    from .checks import CheckContext, run_checks
    from .draft import draft_scene, revise
    from .draftdoc import Draft
    from .llm import Usage
    from .models import ChapterCard

    ws = Workspace.find()
    profile = ws.load_profile(args.profile)
    policy = ws.load_policy(args.run)
    graph = ws.open_graph(profile, db=args.db)
    client = _client_or_none(True)

    card = (ChapterCard.model_validate_json(Path(args.card).read_text(encoding="utf-8"))
            if args.card else graph.card(args.chapter))
    if card is None:
        _echo(f"no chapter card for {args.chapter!r}. Run `bp plan card` first.")
        return 1

    usage = Usage()
    out_dir = Path(args.out or ws.drafts) / card.card_id
    out_dir.mkdir(parents=True, exist_ok=True)
    total_scenes = max(1, len(card.scenes))

    for scene_index in range(1, total_scenes + 1):
        _echo(f"scene {scene_index}/{total_scenes} — drafting {policy.candidates_per_scene} candidates")
        candidates = draft_scene(graph, profile, policy, client, card,
                                 scene_index=scene_index, usage=usage)
        ranked = []
        for cand in candidates:
            draft = Draft.parse(cand.text)
            draft.meta = {"pov": card.pov, "date": card.date_inworld, "place": card.location,
                          "cast": card.cast, "id": f"{card.card_id}.s{scene_index}c{cand.index}"}
            sc = run_checks(CheckContext(draft=draft, graph=graph, profile=profile,
                                         policy=policy, card=card, client=client if args.llm_checks else None))
            cand.scorecard = sc
            ranked.append((len(sc.hard), len(sc.soft), cand, draft, sc))
        ranked.sort(key=lambda t: (t[0], t[1]))

        for i, (_, _, cand, _, sc) in enumerate(ranked, 1):
            path = out_dir / f"s{scene_index}-c{cand.index}.md"
            path.write_text(cand.text + "\n", encoding="utf-8")
            (out_dir / f"s{scene_index}-c{cand.index}.marginalia.json").write_text(
                json.dumps([m.model_dump() for m in sc.marginalia], indent=2), encoding="utf-8")
            _echo(f"  candidate {cand.index}: {cand.words:,} words · "
                  f"{len(sc.hard)} hard, {len(sc.soft)} soft → {path.name}"
                  + ("   ← best" if i == 1 else ""))

        _, _, best, best_draft, best_sc = ranked[0]
        rounds = 0
        while best_sc.hard and rounds < policy.revision_rounds:
            rounds += 1
            _echo(f"  revision {rounds}: {len(best_sc.hard)} hard findings")
            text = revise(graph, profile, policy, client, card, text=best.text,
                          marginalia=best_sc.hard + best_sc.soft[:5],
                          scene_index=scene_index, usage=usage)
            best_draft = Draft.parse(text)
            best_draft.meta = {"pov": card.pov, "date": card.date_inworld,
                               "place": card.location, "cast": card.cast}
            best_sc = run_checks(CheckContext(draft=best_draft, graph=graph, profile=profile,
                                              policy=policy, card=card, client=None))
            best.text = text
        (out_dir / f"s{scene_index}-best.md").write_text(best.text + "\n", encoding="utf-8")
        _echo(f"  best after {rounds} revision(s): {len(best_sc.hard)} hard, {len(best_sc.soft)} soft")

    chapter_path = out_dir / "chapter.md"
    body = "\n\n* * *\n\n".join(
        (out_dir / f"s{i}-best.md").read_text(encoding="utf-8").strip()
        for i in range(1, total_scenes + 1)
    )
    chapter_path.write_text(
        f"---\nbook: {card.book}\nchapter: {card.chapter}\npov: {card.pov}\n"
        f"date: {card.date_inworld}\nplace: {card.location}\ncard: {card.card_id}\n---\n\n{body}\n",
        encoding="utf-8")
    _echo(f"\nchapter: {chapter_path}\n{usage.render()}")
    if policy.max_usd and usage.usd > policy.max_usd:
        _echo(f"WARNING: run exceeded budget.max_usd (${policy.max_usd:,.2f})")
    graph.close()
    return 0


# --------------------------------------------------------------------- accept
def cmd_accept(args) -> int:
    from .accept import accept_chapter
    from .draftdoc import Draft
    from .models import ChapterCard

    ws = Workspace.find()
    profile = ws.load_profile(args.profile)
    policy = ws.load_policy(args.run)
    graph = ws.open_graph(profile, db=args.db)
    draft = Draft.load(args.chapter)
    card = None
    if args.card:
        card = ChapterCard.model_validate_json(Path(args.card).read_text(encoding="utf-8"))
    elif draft.card_id:
        card = graph.card(draft.card_id)

    client = _client_or_none(args.llm)
    result = accept_chapter(graph, profile, policy, draft, card=card, client=client,
                            book=args.book or "", accepted_dir=ws.accepted, git=not args.no_git,
                            note=args.note or "")
    _echo(result.render())
    graph.close()
    return 0


def cmd_state(args) -> int:
    from .accept import state_before

    ws = Workspace.find()
    profile = ws.load_profile(args.profile)
    graph = ws.open_graph(profile, db=args.db)
    out = state_before(graph, profile, args.character, args.date)
    if args.json:
        _echo(json.dumps(out, indent=2))
    else:
        _echo(f"{out['character']} as of {out['as_of']} · at {out['location'] or 'unknown'}")
        for eid, b in out["beliefs"].items():
            _echo(f"  [{b['state']:>14}] {eid}: {b['summary'][:100]}")
    graph.close()
    return 0


# ----------------------------------------------------------------------- eval
def cmd_probe(args) -> int:
    from .evalharness import probe

    ws = Workspace.find()
    profile = ws.load_profile(args.profile)
    policy = ws.load_policy(args.run)
    result = probe(_client_or_none(True), policy, series=profile.name, book=args.book)
    _echo(result.render())
    out = ws.eval / "probe" / f"{profile.name}-{args.book}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result.__dict__, indent=2), encoding="utf-8")
    _echo(f"\nsaved {out}")
    return 0


def cmd_backtest(args) -> int:
    from .draftdoc import Draft
    from .evalharness import BacktestReport, hide_book, real_shape, tier_a, tier_b
    from .planner import measure_skeleton

    ws = Workspace.find()
    profile = ws.load_profile(args.profile)
    policy = ws.load_policy(args.run)
    graph = ws.open_graph(profile, db=args.db)
    report = BacktestReport(series=profile.name, hidden_book=args.hide, ablation=args.ablation)

    hidden = hide_book(graph, args.hide)
    _echo(f"hid {len(hidden)} scenes of {args.hide} from retrieval")

    if args.generated:
        drafts = [Draft.load(p) for p in sorted(Path(args.generated).glob("**/*.md"))]
        _echo(f"scoring {len(drafts)} generated chapters")
        report.constraints = tier_b(graph, profile, policy, drafts)
        gen_shape = _shape_of_drafts(drafts)
        report.shape = tier_a(gen_shape, real_shape(graph, args.hide))
    else:
        _echo("no --generated directory given; running Tier B on the corpus itself as a self-check")
        report.constraints = tier_b(graph, profile, policy, [])

    _echo("\n" + report.render())
    out = ws.eval / "runs" / f"backtest-{args.hide}-{args.ablation}.json"
    report.save(out)
    _echo(f"saved {out}")
    graph.close()
    return 0


def _shape_of_drafts(drafts) -> "object":
    from .planner import Skeleton
    from .textstats import words as _w

    if not drafts:
        return Skeleton()
    counts = [len(_w(d.text)) for d in drafts]
    povs: dict[str, int] = {}
    for d in drafts:
        if d.pov:
            povs[d.pov] = povs.get(d.pov, 0) + 1
    total = sum(povs.values()) or 1
    import statistics

    return Skeleton(
        chapters=len(drafts),
        mean_chapter_words=statistics.fmean(counts),
        sd_chapter_words=statistics.pstdev(counts) if len(counts) > 1 else 0.0,
        pov_slate=sorted(povs, key=lambda p: -povs[p]),
        pov_share={p: c / total for p, c in povs.items()},
    )


# ---------------------------------------------------------------------- graph
def cmd_graph(args) -> int:
    from .knowledge import KnowledgeGraph

    ws = Workspace.find()
    profile = ws.load_profile(args.profile)
    graph = ws.open_graph(profile, db=args.db)

    if args.what == "stats":
        for table, n in graph.counts().items():
            _echo(f"  {table:16} {n:>8,}")
        _echo(f"  corpus words     {graph.get_meta('corpus_words', '0'):>8}")
    elif args.what == "knows":
        kg = KnowledgeGraph(graph)
        k = kg.earliest_knowledge(args.event, args.character)
        _echo(f"{args.character} · event {args.event}")
        _echo(f"  earliest knowledge: {profile.calendar.format(k.day) if k.day is not None else 'never'}")
        _echo(f"  path: {kg.explain(k)}")
    elif args.what == "contradictions":
        rows = graph.contradictions(unresolved_only=True)
        _echo(f"{len(rows)} unresolved contradictions kept open")
        for r in rows:
            _echo(f"  {r['contradiction_id']}: {r['subject']}")
            _echo(f"    A: {r['reading_a']}")
            _echo(f"    B: {r['reading_b']}")
            if r["note"]:
                _echo(f"    note: {r['note']}")
    elif args.what == "promises":
        for r in sorted(graph.promises(args.status), key=lambda x: -x["weight"]):
            _echo(f"  {r['promise_id']} [{r['status']}, {r['kind']}, w={r['weight']:.2f}] {r['summary']}")
    elif args.what == "threads":
        for r in graph.threads(args.status):
            _echo(f"  {r['thread_id']} [{r['status']}] {r['name']} — {r['state'][:120]}")
    graph.close()
    return 0


def cmd_cost(args) -> int:
    """Estimate a run before paying for it."""
    from .llm import RATES

    corpus_tokens = args.corpus_tokens
    book_words = args.book_words
    per_100k = book_words / 100_000
    rows = [
        ("extraction (4 passes, batch)", corpus_tokens / 1e6 * 12.5),
        ("series thesis · move tournaments", 42.0),
        ("chapter cards · scene beats", 7.0 * per_100k),
        (f"drafting ({args.candidates}×{args.revisions})", 30.0 * per_100k * (args.candidates / 3)),
        ("hard checks", 3.0 * per_100k),
        ("soft checks · judges · panel", 10.0 * per_100k),
    ]
    _echo(f"corpus {corpus_tokens/1e6:.2f}M tokens · book {book_words:,} words\n")
    for label, cost in rows:
        _echo(f"  {label:38} ${cost:>8,.0f}")
    book_total = sum(c for l, c in rows if not l.startswith("extraction"))
    _echo(f"  {'—' * 38}")
    _echo(f"  {'extraction (once)':38} ${rows[0][1]:>8,.0f}")
    _echo(f"  {'one book':38} ${book_total:>8,.0f}")
    _echo(f"  {'+ one backtest run':38} ${rows[0][1] + book_total * 2:>8,.0f}")
    _echo("\nOrder of magnitude, not a quote. The dominant cost is human attention at "
          "whatever gates the run policy leaves manual.")
    _echo("\nThese are the plan's published unit rates, kept as written. They were derived "
          "from per-token prices above the current ones, so treat them as a ceiling: a run's "
          "actual spend is tallied by bp.llm.Usage against the current rate table, and comes "
          "out lower.")
    return 0


# ----------------------------------------------------------------------- main
_EXAMPLE_PROFILE = """# A series profile. Everything true about the SERIES lives here;
# the engine itself knows nothing about any particular series.
name: example

narration:
  mode: third_close_rotating     # first_person_rotating | third_close_rotating | frame | ...
  pov_from: chapter_header

time:
  calendar: gregorian            # gregorian | year_label | elapsed
  # epoch_label: AC              # for year_label calendars ("297 AC")

space:
  model: travel_table            # interstellar | travel_table | single_city | none
  distances: data/distances.csv  # from,to,days   (or from,to,light_years)

information:
  # This block is the reason the profile exists: it turns "could this character
  # know that yet?" into arithmetic instead of a judgment call.
  channels:
    - {name: rider, speed: 0.02 ly/day}
    - {name: raven, speed: 0.05 ly/day}

entities:
  aliases: data/aliases.yaml
  clone_lineage: false
  revivable: false

style:
  units: chapter
"""

_EXAMPLE_DISTANCES = """from,to,days
# One row per pair of named places. Symmetric; you only need each pair once.
# Column is `days` for a travel_table profile, `light_years` for interstellar.
"""

_EXAMPLE_ALIASES = """# canonical name: [every alias the text uses]
# Retrieval that misses a scene because the text says "Will" and the graph says
# "Riker" is the most common silent failure in this design, so spell them out.
"""

_EXAMPLE_RUN = """# A run policy. Everything about HOW a run behaves. Change it per run,
# or mid-book: manual while you calibrate trust, auto_if_clean once the checkers
# have earned it, manual again for the ending.
gates:
  plan:    manual                # manual | auto_if_clean | auto | {every_n: 5}
  chapter: auto_if_clean
  act:     manual

candidates:
  per_scene: 3
  revision_rounds: 2

context:
  start_tokens: 30000
  max_tokens:   50000

models:
  plan:    claude-fable-5-1
  draft:   claude-opus-5
  extract: claude-sonnet-5
  checks:  claude-haiku-4-5-20251001

checks:
  epistemic:  hard
  geography:  hard
  objects:    hard
  card:       hard
  repetition: {severity: soft, budget: corpus_baseline}
  voice:      soft
  discriminator: floor           # a floor, never an optimisation target
  panel:      soft

budget:
  max_usd: 400
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bp", description="Blue Pencil — an editorial pipeline for continuing a series.")
    p.add_argument("--version", action="version", version=f"blue-pencil {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp, *, profile=True, run=False):
        if profile:
            sp.add_argument("--profile", default=None, help="series profile name or path")
        sp.add_argument("--db", default=None, help="graph database path (default graph/<profile>.sqlite)")
        if run:
            sp.add_argument("--run", default=None, help="run policy name or path")

    sp = sub.add_parser("init", help="scaffold a workspace")
    sp.add_argument("directory", nargs="?", default=".")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("profile", help="show what a series profile asserts, for confirmation")
    common(sp)
    sp.add_argument("--check", action="store_true", help="resolve channel dates against the ingested corpus")
    sp.set_defaults(func=cmd_profile)

    sp = sub.add_parser("ingest", help="corpus -> scenes (deterministic, no API key)")
    common(sp)
    sp.add_argument("corpus", nargs="?", default=None)
    sp.add_argument("--max-scene-words", type=int, default=2500)
    sp.set_defaults(func=cmd_ingest)

    sp = sub.add_parser("extract", help="scenes -> story graph (needs a model)")
    common(sp, run=True)
    sp.add_argument("--batch", action="store_true", default=True, help="use the Batch API (half price)")
    sp.add_argument("--no-batch", dest="batch", action="store_false")
    sp.add_argument("--limit", type=int, default=0, help="only the first N scenes")
    sp.add_argument("--passes", nargs="*", default=None)
    sp.add_argument("--max-usd", type=float, default=None, dest="max_usd",
                    help="stop before a pass whose estimated cost would push the run past this "
                         "(defaults to budget.max_usd). Enforced BETWEEN passes: a submitted "
                         "batch cannot be un-billed, so set a console spend cap too")
    sp.set_defaults(func=cmd_extract)

    sp = sub.add_parser("ground", help="test every citation against the scene it names (no model, no cost)")
    common(sp)
    sp.add_argument("--show", type=int, default=25, help="how many worst findings to print")
    sp.add_argument("--demote", action="store_true",
                    help="mark records whose quote cannot be found as inferred, confidence 0.5")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_ground)

    sp = sub.add_parser("resolve", help="entity resolution: candidates, adjudication, applying verdicts")
    common(sp, run=True)
    sp.add_argument("what", choices=["candidates", "adjudicate", "apply"])
    sp.add_argument("--verdicts", default=None, help="verdicts YAML to apply")
    sp.add_argument("--apply", action="store_true", help="write the merges; default is a dry run")
    sp.add_argument("--limit", type=int, default=25)
    sp.add_argument("--model", default=None)
    sp.set_defaults(func=cmd_resolve)

    sp = sub.add_parser("settle", help="settle the promise ledger: index (free), or run the verdicts")
    common(sp, run=True)
    sp.add_argument("what", choices=["index", "run"], nargs="?", default="index")
    sp.add_argument("--show", type=int, default=0, help="list this many scenes' candidate counts")
    sp.add_argument("--sample", type=int, default=0, help="run on N scenes spread across the corpus")
    sp.add_argument("--model", default=None)
    sp.add_argument("--max-usd", type=float, default=1.0, dest="max_usd")
    sp.add_argument("--apply", action="store_true", help="write the closes; default is a dry run")
    sp.add_argument("--json", default=None, help="write the complete result here, untruncated")
    sp.add_argument("--batch", action="store_true", help="submit through the Batch API at half price")
    sp.add_argument("--book", default=None, help="settle one book (the real pass runs book by book)")
    sp.set_defaults(func=cmd_settle)

    sp = sub.add_parser("batch", help="look in on a submitted Batch API job")
    sp.add_argument("what", choices=["status", "list"], nargs="?", default="status")
    sp.add_argument("batch_id", nargs="?", default=None)
    sp.add_argument("--limit", type=int, default=10)
    sp.set_defaults(func=cmd_batch)

    sp = sub.add_parser("cast", help="rebuild scene cast from cited events (no model, no cost)")
    common(sp)
    sp.add_argument("--apply", action="store_true", help="write the rebuilt cast; default is a dry run")
    sp.set_defaults(func=cmd_cast)

    sp = sub.add_parser("audit", help="sample cited claims for the Phase 1 spot audit")
    common(sp)
    sp.add_argument("-n", type=int, default=50)
    sp.add_argument("--seed", type=int, default=0)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_audit)

    sp = sub.add_parser("check", help="the editor — run the blue pencils on any chapter")
    common(sp, run=True)
    sp.add_argument("chapter")
    sp.add_argument("--card", default=None)
    sp.add_argument("--only", nargs="*", default=None, help="run only these checks")
    sp.add_argument("--llm", action="store_true", help="enable model-backed checks and mention precision")
    sp.add_argument("--html", default=None, help="write a review page here")
    sp.add_argument("--serve", action="store_true", help="serve the review page and wait for a verdict")
    sp.add_argument("--port", type=int, default=8765)
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--quiet", action="store_true")
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("plan", help="thesis, skeleton, move tournament, chapter cards")
    common(sp, run=True)
    sp.add_argument("what", choices=["thesis", "skeleton", "moves", "card"])
    sp.add_argument("--thread", default=None)
    sp.add_argument("--book", default=None)
    sp.add_argument("--chapter", type=int, default=None)
    sp.add_argument("--pov", default=None)
    sp.add_argument("-n", type=int, default=3)
    sp.add_argument("--breadth", type=int, default=10)
    sp.add_argument("--depth", type=int, default=1, help="2 at act breaks")
    sp.set_defaults(func=cmd_plan)

    sp = sub.add_parser("draft", help="draft a chapter from its card")
    common(sp, run=True)
    sp.add_argument("chapter", help="card id, or use --card")
    sp.add_argument("--card", default=None)
    sp.add_argument("--out", default=None)
    sp.add_argument("--llm-checks", action="store_true", help="use model-backed checks while ranking")
    sp.set_defaults(func=cmd_draft)

    sp = sub.add_parser("accept", help="commit a chapter: re-extract it and move the world")
    common(sp, run=True)
    sp.add_argument("chapter")
    sp.add_argument("--card", default=None)
    sp.add_argument("--book", default=None)
    sp.add_argument("--llm", action="store_true", help="re-extract the accepted chapter (recommended)")
    sp.add_argument("--no-git", action="store_true")
    sp.add_argument("--note", default=None,
                    help="why you're accepting this — recorded in the graph, not just that you did")
    sp.set_defaults(func=cmd_accept)

    sp = sub.add_parser("state", help="what did X believe on date D — a checkout, not a guess")
    common(sp)
    sp.add_argument("character")
    sp.add_argument("date")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_state)

    sp = sub.add_parser("probe", help="contamination control: what does the bare model already know?")
    common(sp, run=True)
    sp.add_argument("--book", required=True)
    sp.set_defaults(func=cmd_probe)

    sp = sub.add_parser("backtest", help="hide a book and score the continuation")
    common(sp, run=True)
    sp.add_argument("--hide", required=True, help="book id to hide")
    sp.add_argument("--generated", default=None, help="directory of generated chapters")
    sp.add_argument("--ablation", default="full")
    sp.set_defaults(func=cmd_backtest)

    sp = sub.add_parser("graph", help="query the story graph")
    common(sp)
    sp.add_argument("what", choices=["stats", "knows", "contradictions", "promises", "threads"])
    sp.add_argument("--event", default=None)
    sp.add_argument("--character", default=None)
    sp.add_argument("--status", default=None)
    sp.set_defaults(func=cmd_graph)

    sp = sub.add_parser("cost", help="estimate a run before paying for it")
    sp.add_argument("--corpus-tokens", type=float, default=700_000)
    sp.add_argument("--book-words", type=int, default=110_000)
    sp.add_argument("--candidates", type=int, default=3)
    sp.add_argument("--revisions", type=int, default=2)
    sp.set_defaults(func=cmd_cost)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except NoCredentials as exc:
        print(f"bp: {exc}", file=sys.stderr)
        return 3
    except BluePencilError as exc:
        print(f"bp: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
