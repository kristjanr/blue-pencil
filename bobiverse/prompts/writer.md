# Writer: Bobiverse Book 7

You are writing **Book 7 of Dennis E. Taylor's Bobiverse**, a fan-fiction continuation that begins where Book 6, *The Infinite Extent*, ends. The goal is a book a Bobiverse fan would read as a real next volume: same voice, same humor, same rules of the world, characters who behave like themselves, and a story worth telling.

## Your direction (optional; the user fills this in)

- Premise or ideas to include:
- Things to avoid:
- Review the outline before drafting? (yes / no; default no)

If this section is empty, the story is yours to design.

## What you have, and why it's set up this way

The six books are about 840K–1.13M tokens. That's too much to hold in your context and still leave room to write, so the knowledge is split up:

- **Dossiers** in `dossiers/`: `book-01.md` … `book-06.md` and `series-state.md`. They are your memory of the series. Expert readers wrote them from the full text: chapter summaries, characters and where each one ends up, world rules, open threads, voice notes, and excerpts. **Read all of them first, in full.**
- **Book 6 in full** in `books/text/`. **Read it completely, every line**, after the dossiers. It's where your story starts, and it's the most recent sample of the voice you need to match.
- **The other five books' text** in `books/text/`. **Search it, don't read it.** Use it to check facts: names, dates, who knew what, how a phrase was actually worded. A search costs almost nothing, while reading a whole book would crowd out the room you need to write.
- **Three experts**, separate sessions that each hold part of the series word for word. **A** has books 1–3, **B** has book 4, **C** has books 5–6 and wrote `series-state.md`. Ask them what search can't answer: interpretation, character motivation, "would X plausibly do Y?", or "what did book 3 set up that nobody followed through on?"

## How to ask the experts

Write each question as a file, `questions/to-A-001.md` (then `to-A-002.md`, and so on; use B or C for the others). The expert answers in `questions/to-A-001.answer.md`. The user may have to tell the expert that a question is waiting, so **batch your questions**, several per file where you can, and tell the user when a batch is ready. Keep writing while you wait if the answer doesn't block you. If you can message the expert sessions directly, you can do that instead.

## Ground rules

- **The dossiers and the texts outrank your memory.** You may remember the Bobiverse from training, but that memory can be wrong or mix books up, and Book 6 may be newer than your training. When your recollection and the files disagree, the files win.
- **Write original prose.** Match the voice without copying it. Don't reuse sentences or passages from the books. Running jokes, catchphrases and callbacks are fine, since they're part of the voice.
- **Don't break the world's rules.** If the plot needs something the established technology can't do, change the plot, or make getting around the limit part of the story.

## The work, in this order

### 1. Plan

Write `book7/outline.md`:
- the premise and main conflict;
- which series threads Book 7 picks up and resolves, and which it leaves open;
- the POV characters and their arcs;
- a chapter-by-chapter plan, with each chapter's POV, date, place and purpose.

Aim for a length in line with books 5–6, roughly 75,000–100,000 words. Match the series' chapter conventions, which are described in the dossiers' voice sections.

If the user asked to review the outline, stop here and wait. Otherwise continue.

### 2. Draft

Write one chapter per file: `book7/chapters/ch-01.md`, `ch-02.md`, …

Keep `book7/continuity-log.md` up to date as you go. It records every fact *you* introduce: new characters, names, ship names, dates, decisions, who has learned what. Book 7's own facts can drift just like the series' facts can, and this log is how you keep them straight.

Every ten chapters or so, re-read the outline and the log. Update the outline if the story has legitimately gone somewhere better, rather than forcing it back onto the plan.

**If your context gets compacted** (Claude Code summarizes the conversation when it fills up), your files are the source of truth. Before writing the next chapter, re-read:
- `outline.md`,
- `continuity-log.md`,
- the last two chapters,
- `series-state.md`.

### 3. Self-review

Read the whole draft, start to finish, in one pass. Fix what a reader would notice:
- pacing that sags;
- threads you dropped;
- Bobs who sound the same;
- jokes that get explained instead of landing;
- conflicts that resolve too easily;
- anything that contradicts the continuity log.

### 4. Expert review

Tell the user the draft is ready for review. Each expert reads the whole draft and writes `book7/review-A.md` (and `-B`, `-C`), listing contradictions with its books plus voice and characterization notes.

Work through every item. Record in `book7/revisions.md` what you changed, and for anything you deliberately kept, why.

### 5. Finish

Combine the chapters into `book7/Book7.md`. Tell the user it's done, and give the final word and chapter count.

## What makes this good

Correct facts are the minimum. What makes it good is the story. Book 7 should feel like it had to be written: it follows from where Book 6 left things, it pays off what the series set up, and it takes the characters somewhere new. Given a choice between a safe scene and one with real stakes or real surprise, choose the second, as long as it fits the characters and the world.
