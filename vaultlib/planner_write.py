"""Vault mutation, shared by the actions MCP server and the `ops` CLI.

This lives in vaultlib rather than in the server because the CLI's fast path
needs it too, and importing an MCP server module just to append a line pulls in
the whole mcp package — 0.37s versus 0.06s, which is most of the wall time of a
one-shot capture. Layering aside, a CLI should not depend on a server module.

Every function here is a plain mutation. Nothing here asks permission: the
approval gate lives in agent/graph.py for the agent path, and the `ops` CLI
confirms in its own way. Callers are responsible for having the right to write.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from vaultlib.dayplanner import (
    PlannerConfig,
    add_minutes,
    format_planner_line,
    match_entries,
    parse_planner_note,
    split_time_block,
)
from vaultlib.paths import VaultPathError, VaultPaths
from vaultlib.tasks import TASK_LINE, parse_task_line
from vaultlib.tasks import _normalize as _normalize_line

# Characters that are illegal or awkward in filenames across the platforms a
# vault gets synced between.
UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

def _pad(clock: str) -> str:
    """"6:10" -> "06:10", so times compare correctly as strings."""
    hours, _, minutes = clock.partition(":")
    return f"{int(hours):02d}:{minutes}"

def _minutes(clock: str) -> int:
    hours, _, minutes = clock.partition(":")
    return int(hours) * 60 + int(minutes)

def _safe_filename(text: str) -> str:
    cleaned = UNSAFE_FILENAME.sub("", text).strip().strip(".")
    return (cleaned or "Untitled")[:100]

def calendar_format(paths: VaultPaths) -> str:
    """Which calendar format this vault actually uses.

    Writing a Full Calendar note into a vault without that plugin produces a file
    nothing will ever display, so follow the vault rather than a default.
    """
    configured = os.environ.get("OBSIDIAN_CALENDAR_FORMAT", "auto").strip().lower()
    if configured in ("full_calendar", "day_planner"):
        return configured
    return "day_planner" if PlannerConfig.from_vault(paths.root).installed else "full_calendar"

def _write_planner_event(
    paths: VaultPaths, title: str, event_date: str, start_time: str | None, end_time: str | None
) -> dict:
    """Insert a Day Planner time block into the daily note for that date."""
    config = PlannerConfig.from_vault(paths.root)

    if not start_time:
        return {
            "error": (
                "This vault uses Day Planner, where every entry is a time block, so "
                "an all-day event cannot be represented. Call this again with a "
                "start_time (HH:MM) and ideally an end_time."
            )
        }
    if not end_time:
        end_time = add_minutes(start_time, config.default_duration_minutes)
    if end_time <= start_time:
        return {"error": f"end_time {end_time} is not after start_time {start_time}"}

    relative = f"{paths.daily_folder}/{event_date}.md"
    try:
        target = paths.resolve(relative, must_exist=False)
    except VaultPathError as exc:
        return {"error": str(exc)}

    existing = target.read_text(encoding="utf-8") if target.exists() else ""

    # Refuse an exact duplicate rather than stacking identical blocks.
    if target.exists():
        for entry in parse_planner_note(target, relative, config):
            if entry.description.strip().lower() == title.strip().lower() and (
                entry.start_time == start_time
            ):
                return {
                    "error": (
                        f"“{title}” is already planned at {start_time} on {event_date} "
                        f"in {relative}; refusing to add it twice."
                    )
                }

    line = format_planner_line(title, start_time, end_time)
    lines = existing.splitlines() if existing else []

    heading_index = next(
        (
            index
            for index, text in enumerate(lines)
            if text.lstrip().startswith("#")
            and text.lstrip("#").strip().lower() == config.heading.strip().lower()
        ),
        None,
    )

    if heading_index is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines += [config.heading_markdown, "", line]
    else:
        # Insert in time order within the planner section: before the first entry
        # that starts later, or at the end of the section.
        insert_at = len(lines)
        for index in range(heading_index + 1, len(lines)):
            if lines[index].lstrip().startswith("#"):
                insert_at = index
                break
            match = re.match(r"^\s*[-*+]\s+\[.\]\s+(\d{1,2}:\d{2})", lines[index])
            if match and _pad(match.group(1)) > start_time:
                insert_at = index
                break
        # Back up over blank lines so the entry joins the end of the list rather
        # than landing after the gap that separates it from the next heading.
        # Without this a move leaves a hole in the list and welds the heading to
        # the entry above it, quietly degrading the note every time.
        while insert_at > heading_index + 1 and not lines[insert_at - 1].strip():
            insert_at -= 1
        lines.insert(insert_at, line)

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    except OSError as exc:
        return {"error": f"Could not write the daily note: {exc}"}

    return {
        "created": True,
        "format": "day-planner",
        "note_path": paths.relative(target),
        "line": line,
        "title": title,
        "date": event_date,
        "start_time": start_time,
        "end_time": end_time,
        "created_note": not existing,
        "status": "DONE — this time block now exists. Do not call this tool again "
        "for it. Tell the user it is scheduled.",
    }

def _locate_planner_entry(
    paths: VaultPaths, note_path: str, title: str | None, start_time: str | None
) -> tuple[Path, object] | dict:
    """Find exactly one planner entry, or explain why that wasn't possible.

    Refuses on ambiguity rather than guessing: deleting the wrong line is not
    something the user can undo from here.
    """
    config = PlannerConfig.from_vault(paths.root)
    try:
        target = paths.resolve(note_path)
    except VaultPathError as exc:
        return {"error": str(exc)}

    entries = parse_planner_note(target, note_path, config)
    if not entries:
        return {"error": f"No Day Planner entries found in {note_path}."}

    matched = match_entries(entries, title=title, start_time=start_time)
    if not matched:
        return {
            "error": (
                f"No entry in {note_path} matches "
                f"{'title ' + repr(title) if title else ''}"
                f"{' at ' + start_time if start_time else ''}. "
                f"Entries there: "
                + ", ".join(f"{e.start_time} {e.description}" for e in entries)
            )
        }
    if len(matched) > 1:
        return {
            "error": (
                f"{len(matched)} entries in {note_path} match. Narrow it with an exact "
                f"title and start_time. Matches: "
                + ", ".join(f"{e.start_time} {e.description}" for e in matched)
            )
        }
    return target, matched[0]

def _delete_planner_line(target: Path, entry) -> str:
    """Remove one entry's line, returning the removed text."""
    lines = target.read_text(encoding="utf-8").splitlines()
    removed = lines.pop(entry.line_number - 1)
    target.write_text(("\n".join(lines).rstrip("\n") + "\n") if lines else "", encoding="utf-8")
    return removed

def _strip_decoration(text: str, default_duration_minutes: int) -> str:
    """Reduce however the model quoted a task to just its description.

    A live run had the model pass "- [ ] 09:00 - 09:30 Standup", then
    "09:00 - 09:30 Standup", then "Standup" — retrying because each failed to
    match, and costing the user an approval prompt every time. Accepting all
    three forms is cheaper than teaching the model which one is right.

    Case is preserved, because this also cleans the *replacement* text for a
    rename: a model that sends the whole line back as new_text would otherwise
    have it spliced inside the existing line, doubling the prefix.
    """
    candidate = text.strip()
    if match := TASK_LINE.match(_normalize_line(candidate)):
        candidate = match.group("body")
    if block := split_time_block(candidate, default_duration_minutes):
        candidate = block[2]
    return candidate.strip()

def _bare_description(text: str, default_duration_minutes: int) -> str:
    return _strip_decoration(text, default_duration_minutes).lower()


def description_matches(needle: str, plain: str) -> bool:
    """Whether a bare description matches what the caller asked for.

    Match in both directions. The model quotes a task back decorated —
    "- [ ] Standup - 2026-08-18 09:00" - so the real description is a substring
    of what it sent, not the other way round. Over-matching is safe because
    every caller refuses on ambiguity rather than guessing.
    """
    return bool(plain) and (needle == plain or needle in plain or plain in needle)

def _locate_task_line(
    paths: VaultPaths, note_path: str, text: str, start_time: str | None
) -> tuple[Path, int, object] | dict:
    """Find exactly one checkbox line by its text, refusing on ambiguity.

    Works for both plain Tasks-plugin lines and Day Planner blocks; for the
    latter the leading time is stripped before matching, so the caller can pass
    the description as it was shown to them.
    """
    try:
        target = paths.resolve(note_path)
    except VaultPathError as exc:
        return {"error": str(exc)}

    config = PlannerConfig.from_vault(paths.root)
    needle = _bare_description(text, config.default_duration_minutes)

    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        return {"error": f"Could not read {note_path}: {exc}"}

    candidates = []
    for index, line in enumerate(lines):
        task = parse_task_line(line, note_path=note_path, line_number=index + 1)
        if task is None or not task.description.strip():
            continue
        description, entry_start = task.description, None
        block = split_time_block(task.description, config.default_duration_minutes)
        if block:
            entry_start, _, description = block
        if start_time and entry_start != _pad(start_time):
            continue
        if description_matches(needle, description.strip().lower()):
            candidates.append((index, task, description))

    if not candidates:
        present = []
        for line in lines:
            task = parse_task_line(line)
            if task and task.description.strip():
                present.append(task.description)
        return {
            "error": f"No task in {note_path} matches {text!r}."
            + (f" Lines there: {', '.join(present)}" if present else " It has no tasks.")
        }
    # Prefer an exact match when one exists.
    exact = [c for c in candidates if c[2].strip().lower() == needle]
    candidates = exact or candidates
    if len(candidates) > 1:
        return {
            "error": f"{len(candidates)} lines in {note_path} match {text!r}. Narrow it "
            "with an exact description and start_time. Matches: "
            + ", ".join(c[2] for c in candidates)
        }

    index, task, _ = candidates[0]
    return target, index, task
