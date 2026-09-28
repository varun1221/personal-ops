# Personal Ops Agent

[![CI](https://github.com/varun1221/personal-ops-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/varun1221/personal-ops-agent/actions/workflows/ci.yml)

A personal assistant that lives in your terminal and works on your Obsidian notes and Gmail. It tells you what's on today, finds the promises you made in passing and never wrote down, and remembers when you actually did things. It never changes a note without showing you the edit first and waiting for your yes.

Everyday commands run on your machine in under a tenth of a second, with no AI involved. When you want to ask it something, bring a Claude key or use Mistral's free tier. Switching is one setting.

<!-- demo: docs/demo.gif (see docs/record-demo.md) -->
![The assistant proposes adding an event, you say no, and nothing is written](docs/demo.gif)

| | |
|---|---|
| **Your day in one word** | Type `ops` and see what's overdue, then what's scheduled, then what's due. You don't need to open Obsidian. |
| **Instant capture** | `ops gym at 6pm` lands in today's plan before you've let go of the enter key. `ops done standup` ticks it off and tells you how long it sat open. |
| **Finds what you owe people** | `ops ask what do I owe Sarah?` reads your meeting notes, daily notes and email, and pulls out commitments that never became a task. |
| **A memory of what happened** | Every change is logged, so you can ask *"when did I send Sarah the deck?"* A promise it has already raised comes back as *"first raised 6 days ago"*, not as news. |
| **Asks before it touches anything** | Every edit is shown exactly as it will be written, and nothing happens until you approve it. Saying no is always safe, and it won't ask you the same rejected thing twice. |
| **Works with your calendar setup** | Supports all three ways people keep calendars in Obsidian: Full Calendar, Tasks and Day Planner. It detects which one you use from your vault's own settings. |

## Try it in a minute

You don't need an account, an API key or your real notes. The repo ships with a sample vault.

```bash
git clone https://github.com/varun1221/personal-ops-agent && cd personal-ops-agent
python3 -m venv .venv && ./.venv/bin/pip install -qe ".[dev]"

OBSIDIAN_VAULT_PATH=./fixtures/vault ./.venv/bin/python ops.py todo
```

To point it at your own notes, see [`docs/setup.md`](docs/setup.md). To run `ops` from any folder:

```bash
ln -s "$PWD/bin/ops" ~/.local/bin/ops
```

## Getting started

```bash
ops                          # today: overdue first, then the schedule, then what's due
ops gym at 6pm               # add something to today's plan
ops done standup             # tick it off
ops history standup          # when it was added, done, or moved
ops todo                     # every open task
ops week                     # the week at a glance
ops free 90                  # gaps of at least 90 minutes today
ops rm gym                   # remove something (asks first)
ops ask what do I owe Sarah? # ask the assistant anything
ops chat                     # a back-and-forth conversation
```

Only `ask` and `chat` use an AI model. The one exception is `add`: if your wording is too loose for it to parse, it hands the request to the assistant and tells you so.

## Pick a model

`ask` and `chat` need a key from one of two providers, set in `.env`:

| Provider | Cost | Good for |
|---|---|---|
| **Anthropic** (Claude Sonnet 5) | Pay per use from prepaid credits at [console.anthropic.com](https://console.anthropic.com), separate from any Claude subscription. A typical question costs about 5–10 cents. | Reliable answers |
| **Mistral** (Mistral Small) | Free tier at [console.mistral.ai](https://console.mistral.ai) | Trying it out for free. Less reliable, and rate-limited. |

## Safe by design

- **Nothing is written without your yes.** The parts that read your notes and email have no way to write. Only one component can edit your vault, and every action it takes waits for your approval.
- **Email is read-only.** Gmail uses Google's read-only permission, so it cannot send, delete or change mail even if asked.
- **Your notes stay the source of truth.** If you tick something off in Obsidian yourself, the assistant sees it.
- **It refuses rather than guesses.** If "Sync" could mean two entries, it shows you both and asks which one.
- **It can't leave your vault.** Every file path the AI suggests is checked, and anything outside the vault is refused.
- **Start read-only.** `./.venv/bin/python cli.py --read-only` turns editing off entirely. It's the safe way to try it on your real notes for the first time.

**Where your data goes:** your notes and history stay on your machine. When you use `ask` or `chat`, the notes and emails relevant to your question are sent to the AI provider you picked. The everyday commands send nothing anywhere.

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

The assistant is a [LangGraph](https://langchain-ai.github.io/langgraph/) agent that talks to four small [MCP](https://modelcontextprotocol.io) servers, one each for reading notes, editing notes, keeping history and reading email. Because the editor is separate, "nothing changes without your yes" is enforced by the design: the reading services have no way to write.

History is a SQLite database in `~/.local/state/ops/`, kept outside your vault so a sync service never copies it mid-write. It records every change, every promise the assistant has pointed out, and every edit you approved or rejected.

| | |
|---|---|
| **Language** | Python 3.12 |
| **Agent** | LangGraph, with human approval built into the loop |
| **Services** | 4 MCP servers, 21 tools, written by hand |
| **Database** | SQLite, with versioned schema migrations |
| **Models** | Anthropic Claude or Mistral |
| **Integrations** | Obsidian (plain markdown files), Gmail API (read-only) |
| **Quality** | 286 tests, 17 live AI evaluation scenarios, GitHub Actions CI |

## Tests

```bash
./.venv/bin/python -m pytest
```

286 tests run in about 10 seconds with no API key and no network. They cover the full approval flow end to end, with a scripted stand-in for the AI, and they only ever touch sample data. CI runs them on every push, along with `ruff`.

Unit tests check the code. The [live evaluations](evals/README.md) check the *assistant* by asking a real model real questions, and they catch problems that only show up with a real model.

## Known limitations

- **Edits you make by hand leave gaps in history.** A task you tick off in Obsidian shows as done on an unknown date, and one you rename there starts a fresh history.
- **Promise tracking depends on the model.** If the model forgets to record a promise, that promise looks new next time. The same promise in an email and a note is tracked twice.
- **Vague dates are unreliable.** "By Friday next week" gets read inconsistently, but explicit dates are read correctly.
- **Moving events needs Day Planner.** With Full Calendar it can delete and recreate an event, but it can't move one.
- **Due dates can't be changed.** Tasks can be ticked, reopened and renamed. To change a due date you edit the line yourself.
- **Day Planner has no all-day events**, so the assistant asks for a time instead of inventing one.
- **Identical tasks are ambiguous.** If "Call mum" is open in two notes, `ops done call mum` refuses until you rename one.
- **The free model is flakier.** On Mistral's free tier it sometimes proposes a change it already made. A duplicate check stops it from being written twice.

## Documentation

| | |
|---|---|
| [`docs/setup.md`](docs/setup.md) | Your own vault, API keys, and Gmail |
| [`docs/vault-formats.md`](docs/vault-formats.md) | The three Obsidian calendar formats |
| [`docs/adr/`](docs/adr/) | The decisions that were expensive to reverse, and why |
| [`docs/design-notes.md`](docs/design-notes.md) | The rest of the reasoning |
| [`CONTEXT.md`](CONTEXT.md) | Glossary: exactly what a task, event, change and sighting mean |
| [`evals/README.md`](evals/README.md) | What the live evaluations caught |

### Project layout

```
ops.py              the everyday commands; skips the AI wherever it can
cli.py              the chat interface; shows approval prompts
agent/              the LangGraph loop, prompts and model setup
servers/obsidian/   reads notes, events and tasks
servers/actions/    the only service that edits the vault; every tool needs approval
servers/memory/     reads and records history, promises and past refusals
servers/gmail/      reads email (read-only)
history/            the SQLite database, checked against the vault
vaultlib/           parses the vault; shared so nothing disagrees on what a task is
fixtures/           sample vaults used by the tests
evals/              live scenarios run against a real model
```
