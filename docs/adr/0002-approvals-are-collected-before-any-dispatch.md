# Approvals are collected before any dispatch

`interrupt()` works by raising. On resume, LangGraph re-runs the node from the
top and replays each earlier interrupt from its saved answer — so anything the
node executed *before* an interrupt executes again. Every approval in a turn is
therefore collected first, and every side effect ordered after the last one, so
an approved write happens exactly once.

This is the least obvious constraint in the codebase and the easiest to undo by
accident: moving a single dispatch above an `interrupt()` call reintroduces
duplicate writes, and it will look like a harmless reordering.
`test_approved_write_runs_exactly_once` exists to catch exactly that.

## Consequences

A checkpointer is **mandatory**, not an optimisation. Without persisted state
there is nothing to pause and resume, and `interrupt()` fails at runtime. The
CLI uses `AsyncSqliteSaver`; tests use `MemorySaver`.

Rejected calls still cost a round trip through the node, which is the price of
keeping the ordering rule simple enough to state in one sentence.
