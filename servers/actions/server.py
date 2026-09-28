"""Actions MCP server — WRITE.

The only server that changes anything. Every tool here is listed in
agent.mcp_client.GATED_TOOLS, so the graph pauses for the user's approval before
any of it runs.

The gate lives in the graph, not here: an MCP server cannot pause the caller's
graph. This file still refuses to clobber existing notes, because "approved" means
the user agreed to the action described — not to overwriting something unrelated.

Run standalone for inspection:
    npx @modelcontextprotocol/inspector .venv/bin/python servers/actions/server.py
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date as date_type
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import warnings

# pydantic-settings emits an IncompleteFieldDefinitionWarning while the MCP
# library imports. It is internal to that library, harmless, and prints to
# stderr on every server start — which means twice on every CLI launch.
warnings.filterwarnings("ignore", message=".*incomplete definition.*")

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

import history
from history import ChangeKind, WritePath
from vaultlib.dayplanner import (
    PlannerConfig,
    add_minutes,
    format_planner_line,
    split_time_block,
)
from vaultlib.events import format_event_note, parse_event_note
from vaultlib.paths import VaultPathError, VaultPaths
from vaultlib.planner_write import (
    _delete_planner_line,
    _locate_planner_entry,
    _locate_task_line,
    _minutes,
    _pad,
    _safe_filename,
    _strip_decoration,
    _write_planner_event,
    calendar_format,
)
from vaultlib.tasks import (
    format_task_line,
    parse_task_line,
    set_checkbox,
)

load_dotenv()

mcp = FastMCP("actions", log_level="WARNING")

# Characters that are illegal or awkward in filenames across the platforms a
# vault gets synced between.
UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def vault() -> VaultPaths:
    return VaultPaths.from_env()


def record(kind: ChangeKind, note_path: str, description: str, **follow) -> None:
    """Every write here was approved: that is the only way these tools run."""
    history.record_change(kind, note_path, description, via=WritePath.APPROVED, **follow)


def wording(paths: VaultPaths, text: str) -> str:
    """An entry's words alone, no checkbox or time block — how History knows it."""
    config = PlannerConfig.from_vault(paths.root)
    return _strip_decoration(text, config.default_duration_minutes)










@mcp.tool()
def create_calendar_event(
    title: str,
    date: str,
    start_time: str | None = None,
    end_time: str | None = None,
) -> str:
    """Create a calendar event in the vault. Requires the user's approval.

    Writes in whichever format this vault uses: a Full Calendar event note, or a
    Day Planner time block inside the daily note. Day Planner vaults require a
    start_time, because every entry there is a time block.

    Args:
        title: Event title.
        date: YYYY-MM-DD.
        start_time: HH:MM in 24-hour time. Omit for an all-day event.
        end_time: HH:MM in 24-hour time.
    """
    paths = vault()

    try:
        date_type.fromisoformat(date)
    except ValueError:
        return json.dumps({"error": f"date must be YYYY-MM-DD, got {date!r}"})

    if end_time and not start_time:
        return json.dumps({"error": "end_time given without start_time"})
    if start_time and end_time and end_time <= start_time:
        return json.dumps({"error": f"end_time {end_time} is not after start_time {start_time}"})

    if calendar_format(paths) == "day_planner":
        written = _write_planner_event(paths, title, date, start_time, end_time)
        if "error" not in written:
            record(ChangeKind.CREATED, written["note_path"], title)
        return json.dumps(written, indent=2)

    events_dir = paths.events_dir
    events_dir.mkdir(parents=True, exist_ok=True)

    relative = f"{paths.events_folder}/{date} {_safe_filename(title)}.md"
    try:
        target = paths.resolve(relative, must_exist=False)
    except VaultPathError as exc:
        return json.dumps({"error": str(exc)})

    if target.exists():
        return json.dumps(
            {"error": f"A note already exists at {relative}; refusing to overwrite it."}
        )

    try:
        target.write_text(format_event_note(title, date, start_time, end_time), encoding="utf-8")
    except (OSError, ValueError) as exc:
        return json.dumps({"error": f"Could not write the event: {exc}"})

    record(ChangeKind.CREATED, paths.relative(target), title)
    return json.dumps(
        {
            "created": True,
            "note_path": paths.relative(target),
            "title": title,
            "date": date,
            "start_time": start_time,
            "end_time": end_time,
            # Stated outright because a repeated write costs the user another
            # approval prompt, and models do re-propose writes that already landed.
            "status": "DONE — this event now exists. Do not call this tool again "
            "for it. Tell the user it is scheduled.",
        },
        indent=2,
    )






@mcp.tool()
def delete_event(
    note_path: str,
    title: str | None = None,
    start_time: str | None = None,
) -> str:
    """Delete a calendar event. Requires the user's approval. THIS REMOVES DATA.

    For a Day Planner vault, removes one time-block line from a daily note. For a
    Full Calendar vault, deletes the event's note file.

    Identify the entry with the note_path from list_events, plus enough of title
    and start_time to pick out exactly one. If more than one matches, this
    refuses rather than guessing.

    Args:
        note_path: Vault-relative path from a list_events result.
        title: The event's title, to disambiguate within a daily note.
        start_time: HH:MM, to disambiguate within a daily note.
    """
    paths = vault()

    if calendar_format(paths) == "day_planner":
        found = _locate_planner_entry(paths, note_path, title, start_time)
        if isinstance(found, dict):
            return json.dumps(found)
        target, entry = found
        try:
            removed = _delete_planner_line(target, entry)
        except OSError as exc:
            return json.dumps({"error": f"Could not update the note: {exc}"})
        record(ChangeKind.DELETED, paths.relative(target), entry.description)
        return json.dumps(
            {
                "deleted": True,
                "note_path": paths.relative(target),
                "removed_line": removed.strip(),
                "status": "DONE — the entry is gone. Do not call this tool again for "
                "it. Tell the user what was removed.",
            },
            indent=2,
        )

    # Full Calendar: the event is the note.
    try:
        target = paths.resolve(note_path)
    except VaultPathError as exc:
        return json.dumps({"error": str(exc)})

    event = parse_event_note(target, note_path)
    if event is None:
        return json.dumps(
            {
                "error": f"{note_path} is not a calendar event note; refusing to delete "
                "it. Only event notes can be deleted here."
            }
        )

    content = target.read_text(encoding="utf-8")
    try:
        target.unlink()
    except OSError as exc:
        return json.dumps({"error": f"Could not delete the note: {exc}"})

    record(ChangeKind.DELETED, paths.relative(target), event.title)
    return json.dumps(
        {
            "deleted": True,
            "note_path": note_path,
            "removed_content": content,
            "status": "DONE — the event note is deleted. Do not call this tool again "
            "for it. Tell the user what was removed.",
        },
        indent=2,
    )


@mcp.tool()
def move_event(
    note_path: str,
    new_date: str | None = None,
    new_start_time: str | None = None,
    new_end_time: str | None = None,
    title: str | None = None,
    start_time: str | None = None,
) -> str:
    """Move or reschedule an existing event. Requires the user's approval.

    Identify the event as for delete_event, then give at least one of new_date,
    new_start_time, or new_end_time. The event keeps its duration when you move
    it to a new time without saying how long it should be.

    Args:
        note_path: Vault-relative path from a list_events result.
        new_date: YYYY-MM-DD to move it to. Omit to keep the same day.
        new_start_time: HH:MM to move it to. Omit to keep the same time.
        new_end_time: HH:MM. Omit to preserve the original duration.
        title: The event's current title, to disambiguate.
        start_time: The event's current HH:MM, to disambiguate.
    """
    paths = vault()

    if not any([new_date, new_start_time, new_end_time]):
        return json.dumps(
            {"error": "Give at least one of new_date, new_start_time, or new_end_time."}
        )
    for label, value in (("new_date", new_date),):
        if value:
            try:
                date_type.fromisoformat(value)
            except ValueError:
                return json.dumps({"error": f"{label} must be YYYY-MM-DD, got {value!r}"})

    if calendar_format(paths) != "day_planner":
        return json.dumps(
            {
                "error": "move_event currently supports Day Planner vaults only. For a "
                "Full Calendar vault, delete the event and create it again."
            }
        )

    found = _locate_planner_entry(paths, note_path, title, start_time)
    if isinstance(found, dict):
        return json.dumps(found)
    source_note, entry = found

    # Preserve duration unless the caller states a new end.
    original_minutes = _minutes(entry.end_time) - _minutes(entry.start_time)
    target_start = _pad(new_start_time) if new_start_time else entry.start_time
    if new_end_time:
        target_end = _pad(new_end_time)
    else:
        target_end = add_minutes(target_start, original_minutes)
    if _minutes(target_end) <= _minutes(target_start):
        return json.dumps(
            {"error": f"end time {target_end} is not after start time {target_start}"}
        )

    target_date = new_date or entry.date.isoformat()

    # Write the new entry first: if that fails, the original is still there.
    written = _write_planner_event(paths, entry.description, target_date, target_start, target_end)
    if "error" in written:
        return json.dumps({"error": f"Could not create the moved entry: {written['error']}"})

    try:
        removed = _delete_planner_line(source_note, entry)
    except OSError as exc:
        return json.dumps(
            {
                "error": f"The new entry was created at {written['note_path']} but the "
                f"original could not be removed ({exc}). You now have both — "
                f"delete the old one by hand."
            }
        )

    record(
        ChangeKind.MOVED, paths.relative(source_note), entry.description,
        new_note_path=written["note_path"],
    )
    return json.dumps(
        {
            "moved": True,
            "from": {
                "note_path": paths.relative(source_note),
                "date": entry.date.isoformat(),
                "start_time": entry.start_time,
                "end_time": entry.end_time,
                "removed_line": removed.strip(),
            },
            "to": {
                "note_path": written["note_path"],
                "date": target_date,
                "start_time": target_start,
                "end_time": target_end,
            },
            "title": entry.description,
            "status": "DONE — the event has been moved. Do not call this tool again "
            "for it. Tell the user where it went.",
        },
        indent=2,
    )










@mcp.tool()
def complete_task(
    note_path: str,
    text: str,
    start_time: str | None = None,
    done: bool = True,
) -> str:
    """Tick a task or planner entry off (or un-tick it). Requires approval.

    Flips the checkbox in place and changes nothing else on the line, so dates,
    priorities and times survive untouched.

    Args:
        note_path: Vault-relative path from a list_tasks or list_events result.
        text: The task's description, as it was shown to you.
        start_time: HH:MM, to disambiguate between planner entries.
        done: True to tick it off, False to re-open it.
    """
    paths = vault()
    found = _locate_task_line(paths, note_path, text, start_time)
    if isinstance(found, dict):
        return json.dumps(found)
    target, index, task = found

    marker = "x" if done else " "
    lines = target.read_text(encoding="utf-8").splitlines()
    before = lines[index]
    after = set_checkbox(before, marker)

    if before == after:
        state = "already done" if done else "already open"
        return json.dumps(
            {"error": f"“{task.description}” is {state}; nothing to change."}
        )

    lines[index] = after
    try:
        target.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    except OSError as exc:
        return json.dumps({"error": f"Could not update the note: {exc}"})

    record(
        ChangeKind.COMPLETED if done else ChangeKind.REOPENED,
        paths.relative(target),
        wording(paths, task.description),
    )
    return json.dumps(
        {
            "updated": True,
            "note_path": paths.relative(target),
            "line_before": before.strip(),
            "line_after": after.strip(),
            "status": "DONE — the checkbox is updated. Do not call this tool again "
            "for it. Tell the user.",
        },
        indent=2,
    )


@mcp.tool()
def rename_entry(
    note_path: str,
    text: str,
    new_text: str,
    start_time: str | None = None,
) -> str:
    """Change the wording of a task or planner entry. Requires approval.

    Rewrites only the description, preserving the checkbox, any time block, and
    any Tasks-plugin metadata on the line.

    Args:
        note_path: Vault-relative path from a list_tasks or list_events result.
        text: The current description.
        new_text: What it should say instead.
        start_time: HH:MM, to disambiguate between planner entries.
    """
    paths = vault()
    if not new_text.strip():
        return json.dumps({"error": "new_text must not be empty"})

    found = _locate_task_line(paths, note_path, text, start_time)
    if isinstance(found, dict):
        return json.dumps(found)
    target, index, task = found

    lines = target.read_text(encoding="utf-8").splitlines()
    before = lines[index]
    config = PlannerConfig.from_vault(paths.root)
    block = split_time_block(task.description, config.default_duration_minutes)

    # The replacement gets the same treatment as the search text: the model
    # routinely echoes the whole line back, and splicing that in doubles the
    # checkbox and time prefix.
    new_text = _strip_decoration(new_text, config.default_duration_minutes)
    if not new_text:
        return json.dumps({"error": "new_text has no description in it"})

    if block:
        start, end, old_description = block
        after = before.replace(
            f"{start} - {end} {old_description}", f"{start} - {end} {new_text.strip()}", 1
        )
        if after == before:  # Time written in some other variant; rebuild the line.
            after = format_planner_line(new_text, start, end)
            if not before.lstrip().startswith("- [ ]"):
                after = set_checkbox(after, before.split("[", 1)[1][0])
    else:
        after = before.replace(task.description, new_text.strip(), 1)

    if after == before:
        return json.dumps({"error": "Could not rewrite that line; its wording did not match."})

    lines[index] = after
    try:
        target.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    except OSError as exc:
        return json.dumps({"error": f"Could not update the note: {exc}"})

    record(
        ChangeKind.RENAMED,
        paths.relative(target),
        wording(paths, task.description),
        new_description=new_text.strip(),
    )
    return json.dumps(
        {
            "updated": True,
            "note_path": paths.relative(target),
            "line_before": before.strip(),
            "line_after": after.strip(),
            "status": "DONE — the entry is renamed. Do not call this tool again for it.",
        },
        indent=2,
    )


@mcp.tool()
def add_task(
    text: str,
    due_date: str | None = None,
    priority: str | None = None,
    target_note: str | None = None,
) -> str:
    """Append a task to a note, in Obsidian Tasks plugin format. Requires approval.

    Args:
        text: The task description.
        due_date: YYYY-MM-DD.
        priority: One of highest, high, medium, low, lowest.
        target_note: Vault-relative note to append to. Defaults to the inbox note.
    """
    paths = vault()

    if not text.strip():
        return json.dumps({"error": "text must not be empty"})

    try:
        line = format_task_line(text, due_date=due_date, priority=priority)
    except ValueError as exc:
        return json.dumps({"error": str(exc)})

    relative = target_note or paths.inbox_note
    try:
        target = paths.resolve(relative, must_exist=False)
    except VaultPathError as exc:
        return json.dumps({"error": str(exc)})

    if target.exists() and target.suffix != ".md":
        return json.dumps({"error": f"{relative} is not a markdown note"})

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        separator = "" if not existing or existing.endswith("\n") else "\n"
        with target.open("a", encoding="utf-8") as handle:
            handle.write(f"{separator}{line}\n")
    except OSError as exc:
        return json.dumps({"error": f"Could not append the task: {exc}"})

    # The line's own description, not the text as given: a model that puts
    # "📅 2026-09-30" in the text has it parsed out as a due date, and completing
    # the task later will find it by what is left.
    written = parse_task_line(line)
    record(
        ChangeKind.CREATED,
        paths.relative(target),
        wording(paths, written.description if written else text),
    )
    return json.dumps(
        {
            "created": True,
            "note_path": paths.relative(target),
            "line": line,
            "created_note": not existing,
            "status": "DONE — this task was appended. Do not call this tool again "
            "for it. Tell the user it is added.",
        },
        indent=2,
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
