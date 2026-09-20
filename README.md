# Personal Ops Agent

[![CI](https://github.com/varun1221/personal-ops-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/varun1221/personal-ops-agent/actions/workflows/ci.yml)

A LangGraph agent over three hand-written MCP servers, reading an Obsidian vault
and Gmail — which **cannot write anything without being told yes**.

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
  `test_approved_write_runs_exactly_once` pins it.
- **Ambiguity refuses rather than guesses.** Two entries called "Sync" in one
  note, with no start time, produces an error naming both. Deleting the wrong
  line is not something you can undo from a terminal.
- **Nobody is asked to approve a no-op.** A gated call that validation would
  reject anyway is dropped before the prompt ever renders. An approval costs
  real attention, and a doomed call must not spend it.
- **Model output is treated as untrusted.** Every path it proposes is resolved
  against the vault root before anything opens it — see the trust boundary
  below.

## At a glance

| | |
|---|---|
| **245 tests, 7.9s, no API key** | including the gate driven end to end against the real MCP servers, with a scripted model standing in for the LLM |
| **3 MCP servers** | two read-only, one write — hand-authored, each drivable on its own with the Inspector |
| **14 live eval scenarios** | run against a real model, because tool *selection* only breaks when a real model is choosing |
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

Nothing above touches a model, a network, or a real vault.
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
                        ┌──────────────────┼──────────────────┐
                        ▼                  ▼                  ▼
                   obsidian            actions             gmail
                   7 tools, READ    6 tools, WRITE      3 tools, READ
                        │                  │
                        └────────┬─────────┘
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

The trust boundary: everything arriving from the model is untrusted. Every path
goes through `VaultPaths.resolve`, which refuses anything escaping the vault,
symlinks included.

## Design notes

**The approval gate lives in the graph, not the server.** An MCP server cannot pause its
caller's graph. `agent/graph.py` checks each proposed call against `GATED_TOOLS`, calls
`interrupt()`, and dispatches only after `Command(resume=...)` comes back approved.

**Approvals are collected before any dispatch**, and that ordering is load-bearing.
`interrupt()` raises, and on resume LangGraph re-runs the node from the top, replaying
earlier interrupts from their saved answers — so anything executed before an interrupt
runs twice. Keeping every side effect after the last interrupt makes writes happen
exactly once. `test_approved_write_runs_exactly_once` pins this.

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

## Why the fast path skips the approval prompt

`ops add gym at 6pm` writes without asking. That is deliberate and worth being
explicit about: the gate exists so a human reviews what a *model inferred*. Here
there is no inference — the user typed the instruction, a regex parsed it, and
the resolved time is echoed straight back. Asking someone to confirm what they
just typed is friction with no safety in it.

The parser earns that by refusing rather than guessing. `parse_capture` returns
None for anything ambiguous — no time, a bare number that might be part of a
title, `"6-7"` with no am/pm — and those go to the agent, which does prompt.
`tests/test_quickparse.py` weights its cases accordingly: the refusals matter
more than the successes.

`ops rm` still confirms, because deleting is the one thing here that loses data.

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

## Setup

```bash
python3 -m venv .venv
./.venv/bin/pip install -e ".[dev]"
cp .env.example .env
```

Then edit `.env`:

- `OBSIDIAN_VAULT_PATH` — start at `./fixtures/vault`, switch to your real vault once
  you trust it.
- `MODEL_PROVIDER` + the matching API key. `anthropic` for reliable tool calling while
  you debug the graph; `mistral` for the free tier.

## Running

### `ops` — everyday use

```bash
ops                        today's plan, overdue first
ops todo                   every open task in the vault
ops add gym at 6pm         capture a block
ops add standup 9-9:15 tomorrow
ops tomorrow               a specific day
ops week                   the week
ops done standup           tick it off
ops free 90                gaps of at least 90 minutes
ops rm gym                 remove a block (asks first)
ops ask what do I owe Sarah?    full agent
ops chat                   interactive session
```

`add` is implied, so `ops gym at 6pm` works too.

Bare `ops` is meant to be complete for the day, so that checking it beats opening
Obsidian: overdue tasks first (three, then a count — debt you cannot see is debt
you never close), then the day's timed blocks, then anything due today with no
time on it. `ops done` searches the whole vault to match, restricted to *open*
tasks so a standup finished months ago cannot collide with today's.

Install the shim once:

```bash
echo "alias ops='$PWD/bin/ops'" >> ~/.zshrc && source ~/.zshrc
```

**Most of these never call a model.** `ops add` parses locally (see
`vaultlib/quickparse.py`) and writes straight through `vaultlib` — about **0.07s
and zero API calls**, against **~5s** for the agent path. Only `ask`, and `add`
when the phrasing is not one the parser is sure about, fall back to the agent.

That gap is the whole point: capture has to be cheap or it does not happen, and a
planner nobody captures into is just an empty folder.

### `cli.py` — the conversational agent

```bash
./.venv/bin/python cli.py --read-only --no-gmail   # obsidian only, cannot write
./.venv/bin/python cli.py --no-gmail               # adds the write server
./.venv/bin/python cli.py                          # everything
```

`--read-only` drops the write server entirely, which is the safe way to point this at
your real vault the first time.

## Layout

```
servers/obsidian/   READ-ONLY   search_notes, read_note, list_events, list_tasks,
                                get_daily_note, find_free_slots, week_overview
servers/gmail/      READ-ONLY   gmail.readonly scope
servers/actions/    WRITE       the only mutating server; every tool is gated
                                create_calendar_event, add_task, move_event,
                                delete_event, complete_task, rename_entry
ops.py              fast one-shot CLI; bypasses the model where it can
bin/ops             shim so `ops` works from any directory
agent/              the LangGraph loop, model factory, working-set memory
vaultlib/           vault parsing shared by the read and write servers
cli.py              REPL; renders approval prompts and resumes the graph
CONTEXT.md          the glossary: entry, task, event, overdue, gated tool
docs/adr/           the three decisions that would be expensive to reverse
fixtures/           two synthetic vaults (Full Calendar + Tasks, and Day Planner);
                    tests never touch the real one
evals/              live scenarios run against a real model
```

## Decisions and vocabulary

The three decisions above that would be expensive to reverse are written up in
[`docs/adr/`](docs/adr/): where the gate lives, why approvals are collected
before any dispatch, and why the fast path does not ask.

[`CONTEXT.md`](CONTEXT.md) is the glossary — what an *entry* is as against a
*task* or an *event*, why an undated task is never *overdue*, and which word to
reach for when several would do.

## Vault formats

Obsidian has no single calendar or task format — it depends on which plugins a
vault has. Three are supported, detected from the vault's own `.obsidian` config
rather than assumed:

| Format | Looks like | Where |
|---|---|---|
| Full Calendar | frontmatter `title` / `date` / `startTime` | one note per event |
| Tasks plugin | `- [ ] Task 📅 2026-08-21 ⏫` | anywhere |
| Day Planner | `- [ ] 06:10 - 06:20 Work` under `# Day planner` | daily notes |

A Day Planner entry is both a task and an event — it has a checkbox *and* a time
on a date — so it appears in `list_tasks` and `list_events` alike, tagged
`"source": "day-planner"`. Its date comes from the daily note's filename, not the
line.

Settings are read from the vault: the daily-notes folder from
`.obsidian/daily-notes.json`, and the planner heading and default duration from
the plugin's own `data.json`. So a vault whose daily notes live in `Schedules`
works without configuration. `OBSIDIAN_DAILY_FOLDER` overrides detection if it is
ever wrong.

Writes follow the same detection. `OBSIDIAN_CALENDAR_FORMAT=auto` picks Day
Planner when that plugin is installed, because writing a Full Calendar note into
a vault without that plugin produces a file nothing will ever render.

## Vault layout it expects

Nothing is required, but these make the agent more useful, and the real vault is
set up this way:

- **Daily notes** in whatever folder `.obsidian/daily-notes.json` names, filed as
  `YYYY-MM-DD.md`. This is where planner entries live and where their date comes
  from.
- **A daily template** with a `## Follow-ups` section. Commitments written there as
  prose ("Told Sarah I'd send the spec by Friday") are what `search_notes` finds
  and what the flagship query depends on. A commitment that only exists in someone's
  head cannot be found by any tool.
- **`Inbox.md`** — where `add_task` appends unless told otherwise.

The templates folder is read from `.obsidian/templates.json` and **excluded** from
searches and task listings. A template's placeholder `- [ ]` is not work the user
has to do, and surfacing it as real content is noise.

## Finding time

`find_free_slots` computes gaps between timed blocks on a day, and the prompt tells
the model to call it before proposing any time. All-day events do not consume time.
`week_overview` summarises seven days — events, booked minutes per day, busiest day —
so "how does my week look" is one call rather than seven.

## Gmail setup

Only needed without `--no-gmail`. In Google Cloud Console: create a project, enable the
Gmail API, create an OAuth client ID of type **Desktop app**, download the JSON, and
point `GMAIL_CREDENTIALS_PATH` at it. First call opens a browser once; the token caches
to `GMAIL_TOKEN_PATH`.

Get consent out of the way outside the agent:

```bash
npx @modelcontextprotocol/inspector ./.venv/bin/python servers/gmail/server.py
```

## Verifying a server on its own

Always do this before blaming the agent:

```bash
npx @modelcontextprotocol/inspector ./.venv/bin/python servers/obsidian/server.py
```

`get_tools()` gathers across servers without `return_exceptions`, so one server failing
to start can take down the whole toolset. `agent/mcp_client.py` loads per-server to turn
that into a named failure, but the Inspector is still the fastest way to isolate one.

## Tests

```bash
./.venv/bin/python -m pytest
```

**245 tests, 7.9 seconds, no API key and no `.env`** — every test points itself at
`fixtures/`. CI runs exactly this, plus `ruff check`.

Run against `fixtures/vault` and a throwaway copy of it — never your real vault.
`tests/test_approval_gate.py` drives the real graph against the real MCP servers with a
scripted model standing in for the LLM, so the gate is verified without an API key.

## Live evals

`pytest` verifies the code; `evals/live_eval.py` verifies the *agent* — the part
that only breaks when a real model chooses the tool calls.

```bash
MODEL_RPS=0.4 PACE=8 ./.venv/bin/python evals/live_eval.py all
```

14 scripted scenarios with real assertions, against a throwaway copy of your vault.
See `evals/README.md` for what it has caught that the unit suite could not — every
one of those bugs was about phrasing or formatting, not logic. The short version:
assert on the exact line and the number of approval prompts, never on a substring.

## The eval set

Run these end to end in the CLI. They are ordered by what they exercise.

| # | Query | Exercises |
|---|---|---|
| 1 | What's on my calendar Thursday? | single source, baseline |
| 2 | What did I commit to this week that isn't scheduled? | notes + calendar join |
| 3 | What did I promise anyone over email last week? | Gmail routing |
| 4 | *(after 1)* Move the second one to Monday | working memory / coreference |
| 5 | Schedule the thing I said I'd do for Sarah | full loop: read → propose → **interrupt** → write |

Against `fixtures/vault`, #1 should return three events (standup, Design Review, 1-1 with
Priya), #4 should resolve to the 1-1 with Priya, and #5 should find the commitment in
`Meetings/2026-08-13 Sync with Sarah.md` — which is written as prose and never made it
onto the calendar.

Reject once on #5 and confirm nothing was written.

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

**Free-tier tool calling is the flaky part, not the graph.** On `mistral-small-latest`
the model occasionally re-proposes a write that already succeeded and drifts from
prompt instructions ("don't ask 'shall I' in prose"). Both are mitigated but not
eliminated. The `already exists` guard in the actions server is what turns a repeated
write into a harmless error instead of duplicate data — keep it.

## Ideas from here

- Swap `MODEL_PROVIDER` to `mistral` and watch which queries degrade. Tool-call
  reliability drops with ~10 tools and long histories; that is the lesson, not a bug.
- `MultiServerMCPClient` accepts `tool_interceptors` — a second, more declarative place
  the approval gate could live. Worth comparing against the hand-rolled node.
- Split the Obsidian server into separate notes and calendar servers to see how tool
  selection changes as the count grows.
