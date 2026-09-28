# 002 — Run the history live eval, and act on what it shows

**Priority:** high · **Size:** S (run) + M (if it fails) · **Blocked by:** a working API key

## Why

Sightings are the one part of History only the model can write: nothing is
recorded unless it calls `record_sighting` for each commitment it raises. The
unit suite proves the tool works; only a live run shows whether the model
remembers to call it. The first attempt (2026-09-26) never reached a model —
the Mistral key in `.env` returns `401 Invalid API Key`.

## What

1. Put a working key in `.env` — a new Mistral free-tier key, or
   `MODEL_PROVIDER=anthropic` with an Anthropic key (see README, *Choosing an AI model*).
2. Run `MODEL_RPS=0.4 PACE=8 ./.venv/bin/python evals/live_eval.py history`.
3. Record the result in `evals/README.md`, with the model it ran against.
4. If H1 or H2 fails because the model skips `record_sighting`: fall back to
   the design's option (b) — make the commitment answer structured output
   (`[{source, quote}]`) and record Sightings deterministically in the graph.
   That is a new decision; write it up as an ADR.

## Done when

- [ ] H1–H3 have run against a real model, and the pass/fail is written down.
- [ ] If Sightings proved unreliable, either (b) is in place or the README
      limitation says how unreliable, in numbers.
- [ ] While there: note the actual cost of the run from the provider console.
      The README's "5–10 cents a question" is an estimate that has never been
      measured — replace it with the real figure.
