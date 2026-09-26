# Bobiverse Book 7: expert sessions

Three local Claude Code sessions each hold part of the series in full and write
dossiers that the Book 7 writer will read.

| Expert | Books | Prompt |
|---|---|---|
| A | 1–3 | `prompts/expert-A.md` |
| B | 4 | `prompts/expert-B.md` |
| C | 5–6 (+ series state) | `prompts/expert-C.md` |
| Writer | reads all dossiers + book 6, writes Book 7 | `prompts/writer.md` |

After the writer's draft is done, paste `prompts/expert-review.md` into each
expert session.

**Run them one after another, A → B → C.** Each expert reads the earlier
dossiers before its own books.

Shared folder layout (all three sessions work in the same directory):

```
books/       EPUBs or extracted .txt (don't commit — copyrighted)
dossiers/    book-01.md … book-06.md, series-state.md
questions/   to-A-001.md / to-A-001.answer.md, … (file-based Q&A)
book7/       outline.md, chapters/, continuity-log.md, review-*.md, Book7.md
```

Before starting, check that each session runs Opus 5.5 with the 1M context
window (`/model`, `/context`). Expert A ends up holding ~360–485K tokens of book
text; the default 200K window would force compaction and lose the books.
