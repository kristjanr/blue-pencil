"""The review surface: draft on the left, blue pencils in the margin.

The design constraint comes from the risk register rather than from taste. Two
gates per chapter across seventy-plus chapters is a real commitment; if a
chapter is not reviewable in about ten minutes, the gates get rubber-stamped and
the whole design fails quietly. So this page is built for triage, not
admiration:

- hard findings first, and the count is visible before you scroll;
- every finding anchored to its line, with the citation it conflicts with;
- fixes offered, so the reader decides rather than diagnoses;
- one keystroke each for accept and reject.

A local page, not a web app. The CLI has to prove the loop before anything
prettier is worth building.
"""

from __future__ import annotations

import html
import json
import webbrowser
from pathlib import Path

from .checks import Scorecard
from .draftdoc import Draft
from .models import ChapterCard

_CSS = """
:root{--paper:#F4F5F2;--ink:#1B1D22;--ink2:#4E535B;--muted:#7A7F88;--rule:#D3D6D0;
--blue:#2B57B8;--blue-tint:#E3EAF8;--red:#B8382B;--red-tint:#F6E3E0;--amber:#8A6D1F;--amber-tint:#F7F0DC;
--serif:"Iowan Old Style",Palatino,Georgia,serif;--mono:"Courier New",monospace;color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--paper:#16181C;--ink:#E6E4DD;--ink2:#B4B3AC;
--muted:#868B94;--rule:#2C3037;--blue:#86ACF2;--blue-tint:#1C2740;--red:#E38073;--red-tint:#3A2320;
--amber:#D8BC6A;--amber-tint:#2A2517;color-scheme:dark}}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.6 var(--serif)}
header{position:sticky;top:0;z-index:5;background:var(--paper);border-bottom:2px solid var(--ink);
padding:14px 20px;display:flex;gap:18px;align-items:baseline;flex-wrap:wrap}
h1{font-size:20px;margin:0;font-weight:500}
.meta{font:12px var(--mono);color:var(--muted);letter-spacing:.05em;text-transform:uppercase}
.tally{font:12px var(--mono);letter-spacing:.05em}
.tally b{font-size:15px}
.hard{color:var(--red)}.soft{color:var(--blue)}.note{color:var(--muted)}
.actions{margin-left:auto;display:flex;gap:8px}
button{font:13px var(--mono);padding:7px 14px;border:1px solid var(--ink);background:transparent;
color:var(--ink);cursor:pointer;letter-spacing:.05em}
button.go{background:var(--blue);border-color:var(--blue);color:#fff}
button:disabled{opacity:.45;cursor:default}
main{display:grid;grid-template-columns:minmax(0,1fr) 420px;gap:32px;max-width:1400px;margin:0 auto;padding:26px 20px 90px}
.manuscript{max-width:66ch;white-space:pre-wrap}
.manuscript .ln{display:block;padding-left:56px;text-indent:-56px}
.manuscript .n{display:inline-block;width:44px;font:11px var(--mono);color:var(--muted);
text-align:right;margin-right:12px;user-select:none;text-indent:0}
.manuscript .ln.flag-hard{background:var(--red-tint)}
.manuscript .ln.flag-soft{background:var(--blue-tint)}
.margin{position:sticky;top:78px;align-self:start;max-height:calc(100vh - 110px);overflow-y:auto}
.mark{border-left:3px solid var(--blue);background:var(--blue-tint);padding:11px 14px;margin-bottom:12px}
.mark.hard{border-left-color:var(--red);background:var(--red-tint)}
.mark.floor{border-left-color:var(--amber);background:var(--amber-tint)}
.mark.note{border-left-color:var(--muted);background:transparent;border-left-style:dashed}
.mark .lbl{font:11px var(--mono);letter-spacing:.07em;text-transform:uppercase;color:var(--blue);display:block;margin-bottom:5px}
.mark.hard .lbl{color:var(--red)}.mark.floor .lbl{color:var(--amber)}.mark.note .lbl{color:var(--muted)}
.mark p{margin:0 0 7px;font-size:14.5px}
.mark .ex{font:12.5px var(--mono);color:var(--ink2);border-left:2px solid var(--rule);padding-left:8px;margin:6px 0}
.mark .fix{font-size:13.5px;color:var(--ink2);margin:3px 0 0 16px}
.mark .cite{font:11.5px var(--mono);color:var(--muted)}
.card{border:1px solid var(--rule);padding:12px 14px;margin-bottom:16px;font:12.5px var(--mono)}
.card h2{font:11px var(--mono);letter-spacing:.07em;text-transform:uppercase;color:var(--blue);margin:0 0 8px}
.empty{color:var(--muted);font-style:italic}
.noteBox{margin-bottom:16px}
.noteBox label{display:block;font:11px var(--mono);letter-spacing:.07em;text-transform:uppercase;
color:var(--muted);margin-bottom:6px}
.noteBox textarea{width:100%;min-height:70px;resize:vertical;font:14px var(--serif);color:var(--ink);
background:var(--paper);border:1px solid var(--rule);padding:8px 10px}
#done{position:fixed;inset:0;display:none;place-items:center;background:rgba(0,0,0,.6);color:#fff;
font:16px var(--mono);z-index:9}
@media(max-width:1000px){main{grid-template-columns:1fr}.margin{position:static;max-height:none}}
"""

_JS = """
const post = (verdict) => {
  document.querySelectorAll('button').forEach(b => b.disabled = true);
  const note = (document.getElementById('note') || {}).value || '';
  fetch('/decide', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({verdict, note, ref: REF})})
    .then(() => { document.getElementById('done').style.display='grid';
                  document.getElementById('done').textContent = verdict + ' — you can close this tab'; })
    .catch(e => { document.getElementById('done').style.display='grid';
                  document.getElementById('done').textContent = 'could not reach bp: ' + e; });
};
document.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  if (e.key === 'a') post('accepted');
  if (e.key === 'r') post('rejected');
  if (e.key === 'v') post('revise');
});
"""


def render_html(draft: Draft, scorecard: Scorecard, *, card: ChapterCard | None = None,
                title: str = "", serve: bool = False) -> str:
    lines = draft.text.split("\n")
    by_line: dict[int, list] = {}
    for m in scorecard.marginalia:
        by_line.setdefault(max(1, m.line - draft.body_offset), []).append(m)

    body_lines = []
    for i, line in enumerate(lines, start=1):
        marks = by_line.get(i, [])
        cls = ""
        if any(m.severity == "hard" for m in marks):
            cls = " flag-hard"
        elif any(m.severity == "soft" for m in marks):
            cls = " flag-soft"
        body_lines.append(
            f'<span class="ln{cls}" id="L{i}"><span class="n">{i}</span>{html.escape(line) or "&nbsp;"}</span>'
        )

    order = {"hard": 0, "soft": 1, "floor": 2, "note": 3}
    marks_html = []
    for m in sorted(scorecard.marginalia, key=lambda x: (order.get(x.severity, 9), x.line)):
        bits = [f'<div class="mark {html.escape(m.severity)}">',
                f'<span class="lbl">{html.escape(m.check)} · {html.escape(m.severity)}'
                + (f' · line {m.line}' if m.line else '') + '</span>',
                f'<p>{html.escape(m.message)}</p>']
        if m.excerpt:
            bits.append(f'<div class="ex">{html.escape(m.excerpt[:220])}</div>')
        for c in m.citations[:3]:
            q = f': “{html.escape(c.quote[:90])}”' if c.quote else ''
            bits.append(f'<div class="cite">cf. {html.escape(c.scene)}{q}</div>')
        for f in m.fixes:
            bits.append(f'<div class="fix">→ {html.escape(f)}</div>')
        bits.append('</div>')
        marks_html.append("".join(bits))
    if not marks_html:
        marks_html = ['<p class="empty">No marginalia. Every enabled check passed.</p>']

    card_html = ""
    if card is not None:
        rows = [f"<h2>chapter card</h2>",
                f"POV {html.escape(card.pov)} · {html.escape(card.date_inworld)} · {html.escape(card.location)}<br>",
                f"goal: {html.escape(card.goal)}<br>turn: {html.escape(card.turn)}<br>"]
        if card.reveals:
            rows.append("reveals: " + html.escape("; ".join(r.get("what", "") for r in card.reveals)) + "<br>")
        if card.seeds_paid:
            rows.append("pays: " + html.escape(", ".join(card.seeds_paid)))
        card_html = f'<div class="card">{"".join(rows)}</div>'

    skipped = ""
    if scorecard.skipped:
        skipped = ('<div class="card"><h2>stood down</h2>'
                   + "<br>".join(f"{html.escape(k)}: {html.escape(v)}" for k, v in scorecard.skipped.items())
                   + "</div>")

    actions = ""
    note_html = ""
    if serve:
        actions = ('<div class="actions">'
                   '<button class="go" onclick="post(\'accepted\')">accept (a)</button>'
                   '<button onclick="post(\'revise\')">revise (v)</button>'
                   '<button onclick="post(\'rejected\')">reject (r)</button></div>')
        note_html = ('<div class="noteBox"><label for="note">why? (reaches the reviser if you choose '
                    'revise)</label>'
                    '<textarea id="note" placeholder="e.g. this should feel quieter, and end badly"></textarea></div>')

    head = title or f"{draft.ref}"
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>blue pencil · {html.escape(head)}</title><style>{_CSS}</style></head><body>
<header>
  <h1>{html.escape(head)}</h1>
  <span class="meta">{html.escape(draft.pov or "?")} · {html.escape(draft.date_text or "undated")}
   · {html.escape(draft.place or "unplaced")} · {draft.word_count:,} words</span>
  <span class="tally"><b class="hard">{len(scorecard.hard)}</b> hard ·
   <b class="soft">{len(scorecard.soft)}</b> soft ·
   <b class="note">{len(scorecard.notes)}</b> notes</span>
  {actions}
</header>
<main>
  <div class="manuscript">{"".join(body_lines)}</div>
  <div class="margin">{note_html}{card_html}{skipped}{"".join(marks_html)}</div>
</main>
<div id="done"></div>
<script>const REF = {json.dumps(draft.ref)};{_JS}</script>
</body></html>"""


def write_page(path: str | Path, draft: Draft, scorecard: Scorecard, *,
               card: ChapterCard | None = None, title: str = "", serve: bool = False) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_html(draft, scorecard, card=card, title=title, serve=serve), encoding="utf-8")
    return p


def serve_review(draft: Draft, scorecard: Scorecard, *, card: ChapterCard | None = None,
                 port: int = 8765, open_browser: bool = True) -> tuple[str, str]:
    """Serve the page and block until the human decides. Returns (verdict, note).

    ``note`` is whatever the human typed in the free-text box, or ``""``. A
    rejection with no reason attached tells the reviser nothing it didn't
    already know from the checkers' own marginalia — the note is what lets a
    human actually speak here, not just veto.

    This is the whole 'accept/reject buttons that call bp' surface: a local
    server, one decision, then it shuts down. No daemon, no state.
    """
    from http.server import BaseHTTPRequestHandler, HTTPServer

    page = render_html(draft, scorecard, card=card, serve=True).encode("utf-8")
    decision: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep the terminal clean
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
            decision["verdict"] = str(payload.get("verdict", "rejected"))
            decision["note"] = str(payload.get("note", "")).strip()
            self.send_response(204)
            self.end_headers()

    server = HTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"review page: {url}   (a = accept, v = revise, r = reject)")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        while "verdict" not in decision:
            server.handle_request()
    except KeyboardInterrupt:
        return "rejected", ""
    finally:
        server.server_close()
    return decision["verdict"], decision["note"]
