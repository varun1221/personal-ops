# 004 — An approval can be recorded twice if the process dies mid-write

**Priority:** medium · **Size:** S · **Type:** bug (edge case) · **Found by:** spec review

## What happens

Approvals are recorded in the graph's tool node after the last `interrupt()`,
so a normal replay records each answer once (pinned by
`test_each_answer_is_recorded_exactly_once`). But if the process dies *after*
recording and *before* the node finishes, resuming with `cli.py --thread <id>`
re-runs the node and records every answer again. `past_rejections` then shows
duplicates, and "you've refused this twice" can be false.

## Fix

Make recording idempotent on the call it answers: store the tool call's id,
add `UNIQUE (thread_id, tool_call_id)` in a new migration (append — never edit
migration 1), and insert with `ON CONFLICT DO NOTHING`.

## Done when

- [x] Recording the same `(thread_id, tool_call_id)` twice leaves one row.
- [x] Migration 2 applies cleanly to a database created by migration 1.
- [x] A test through the `history` API shows it; no test needs to kill a process.
