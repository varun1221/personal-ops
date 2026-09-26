# Personal Ops Agent

[![CI](https://github.com/varun1221/personal-ops-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/varun1221/personal-ops-agent/actions/workflows/ci.yml)

**A personal assistant for your Obsidian notes and your Gmail.** It tells you
what's on today, what you promised people, and when you actually did things —
and it never changes a note without asking you first.

![The assistant proposes adding an event, you say no, and nothing is written](docs/demo.gif)

## What it does

- **Shows your day in one command.** Type `ops` and get what's overdue, what's
  scheduled, and what's due — without opening Obsidian.
- **Captures things instantly.** `ops gym at 6pm` adds it to today's plan in
  under a tenth of a second. No AI involved, no waiting.
- **Answers questions about your commitments.** `ops ask what do I owe Sarah?`
  reads your meeting notes, daily notes and email, and finds promises you made in
  passing that never became a task.
- **Remembers what happened.** It keeps a history of every change it makes, so
  you can ask *"when did I send Sarah the deck?"* or see that a task sat open for
  six days. A promise it has already pointed out comes back as *"first raised 6
  days ago"*, not as news.
- **Asks before it changes anything.** Every edit is shown to you exactly as it
  will be made, and nothing happens until you say yes. Saying no is always safe.

## Try it in a minute

No account, API key, or real notes needed — it ships with a sample vault.

```bash
git clone https://github.com/varun1221/personal-ops-agent && cd personal-ops-agent
python3 -m venv .venv && ./.venv/bin/pip install -qe ".[dev]"

# see every open task in the sample vault
OBSIDIAN_VAULT_PATH=./fixtures/vault ./.venv/bin/python ops.py todo
```

To use it on your own notes, see [`docs/setup.md`](docs/setup.md). To run `ops`
from any folder, link it onto your path:

```bash
ln -s "$PWD/bin/ops" ~/.local/bin/ops
```

## Everyday use

```bash
ops                          today: overdue first, then the schedule, then what's due
ops gym at 6pm               add something to today's plan
ops done standup             tick it off (and see how long it was open)
ops history standup          when it was added, done, or moved
ops todo                     every open task
ops week                     the week at a glance
ops free 90                  gaps of at least 90 minutes today
ops rm gym                   remove something (asks first)
ops ask what do I owe Sarah? ask the assistant anything
ops chat                     a back-and-forth conversation
```

Most of these run entirely on your machine and finish instantly. Only `ask` and
`chat` use an AI model — and `add`, when the wording is too loose for it to be
sure what you meant.

### Choosing an AI model

`ask` and `chat` need an API key from one of two providers, set in `.env`:

| Provider | Cost | Best for |
|---|---|---|
| **Anthropic** (Claude Sonnet 5) | Pay per use. A key from [console.anthropic.com](https://console.anthropic.com) is billed from prepaid credits, separately from any Claude subscription. A typical question costs roughly 5–10 cents. | Reliable results |
| **Mistral** (Mistral Small) | Free tier from [console.mistral.ai](https://console.mistral.ai) | Trying it out at no cost; less reliable, and rate-limited |

## Safe by design

- **Nothing is written without your yes.** The parts that read your notes and
  email have no ability to write at all. Only one component can change your
  vault, and every one of its actions pauses for your approval.
- **Your notes stay the source of truth.** The history database records *when*
  things happened. Whether something is still open is always read from your notes
  as they are now — so if you tick something off in Obsidian yourself, the
  assistant sees it.
- **It refuses rather than guesses.** If "Sync" could mean two different
  entries, it tells you both and asks you to be specific instead of picking one.
- **Email is read-only.** Gmail access uses Google's read-only permission, so the
  assistant cannot send, delete or change mail even if asked.
- **It can't reach outside your vault.** Every file path the AI suggests is
  checked first, and anything pointing outside your vault is refused.
- **Start in read-only mode.** `./.venv/bin/python cli.py --read-only` turns off
  editing entirely — the safe way to point it at your real notes the first time.

**Where your data goes:** your notes and history stay on your computer. When you
use `ask` or `chat`, the notes and emails relevant to your question are sent to
the AI provider you chose, to answer it. The everyday commands send nothing
anywhere — the one exception is `ops add` with wording it can't parse, which hands
the request to the assistant (and says so).

## How it works

```
  you ──► ops / chat
              │
              ▼
        the assistant  ──── asks you before any change
              │
   ┌──────────┼───────────┬────────────┐
   ▼          ▼           ▼            ▼
 notes      editor      history      email
(read)   (only writer) (database)    (read)
   │          │           │
   └──────────┴─────┬─────┘
                    ▼
          your Obsidian vault
```

The assistant is built from four small, independent services, each with one job:
reading notes, editing notes, keeping history, and reading email. Keeping the
editor separate is what makes "nothing changes without your yes" a guarantee
rather than a promise — the reading services have no way to write.

**History is a small SQLite database** in `~/.local/state/ops/` (configurable),
outside your vault so a sync service never copies it mid-write. It stores three things:

- **Changes** — every task or event created, completed, reopened, renamed,
  moved or deleted, with when and how.
- **Sightings** — each promise the assistant has pointed out to you, where it
  found it, and whether you turned it into a task or dismissed it.
- **Approvals** — each edit you said yes or no to, so it doesn't propose the
  same rejected change twice.

It works with all three ways people keep calendars in Obsidian — the Full
Calendar, Tasks and Day Planner plugins — detecting which one your vault uses
from its own settings. See [`docs/vault-formats.md`](docs/vault-formats.md).

### Built with

| | |
|---|---|
| **Language** | Python 3.12 |
| **Agent** | [LangGraph](https://langchain-ai.github.io/langgraph/), with human approval built into the loop |
| **Services** | 4 [MCP](https://modelcontextprotocol.io) servers, 21 tools, written by hand |
| **Database** | SQLite (Python's built-in `sqlite3`), versioned schema migrations |
| **Models** | Anthropic Claude or Mistral, switchable with one setting |
| **Integrations** | Obsidian (plain markdown files), Gmail API (read-only) |
| **Quality** | 286 automated tests, 17 live AI evaluation scenarios, GitHub Actions CI |

## Quality

```bash
./.venv/bin/python -m pytest
```

**286 tests run in about 10 seconds with no API key and no network.** They
include the full approval flow driven end to end, with a scripted stand-in for
the AI, and every one runs against sample data — never your real notes or
history. CI runs them on every push, along with the `ruff` linter.

Tests check the code; [live evaluations](evals/README.md) check the *assistant*,
by running real questions against a real model. They catch the problems only a
real model causes — the README there lists six that unit tests could not.

## Known limitations

- **Hand edits leave gaps in history.** Only changes made through `ops` or the
  assistant are recorded. A task ticked off in Obsidian shows as done on an
  unknown date; one renamed there starts a fresh history.
- **Promise tracking depends on the AI.** Spotting a promise in ordinary prose is
  the model's job, so one it forgets to record will look new next time. The same
  promise in an email and a note is tracked twice.
- **Vague dates are unreliable.** "By Friday next week" in a note is interpreted
  inconsistently. Explicit dates are read correctly.
- **Moving events needs Day Planner.** With Full Calendar it can delete and
  recreate, but not move.
- **Due dates can't be changed.** Tasks can be ticked, reopened and renamed, but
  a new due date means editing the line yourself.
- **All-day events don't exist in Day Planner**, so the assistant asks for a time
  rather than inventing one.
- **Identical tasks can't be told apart from the terminal.** If "Call mum" is
  open in two notes, `ops done call mum` refuses and you rename one.
- **The free model is flakier.** On Mistral's free tier the assistant sometimes
  proposes a change it already made; a duplicate check stops it being written
  twice.

## For developers

```
ops.py              the everyday commands; skips the AI wherever it can
cli.py              the chat interface; shows approval prompts
agent/              the LangGraph loop, prompts and model setup
servers/obsidian/   reads notes, events and tasks
servers/actions/    the only service that edits the vault; every tool needs approval
servers/memory/     reads and records history, promises and past refusals
servers/gmail/      reads email (read-only)
history/            the SQLite database and its answers, checked against the vault
vaultlib/           parses the vault; shared so nothing disagrees on what a task is
fixtures/           sample vaults used by the tests
evals/              live scenarios run against a real model
```

| | |
|---|---|
| [`docs/setup.md`](docs/setup.md) | using your own vault, API keys, and Gmail |
| [`docs/adr/`](docs/adr/) | the four decisions that were expensive to reverse, and why |
| [`CONTEXT.md`](CONTEXT.md) | the glossary: exact meanings of task, event, change, sighting |
| [`docs/design-notes.md`](docs/design-notes.md) | the rest of the reasoning |
| [`docs/vault-formats.md`](docs/vault-formats.md) | the three Obsidian calendar formats |
| [`evals/README.md`](evals/README.md) | what the live evaluations caught |
