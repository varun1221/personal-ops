# Live evals

`pytest` verifies the code. This verifies the *agent* — the part that only breaks
when a real model is choosing the tool calls.

```bash
MODEL_RPS=0.4 PACE=8 ./.venv/bin/python evals/live_eval.py [read|write|memory|history|all]
```

Runs against a throwaway copy of your real vault (for its `.obsidian` config)
with `evals/seed/` written over the daily notes. **Your vault is never touched**,
and neither is your history store — `OPS_STATE_DIR` points into the same temp
directory. Costs real API calls.

The `history` stage exists because Sightings are the one piece of History only the
model can write: nothing records one unless it calls `record_sighting` for each
commitment it raises. A unit test can prove the tool works; only a live run shows
whether the model remembers to call it.

Every scenario carries an assertion, so the output is PASS/FAIL rather than
something to eyeball. Approvals are scripted; rejections are scripted too, and
the harness snapshots the vault before each scenario so a rate-limit retry cannot
double-apply a write.

## What it has caught that unit tests could not

Each of these passed the unit suite and failed here, because each was about how a
real model phrases a tool call:

- **`search_notes` given `"a OR b OR c"` as one literal string** — matched
  nothing, and the agent confidently reported no commitments existed.
- **A write re-proposed after it already succeeded** — three approval prompts for
  one action.
- **`complete_task` quoted as `"- [ ] Standup — 2026-08-18 09:00"`** — the time
  trailing the title instead of leading it, so matching failed and the user was
  prompted twice.
- **`rename_entry` handed the whole line as the new text** — produced
  `- [ ] 10:00 - 12:00 - [ ] 10:00 - 12:00 Title`. The assertion that missed this
  only checked that the new title appeared *somewhere* in the note; it now pins
  the exact line.
- **`move_event` called with no destination** — an approval prompt for an action
  that validation would reject anyway.
- **A move leaving the note malformed** — a blank line inside the planner list and
  `## Notes` welded to the entry above it. Only visible by reading the whole file.

The pattern: assert on the *exact* line and the *number of approval prompts*, not
on substrings. A weak assertion here hides a real corruption.

## Rate limits

Mistral's free tier 429s quickly — an agent turn is several calls. `MODEL_RPS`
throttles the client and `PACE` adds seconds between scenarios; the harness also
backs off and retries on 429. A full run takes a few minutes by design.

## The manual eval set

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

