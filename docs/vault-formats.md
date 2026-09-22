# Vault formats

Obsidian has no single calendar or task format — it depends which plugins a
vault has. This is what the agent detects and how it decides what to write.


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

