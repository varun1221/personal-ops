# Personal Ops Agent

[![CI](https://github.com/varun1221/personal-ops-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/varun1221/personal-ops-agent/actions/workflows/ci.yml)

A LangGraph agent over four hand-written MCP servers, reading an Obsidian vault
and Gmail — which **cannot write anything without being told yes**, and which
remembers what it wrote.

![The agent proposes a calendar write; the approval gate refuses it and the vault is unchanged](docs/demo.gif)

Most agent demos end at *it called the tool*. This one is about the moment before
that. Every mutating call pauses the graph, renders exactly what it is about to
do, and waits. The scene above is the interesting one: the answer is no, and
nothing was written.

## What that actually takes

- **The gate is structural, not a prompt.** The read servers contain no write
  code path at all, and Gmail's readonly scope is enforced by Google. Being
  careful is not the mechanism.
- **An approved write happens exactly once.** `interrupt()` raises, and on resume
  LangGraph replays the node from the top — so anything executed before an
  interrupt runs twice. Every side effect is ordered after the last interrupt.
  [`test_approved_write_runs_exactly_once`](tests/test_approval_gate.py) pins it.
- **Ambiguity refuses rather than guesses.** Two entries called "Sync" in one
  note, with no start time, produces an error naming both. Deleting the wrong
  line is not something you can undo from a terminal.
- **Nobody is asked to approve a no-op.** A gated call that validation would
  reject anyway is dropped before the prompt ever renders. An approval costs real
  attention, and a doomed call must not spend it.
- **Model output is treated as untrusted.** Every path it proposes is resolved
  against the vault root first, which refuses anything escaping the vault,
  symlinks included.
- **It remembers without becoming a second source of truth.** Every write is
  recorded to SQLite as it happens, so "when did I send Sarah the deck?" has an
  answer the vault alone cannot give — a checkbox has no timestamp. But whether
  something is *open* is always read from the vault, never the database, so a
  task ticked off by hand in Obsidian is reported done on an unknown date rather
  than contradicted. ([ADR 0004](docs/adr/0004-history-is-recorded-at-write-time.md))
- **The vault's conventions are read, not assumed.** Obsidian has no single
  calendar format, so all three are detected from the vault's own config — and
  writes follow the same detection, because a Full Calendar note in a vault
  without that plugin is a file nothing will ever render.
  ([`docs/vault-formats.md`](docs/vault-formats.md))

| | |
|---|---|
| **286 tests, ~10s, no API key** | including the gate driven end to end against the real MCP servers, with a scripted model standing in for the LLM |
| **21 tools across 4 MCP servers** | 10 read-only, 6 write-gated, and 5 over the history store — hand-authored, each server drivable on its own with the Inspector |
| **3 vault formats, no configuration** | Full Calendar, Tasks plugin and Day Planner, each detected from the vault's own Obsidian config rather than assumed |
| **17 live eval scenarios** | run against a real model, because tool *selection* only breaks when a real model is choosing |
| **0.07s** | the everyday capture path, which never calls a model at all |

## Try it in a minute, without an API key

```bash
git clone https://github.com/varun1221/personal-ops-agent && cd personal-ops-agent
python3 -m venv .venv && ./.venv/bin/pip install -qe ".[dev]"

# the whole suite, including the approval gate
./.venv/bin/python -m pytest -q

# the read path, against the synthetic vault in fixtures/
OBSIDIAN_VAULT_PATH=./fixtures/vault ./.venv/bin/python ops.py todo
```

Nothing above touches a model, a network, or a real vault. To point it at your
own, see [`docs/setup.md`](docs/setup.md).

## Architecture

```
  You ──► cli.py
             │  HumanMessage
             ▼
     ┌───────────────┐  tool_calls   ┌──────────────┐
     │  agent node   │ ────────────► │  tool node   │
     │  (the model)  │ ◄──────────── │              │
     └───────────────┘  ToolMessages └──────┬───────┘
                                            │ gated?
                                    yes ────┴──── no
                                     │            │
                              interrupt()         │
                                     │            │
                          You approve/reject      │
                                     └─────┬──────┘
                                           ▼
                              MultiServerMCPClient
                                           │  stdio
          ┌──────────────────┬─────────────┴────┬──────────────────┐
          ▼                  ▼                  ▼                  ▼
     obsidian            actions             memory              gmail
   7 tools, READ     6 tools, WRITE      5 tools, HISTORY     3 tools, READ
          │                  │  records         │  reads
          │                  └───────┐   ┌──────┘
          │                          ▼   ▼
          │                        history/ ──► history.sqlite
          │                          │  checked against
          └────────────┬─────────────┘
                       ▼
                  vaultlib/
    paths · tasks · events · dayplanner · agenda
                       ▼
              your vault (markdown)
```

Four layers, each ignorant of the one above it:

- **`cli.py`** renders the approval panel and resumes the graph with
  `Command(resume=...)`. It holds no knowledge of what any tool does.
- **`agent/`** is the loop. `graph.py` is a plain `agent → tools → agent` cycle;
  the gate and the working-set update live in the tool node.
- **`servers/`** are three independent processes speaking MCP over stdio. They do
  not know an agent exists — drive any of them with the Inspector and no LangGraph
  is loaded at all.
- **`vaultlib/`** is pure parsing: no MCP, no LLM. Shared by the read and write
  servers so they cannot disagree about what a task line means.
- **`history/`** is the SQLite store — stdlib `sqlite3`, hand-written schema,
  migrations by `PRAGMA user_version`, WAL because three processes share it. The
  actions server and the fast path write Changes to it; the memory server reads
  them back, checked against the vault by the same code `ops history` uses.

The trust boundary: everything arriving from the model is untrusted. Every path
goes through `VaultPaths.resolve`, which refuses anything escaping the vault,
symlinks included.

## Where the decisions are

Four were expensive enough to reverse that they are written up in
[`docs/adr/`](docs/adr/):

1. [**The gate lives in the graph, not the server**](docs/adr/0001-approval-gate-lives-in-the-graph.md)
   — an MCP server cannot pause its caller's graph, so the gate cannot live where
   the write does. The cost: the gate is a property of this client, not of the
   tools.
2. [**Approvals are collected before any dispatch**](docs/adr/0002-approvals-are-collected-before-any-dispatch.md)
   — because `interrupt()` replays the node, moving one dispatch earlier
   silently reintroduces duplicate writes.
3. [**The fast path does not ask**](docs/adr/0003-the-fast-path-does-not-ask-for-approval.md)
   — nothing was inferred there, so there is nothing for a human to review.
4. [**History is recorded at write time; the vault stays authoritative**](docs/adr/0004-history-is-recorded-at-write-time.md)
   — exact timestamps instead of scanning the vault, bought at the cost of not
   seeing edits made in Obsidian. So the database is never asked whether
   anything is open.

[`CONTEXT.md`](CONTEXT.md) is the glossary: what an *entry* is as against a *task*
or an *event*, and why an undated task is never *overdue*.
[`docs/design-notes.md`](docs/design-notes.md) has the rest of the reasoning.

## Using it

```bash
ops                        today's plan, overdue first
ops todo                   every open task in the vault
ops add gym at 6pm         capture a block    (`add` is implied)
ops done standup           tick it off
ops week                   the week
ops free 90                gaps of at least 90 minutes
ops rm gym                 remove a block (asks first)
ops history standup        when it was added, done, moved
ops ask what do I owe Sarah?    full agent
ops chat                   interactive session
```

Bare `ops` is meant to be complete for the day, so that checking it beats opening
Obsidian: overdue first (three, then a count — debt you cannot see is debt you
never close), then the day's timed blocks, then anything due today with no time
on it.

**Most of these never call a model.** `ops add` parses locally and writes straight
through `vaultlib` — about **0.07s and zero API calls**, against **~5s** for the
agent path. Only `ask`, and `add` when the parser is not sure, fall back to the
agent. That gap is the point: capture has to be cheap or it does not happen.

For the conversational agent:

```bash
./.venv/bin/python cli.py --read-only --no-gmail   # obsidian only, cannot write
./.venv/bin/python cli.py --no-gmail               # adds the write server
./.venv/bin/python cli.py                          # everything
```

`--read-only` drops the write server entirely, which is the safe way to point
this at your real vault the first time.

## Tests

```bash
./.venv/bin/python -m pytest
```

**286 tests, about 10 seconds, no API key and no `.env`** — every test points
itself at `fixtures/`, and at its own throwaway history store. CI runs exactly this, plus `ruff check`.

`pytest` verifies the code; [`evals/`](evals/README.md) verifies the *agent* — the
part that only breaks when a real model chooses the tool calls. That README lists
six bugs the live evals caught which the unit suite could not, every one of them
about phrasing or formatting rather than logic.

## Known limitations

**`move_event` is Day Planner only.** On a Full Calendar vault it refuses and tells
you to delete and recreate. `delete_event` works on both.

**Nothing reschedules a plain task.** `complete_task` and `rename_entry` edit a task
line in place, but changing a Tasks-plugin due date means rewriting the line by hand.

**Day Planner has no all-day concept.** Every entry is a time block, so an all-day
event cannot be represented. `create_calendar_event` refuses one with a message
telling the model to supply a `start_time`, rather than inventing a time.

**`search_notes` is substring matching, not a query language.** The only operator is
` OR `. This is stated bluntly in the tool docstring because a live run had the model
send `"I said I'd OR I'll OR I promised"` as one literal string, match nothing, and
report that the user had no commitments. An empty result now says explicitly that it
is inconclusive, so the model retries instead of concluding.

**Relative dates in prose are the model's weak spot.** A note reading "by Friday next
week" gets resolved inconsistently between runs. Notes with explicit dates are read
correctly; ambiguous prose is a coin flip, and no amount of prompting fixes it.

**Tools take whatever wording the model gives them.** `complete_task`,
`rename_entry`, `move_event` and `delete_event` match in both directions, so
`"Standup"`, `"09:00 - 09:15 Standup"`, the whole raw line, and a decorated form
like `"- [ ] Standup — 2026-08-18 09:00"` all resolve to the same entry. This is
not politeness: because these are gated, every unmatched phrasing costs the user
another approval prompt. Ambiguity still refuses, so looser matching never becomes
guessing. The same normalisation applies to `rename_entry`'s *replacement* text —
without it, a model echoing the whole line back produces
`- [ ] 10:00 - 12:00 - [ ] 10:00 - 12:00 Title`.

**`ops done` can refuse with no way to narrow it.** It searches the whole vault
so the day view's tasks can be ticked off where they actually live, and refuses
when two *open* tasks match. If the same description is open in two notes there
is no way to disambiguate from the terminal — you have to go and rename one. The
alternative was picking one silently, which is the coin flip `move_event` already
refuses to make.

**History has gaps where you edited by hand.** Only writes made through `ops` or
the agent are recorded. A task ticked off in Obsidian shows as done with an
unknown date, and a task renamed there starts a new History under its new
wording. This is the cost ADR 0004 accepts in exchange for exact timestamps.

**A Sighting is only as reliable as the model's tool call.** Recognising a
promise in prose is the model's job, so a commitment it raises without calling
`record_sighting` is presented as new next time. The `history` live eval checks
for exactly this. The same promise in an email *and* a meeting note is two
Sightings; capturing one does not close the other.

**Free-tier tool calling is the flaky part, not the graph.** On `mistral-small-latest`
the model occasionally re-proposes a write that already succeeded and drifts from
prompt instructions ("don't ask 'shall I' in prose"). Both are mitigated but not
eliminated. The `already exists` guard in the actions server is what turns a repeated
write into a harmless error instead of duplicate data — keep it.

## Layout

```
servers/obsidian/   READ-ONLY   7 tools: search, read, events, tasks, free slots
servers/gmail/      READ-ONLY   gmail.readonly scope
servers/actions/    WRITE       the only server that changes the vault; every tool is gated
servers/memory/     HISTORY     when things happened, commitments raised, past rejections
agent/              the LangGraph loop, model factory, working-set memory
vaultlib/           vault parsing and selection, shared by servers and CLI
history/            the SQLite history store, and answers checked against the vault
ops.py              fast one-shot CLI; bypasses the model where it can
cli.py              REPL; renders approval prompts and resumes the graph
fixtures/           two synthetic vaults; tests never touch the real one
evals/              live scenarios run against a real model
```

| | |
|---|---|
| [`CONTEXT.md`](CONTEXT.md) | the glossary |
| [`docs/adr/`](docs/adr/) | the four expensive decisions |
| [`docs/design-notes.md`](docs/design-notes.md) | the rest of the reasoning |
| [`docs/setup.md`](docs/setup.md) | pointing it at a real vault, and Gmail OAuth |
| [`docs/vault-formats.md`](docs/vault-formats.md) | Full Calendar, Tasks, Day Planner |
| [`evals/README.md`](evals/README.md) | what the live evals caught |
