# 007 — Show old promises in the day view

**Priority:** medium · **Size:** S · **Blocked by:** 002

## Why

The original ask was *"you said you'd send Sarah the deck, 6 days ago, still
open"*. Today that only appears when you ask the assistant. Bare `ops` is meant
to be complete for the day, so the promise should show up there without asking.

Deferred deliberately: Sightings only exist if the model records them, so this
waits until 002 shows it does.

## What

After overdue tasks, a short "promised, not captured" block: open Sightings
first raised more than N days ago (default 3), oldest first, three then a
count — the same shape as the overdue list. Read locally; no model call.

## Done when

- [ ] `ops` lists open Sightings older than the threshold, each with its age and source.
- [ ] Captured and dismissed ones never appear.
- [ ] Nothing is shown, and nothing slows down, when there are none.
- [ ] Test through `ops.main` with Sightings recorded via the `history` API.
