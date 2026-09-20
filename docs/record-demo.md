# Recording `docs/demo.gif`

The README's first image. It is the one thing in this repo a 60-second reader is
guaranteed to look at, so it earns a retake.

## What the scene is

The agent proposes a write, the approval panel renders, and the answer is **no**.
Ending on a rejection is the point: approving is what every agent demo shows,
refusing is what almost none do.

## Setup

Point at the fixtures so the run is reproducible and no real data is on screen:

```bash
export OBSIDIAN_VAULT_PATH=./fixtures/vault
./.venv/bin/python cli.py --no-gmail
```

Then ask for the flagship case, which reads a commitment written as prose in
`Meetings/2026-08-13 Sync with Sarah.md` and proposes a calendar write:

```
Schedule the thing I said I'd do for Sarah
```

Reject at the prompt. Then confirm on screen that nothing was written:

```
ops today
```

## Capture

- Keep it **under 20 seconds**.
- **Start mid-scene, at the proposal** — not at an empty shell prompt. Trim the
  model's thinking time; nobody needs to watch a spinner.
- A wide, short terminal (roughly 100x24) reads better inline on GitHub than a
  tall one.
- Save to `docs/demo.gif`.

Any screen recorder works; `asciinema rec` piped through `agg` gives the
smallest file if you have it.
