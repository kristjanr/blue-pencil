# The plan

The engineering plan this repository implements is Blue Pencil v0.3, archived
here as [`plan-v0.3.html`](plan-v0.3.html) (open it in a browser).

Where the code departs from the plan, it is noted in the module docstring and in
the README's *Known limitations*. The substantive departures are:

**Embeddings are local and deterministic.** The plan names `sqlite-vec`. There is
no first-party embedding endpoint alongside the models the engine drafts with, so
a hosted embedder would mean a second vendor in the middle of the retrieval path
and an index that can change under you. Since retrieval filters on hard structure
first (POV, situation and technique tags, date) and only ranks with vectors,
lexical similarity is enough for the second pass. `bp.embed.set_embedder()` swaps
it, and the backtest ablations can say whether it matters.

**Mention detection is layered rather than assumed.** The plan's cost table says
the hard checks are "mostly deterministic; model only classifies mentions". That
is exactly what is implemented — but the lexical layer had to be strong enough to
run alone, because a continuity editor that needs an API key cannot run in a
loop. See `bp/mentions.py` for what the offline layer requires of a candidate and
why.

**Travel-table channels take a factor, not a speed.** The plan's channel block
gives every channel a speed. That is right for a light-year world and wrong for a
road-and-raven one, where the table already encodes journey time; reading such a
channel as a speed makes every carrier instantaneous and silently disables the
epistemic check. Travel-table profiles declare `table_factor` instead.
