"""Answers from History, checked against the vault as it is now.

History says *when*; the vault says whether. Shared by the memory server and
`ops history`, so the agent and the terminal cannot disagree about an Entry.
"""

from __future__ import annotations

from datetime import datetime

import history
from vaultlib.dayplanner import PlannerConfig, split_time_block
from vaultlib.events import parse_event_note
from vaultlib.paths import VaultPathError, VaultPaths
from vaultlib.tasks import iter_tasks


def stamp(moment: datetime | None) -> str | None:
    return moment.isoformat(timespec="minutes") if moment else None


def vault_state(paths: VaultPaths, note_path: str, description: str) -> tuple[str, list[int]]:
    """What the vault says about an Entry right now: open, done, scheduled or missing.

    When the note holds the same wording more than once in different states,
    the answer is "ambiguous", with the line numbers of every match, rather
    than whichever came first.
    """
    try:
        target = paths.resolve(note_path)
    except VaultPathError:
        return "missing", []

    wanted = history.entry_key(description)
    config = PlannerConfig.from_vault(paths.root)
    matches: list[tuple[int, str]] = []
    for task in iter_tasks(target, note_path):
        text = task.description
        if block := split_time_block(text, config.default_duration_minutes):
            text = block[2]
        if history.entry_key(text) == wanted:
            matches.append((task.line_number, "open" if task.is_open else task.status))
    if len({status for _, status in matches}) > 1:
        return "ambiguous", [line for line, _ in matches]
    if matches:
        return matches[0][1], []

    # A Full Calendar event is its own note, with no checkbox to read.
    event = parse_event_note(target, note_path)
    if event is not None and history.entry_key(event.title) == wanted:
        return "scheduled", []
    return "missing", []


def describe(paths: VaultPaths, entry: history.EntryHistory) -> dict:
    status, lines = vault_state(paths, entry.note_path, entry.description)

    created_at = next((c.at for c in entry.changes if c.kind == "created"), None)
    completed_at = None
    for change in entry.changes:
        if change.kind == "completed":
            completed_at = change.at
        elif change.kind == "reopened":
            completed_at = None
    # The vault outranks the record: a completion it no longer shows was undone
    # somewhere History did not see.
    if status != "done":
        completed_at = None

    end = completed_at or (datetime.now() if status == "open" else None)
    open_for_days = (end - created_at).days if created_at and end else None

    if status == "done" and completed_at:
        summary = f"Done {completed_at:%a %-d %b %Y at %H:%M}"
        if open_for_days is not None:
            summary += f", after being open {open_for_days} days"
    elif status == "done":
        summary = (
            "Done, but not through ops — the date it was ticked off is unknown."
        )
    elif status == "open":
        summary = f"Still open, for {open_for_days} days." if open_for_days is not None else "Still open."
    elif status == "missing":
        last = entry.changes[-1] if entry.changes else None
        summary = "No longer in the vault" + (
            f"; last recorded as {last.kind} on {last.at:%a %-d %b %Y}." if last else "."
        )
    elif status == "ambiguous":
        summary = (
            "Can't tell whether it's open: the note has it more than once, in "
            f"different states (lines {', '.join(map(str, lines))})."
        )
    else:
        summary = f"In the vault as {status}."

    return {
        "note_path": entry.note_path,
        "description": entry.description,
        "status": status,
        "created_at": stamp(created_at),
        "completed_at": stamp(completed_at),
        "open_for_days": open_for_days,
        "summary": summary,
        **({"lines": lines} if lines else {}),
        "changes": [
            {"kind": c.kind, "at": stamp(c.at), "via": c.via} for c in entry.changes
        ],
    }
