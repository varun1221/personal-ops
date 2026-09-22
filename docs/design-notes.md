# Design notes

The reasoning behind decisions that were not expensive enough to need an
[ADR](adr/), but would still puzzle a reader who found them cold.


Where the approval gate lives, and why every approval is collected before any
dispatch, are recorded as [ADR-0001](adr/0001-approval-gate-lives-in-the-graph.md)
and [ADR-0002](adr/0002-approvals-are-collected-before-any-dispatch.md).
What follows is everything that did not need an ADR.

**A checkpointer is mandatory.** Without one there is no state to pause and resume and
`interrupt()` fails at runtime. The CLI uses `AsyncSqliteSaver`; tests use `MemorySaver`.

**A human is never asked to approve a no-op.** `precheck` in `agent/graph.py`
drops a gated call that validation would reject anyway — a `move_event` with no
destination, an edit with no `note_path` — and returns the error straight to the
model without an interrupt. An approval prompt costs real attention; a doomed call
must not spend it.

**Free-tier rate limits are handled in the model factory.** An agent turn is
several calls in quick succession, which trips a free tier in seconds.
`agent/models.py` attaches an `InMemoryRateLimiter` plus retries; `MODEL_RPS`
overrides the rate.

**Read-only is structural, not conventional.** The Obsidian server has no write code
path at all, and Gmail's readonly scope is enforced by Google. Being careful is not the
mechanism.

## Why the fast path does not ask

Recorded as [ADR-0003](adr/0003-the-fast-path-does-not-ask-for-approval.md),
together with what `parse_capture` has to guarantee for the reasoning to hold.

## Moving and deleting

`move_event` and `delete_event` identify an entry by the `note_path` from a
`list_events` result plus its title and start time — so the agent lists before it
acts, rather than guessing a path.

**They refuse on ambiguity.** Two entries called "Sync" in the same note, with no
start time given, produces an error naming both rather than a coin flip. Deleting
the wrong line is not something the user can undo from here, so guessing is not an
option the tools have.

`delete_event` on a Full Calendar vault also refuses to delete a note that isn't
an event — pointing it at a project note gets a refusal, not data loss.

`move_event` writes the new entry *before* removing the old one. If the removal
then fails, the error says so explicitly and tells the user they have both, rather
than reporting success over a half-finished move. It preserves the original
duration unless given a new end time, and re-inserts in time order.

## Ideas from here

- Swap `MODEL_PROVIDER` to `mistral` and watch which queries degrade. Tool-call
  reliability drops with ~10 tools and long histories; that is the lesson, not a bug.
- `MultiServerMCPClient` accepts `tool_interceptors` — a second, more declarative place
  the approval gate could live. Worth comparing against the hand-rolled node.
- Split the Obsidian server into separate notes and calendar servers to see how tool
  selection changes as the count grows.
