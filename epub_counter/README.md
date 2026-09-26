# epub_counter

Tells you whether a set of EPUBs (e.g. Bobiverse books 1–6) fits in a model's
context window, with room left for the model to write.

```sh
python epub_counter.py book1.epub book2.epub ... book6.epub          # offline estimate, no deps
python epub_counter.py --exact book*.epub                            # exact count (pip install anthropic; needs ANTHROPIC_API_KEY)
python epub_counter.py --dump-dir text/ book*.epub                   # also save the plain text you'd send
```

Options: `--model` (default `claude-opus-5-5`), `--context` (default 1,000,000),
`--output-reserve` (default 128,000 — one full-length reply).

Exit code: `0` fits, `2` borderline (estimate is inconclusive — use `--exact`), `1` doesn't fit.

## How it counts

- **Characters / words** come from the book text only: the reading order
  (OPF spine) is followed, HTML tags, CSS and scripts are dropped, and
  whitespace from markup indentation is collapsed. This is the text you
  would actually paste into the prompt.
- **Tokens (estimate)** is a range: ~4 chars/token (older Claude tokenizers)
  up to 1.35× that (the tokenizer Opus 4.7+ uses). The verdict is only "fits"
  if the worst case fits.
- **Tokens (`--exact`)** uses Anthropic's `count_tokens` endpoint with the
  real model's tokenizer. It's free and doesn't run the model.

Run tests with `python -m pytest` in this directory.
