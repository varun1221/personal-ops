"""Obsidian MCP server — READ-ONLY.

Exposes the vault as five read tools. There is deliberately no write path in this
file: the only code that mutates the vault lives in servers/actions/server.py,
where every tool is approval-gated.

Run standalone for inspection:
    npx @modelcontextprotocol/inspector .venv/bin/python servers/obsidian/server.py
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

# Allow `python servers/obsidian/server.py` from the project root without install.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import frontmatter
import warnings

# pydantic-settings emits an IncompleteFieldDefinitionWarning while the MCP
# library imports. It is internal to that library, harmless, and prints to
# stderr on every server start — which means twice on every CLI launch.
warnings.filterwarnings("ignore", message=".*incomplete definition.*")

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from vaultlib.dayplanner import PlannerConfig, note_date, parse_planner_note, split_time_block
from vaultlib.events import expand_events, parse_event_note
from vaultlib.paths import VaultPathError, VaultPaths
from vaultlib.tasks import iter_tasks

load_dotenv()

# Servers talk MCP over stdout and log to stderr. At INFO the per-request chatter
# drowns the CLI, so keep stderr for things that actually need attention.
mcp = FastMCP("obsidian", log_level="WARNING")


def vault() -> VaultPaths:
    return VaultPaths.from_env()


def _parse_date(value: str | None, default: date) -> date:
    if not value:
        return default
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(
            f"Expected a date as YYYY-MM-DD, got {value!r}"
        ) from exc


@mcp.tool()
def search_notes(query: str, limit: int = 10) -> str:
    """Full-text search across every note in the vault.

    Matching is plain case-insensitive substring matching, NOT a query language.
    The only operator is OR: separate alternatives with " OR " and a note matches
    if it contains any of them. Everything else — AND, quotes, wildcards,
    parentheses, regex — is treated as literal text to search for.

    To search several phrasings at once:
        "I said I'd OR I'll send OR I promised OR follow-up"

    Use this to find commitments, decisions, or mentions of a person or project.
    Commitments are usually prose, so search the words people actually write.

    Args:
        query: Text to find, optionally several alternatives joined by " OR ".
        limit: Maximum number of notes to return.
    """
    paths = vault()
    terms = [term.strip().lower() for term in re.split(r"\s+OR\s+", query) if term.strip()]
    if not terms:
        return json.dumps({"error": "query must not be empty"})

    results = []
    for path in paths.markdown_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        lowered = text.lower()
        hit_terms = [term for term in terms if term in lowered]
        if not hit_terms:
            continue

        matches = [
            {"line_number": number, "text": line.strip()}
            for number, line in enumerate(text.splitlines(), start=1)
            if any(term in line.lower() for term in hit_terms)
        ]
        results.append(
            {
                "note_path": paths.relative(path),
                "matched_terms": hit_terms,
                "match_count": len(matches),
                "matches": matches[:5],
            }
        )
        if len(results) >= limit:
            break

    payload = {
        "query": query,
        "terms_searched": terms,
        "result_count": len(results),
        "results": results,
    }
    if not results:
        # An empty result is ambiguous: nothing there, or the wrong words? Say so,
        # so the model retries with different phrasing instead of concluding "none".
        payload["note"] = (
            "No note contains any of these terms. This means the exact words were not "
            "found — it does NOT mean no such commitment exists. Try different "
            "phrasings, or read the likely notes directly with read_note."
        )
    return json.dumps(payload, indent=2)


@mcp.tool()
def read_note(note_path: str) -> str:
    """Read one note in full, with its frontmatter parsed out.

    Args:
        note_path: Vault-relative path, e.g. "Projects/Checkout Redesign.md".
    """
    paths = vault()
    try:
        resolved = paths.resolve(note_path)
    except VaultPathError as exc:
        return json.dumps({"error": str(exc)})

    post = frontmatter.load(resolved)
    return json.dumps(
        {
            "note_path": paths.relative(resolved),
            "frontmatter": {key: str(value) for key, value in (post.metadata or {}).items()},
            "content": post.content,
        },
        indent=2,
    )


def _planner_entries(paths: VaultPaths, start: date, end: date) -> list:
    """Day Planner time blocks from daily notes falling in the range."""
    config = PlannerConfig.from_vault(paths.root)
    if not config.installed:
        return []

    entries = []
    search_root = paths.daily_dir if paths.daily_dir.is_dir() else paths.root
    for path in sorted(search_root.rglob("*.md")):
        if ".obsidian" in path.parts:
            continue
        when = note_date(path)
        if when is None or not (start <= when <= end):
            continue
        entries.extend(parse_planner_note(path, paths.relative(path), config, when))
    return entries


@mcp.tool()
def list_events(start_date: str | None = None, end_date: str | None = None) -> str:
    """List calendar events between two dates, inclusive.

    Covers both calendar formats this vault might use: Full Calendar event notes
    (recurring and multi-day events are expanded into individual dated
    occurrences) and Day Planner time blocks inside daily notes. Each result
    carries a "source" field saying which it came from.

    Defaults to the next seven days.

    Args:
        start_date: YYYY-MM-DD. Defaults to today.
        end_date: YYYY-MM-DD. Defaults to seven days after start.
    """
    paths = vault()
    try:
        start = _parse_date(start_date, date.today())
        end = _parse_date(end_date, start + timedelta(days=7))
    except ValueError as exc:
        return json.dumps({"error": str(exc)})

    if end < start:
        return json.dumps({"error": "end_date is before start_date"})

    events = []
    search_root = paths.events_dir if paths.events_dir.is_dir() else paths.root
    for path in sorted(search_root.rglob("*.md")):
        if ".obsidian" in path.parts:
            continue
        event = parse_event_note(path, paths.relative(path))
        if event is not None:
            events.append(event)

    combined = [occurrence.to_dict() for occurrence in expand_events(events, start, end)]
    for entry in combined:
        entry.setdefault("source", "full-calendar")
    combined.extend(entry.to_event_dict() for entry in _planner_entries(paths, start, end))
    combined.sort(key=lambda e: (e["date"], e.get("start_time") or ""))

    return json.dumps(
        {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "event_count": len(combined),
            "events": combined,
        },
        indent=2,
    )


@mcp.tool()
def list_tasks(
    status: str = "open",
    due_before: str | None = None,
    due_after: str | None = None,
) -> str:
    """List tasks from across the vault, in Obsidian Tasks plugin format.

    Args:
        status: "open" (todo or in progress), "done", "cancelled", or "all".
        due_before: Only tasks due on or before this YYYY-MM-DD date.
        due_after: Only tasks due on or after this YYYY-MM-DD date.
    """
    paths = vault()
    valid = {"open", "done", "cancelled", "all"}
    if status not in valid:
        return json.dumps({"error": f"status must be one of {sorted(valid)}"})

    try:
        before = _parse_date(due_before, date.max) if due_before else None
        after = _parse_date(due_after, date.min) if due_after else None
    except ValueError as exc:
        return json.dumps({"error": str(exc)})

    collected = []
    for path in paths.markdown_files():
        for task in iter_tasks(path, paths.relative(path)):
            # A bare "- [ ]" is an empty slot in the editor, not a task. Real
            # vaults are full of them and they are pure noise to the model.
            if not task.description.strip():
                continue
            if status == "open" and not task.is_open:
                continue
            if status in ("done", "cancelled") and task.status != status:
                continue
            if (before or after) and task.due is None:
                continue
            if before and task.due > before:
                continue
            if after and task.due < after:
                continue
            collected.append(task)

    # Undated tasks sort last rather than first.
    collected.sort(key=lambda t: (t.due is None, t.due or date.min))

    # A Day Planner line is a checkbox whose text starts with a time, e.g.
    # "- [ ] 06:10 - 06:20 Work". Left alone the time ends up inside the
    # description, so lift it out into real fields.
    planner = PlannerConfig.from_vault(paths.root)
    payload = []
    for task in collected:
        entry = task.to_dict()
        block = split_time_block(task.description, planner.default_duration_minutes)
        if block:
            entry["start_time"], entry["end_time"], entry["description"] = block
            entry["source"] = "day-planner"
            # The date lives in the daily note's filename, not in the line.
            if when := note_date(paths.root / task.note_path):
                entry["date"] = when.isoformat()
        payload.append(entry)

    return json.dumps(
        {"status_filter": status, "task_count": len(payload), "tasks": payload},
        indent=2,
    )


def _busy_intervals(paths: VaultPaths, day: date) -> list[tuple[int, int, str]]:
    """Timed blocks on a day, as (start_minute, end_minute, title), merged-ready."""
    payload = json.loads(list_events(day.isoformat(), day.isoformat()))
    intervals = []
    for event in payload.get("events", []):
        if event.get("all_day") or not event.get("start_time"):
            continue
        start = _to_minutes(event["start_time"])
        end = _to_minutes(event.get("end_time") or event["start_time"])
        if end > start:
            intervals.append((start, end, event.get("title", "")))
    return sorted(intervals)


def _to_minutes(clock: str) -> int:
    hours, _, minutes = clock.partition(":")
    return int(hours) * 60 + int(minutes)


def _to_clock(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


@mcp.tool()
def find_free_slots(
    note_date: str | None = None,
    duration_minutes: int = 30,
    earliest: str = "09:00",
    latest: str = "18:00",
) -> str:
    """Find gaps in a day long enough to fit something.

    Use this before proposing a time for new work, rather than guessing an hour
    and hoping it is free. Considers every timed event that day; all-day events
    do not block time.

    Args:
        note_date: YYYY-MM-DD. Defaults to today.
        duration_minutes: Minimum gap to report.
        earliest: HH:MM, start of the window to search.
        latest: HH:MM, end of the window to search.
    """
    paths = vault()
    try:
        day = _parse_date(note_date, date.today())
    except ValueError as exc:
        return json.dumps({"error": str(exc)})

    try:
        window_start, window_end = _to_minutes(earliest), _to_minutes(latest)
    except ValueError:
        return json.dumps({"error": "earliest and latest must be HH:MM"})
    if window_end <= window_start:
        return json.dumps({"error": "latest must be after earliest"})
    if duration_minutes <= 0:
        return json.dumps({"error": "duration_minutes must be positive"})

    busy = _busy_intervals(paths, day)

    free, cursor = [], window_start
    for start, end, _ in busy:
        if end <= window_start or start >= window_end:
            continue
        if start - cursor >= duration_minutes:
            free.append((cursor, start))
        cursor = max(cursor, end)
    if window_end - cursor >= duration_minutes:
        free.append((cursor, window_end))

    return json.dumps(
        {
            "date": day.isoformat(),
            "day_of_week": day.strftime("%A"),
            "window": f"{earliest}–{latest}",
            "requested_minutes": duration_minutes,
            "busy": [
                {"start_time": _to_clock(s), "end_time": _to_clock(e), "title": t}
                for s, e, t in busy
            ],
            "free_slot_count": len(free),
            "free_slots": [
                {
                    "start_time": _to_clock(s),
                    "end_time": _to_clock(e),
                    "minutes": e - s,
                }
                for s, e in free
            ],
        },
        indent=2,
    )


@mcp.tool()
def week_overview(start_date: str | None = None) -> str:
    """Summarise a week: what is scheduled each day, and how full it is.

    Defaults to the week containing today, starting Monday. Use this for
    "how does my week look" rather than calling list_events seven times.

    Args:
        start_date: YYYY-MM-DD. Any date in the week; it snaps back to Monday.
    """
    paths = vault()
    try:
        anchor = _parse_date(start_date, date.today())
    except ValueError as exc:
        return json.dumps({"error": str(exc)})

    monday = anchor - timedelta(days=anchor.weekday())
    sunday = monday + timedelta(days=6)

    events = json.loads(list_events(monday.isoformat(), sunday.isoformat())).get("events", [])
    by_day: dict[str, list] = {}
    for event in events:
        by_day.setdefault(event["date"], []).append(event)

    days = []
    for offset in range(7):
        day = monday + timedelta(days=offset)
        entries = by_day.get(day.isoformat(), [])
        booked = sum(
            _to_minutes(e["end_time"]) - _to_minutes(e["start_time"])
            for e in entries
            if e.get("start_time") and e.get("end_time")
        )
        days.append(
            {
                "date": day.isoformat(),
                "day_of_week": day.strftime("%A"),
                "event_count": len(entries),
                "booked_minutes": booked,
                "events": [
                    {
                        "title": e["title"],
                        "start_time": e.get("start_time"),
                        "end_time": e.get("end_time"),
                        "all_day": e.get("all_day", False),
                    }
                    for e in entries
                ],
            }
        )

    open_tasks = json.loads(list_tasks("open")).get("tasks", [])
    return json.dumps(
        {
            "week_start": monday.isoformat(),
            "week_end": sunday.isoformat(),
            "total_events": len(events),
            "total_booked_minutes": sum(day["booked_minutes"] for day in days),
            "busiest_day": max(days, key=lambda d: d["booked_minutes"])["date"],
            "days": days,
            "open_task_count": len(open_tasks),
        },
        indent=2,
    )


@mcp.tool()
def get_daily_note(note_date: str | None = None) -> str:
    """Read the daily note for a given date.

    Args:
        note_date: YYYY-MM-DD. Defaults to today.
    """
    paths = vault()
    try:
        target = _parse_date(note_date, date.today())
    except ValueError as exc:
        return json.dumps({"error": str(exc)})

    relative = f"{paths.daily_folder}/{target.isoformat()}.md"
    try:
        resolved = paths.resolve(relative)
    except VaultPathError:
        return json.dumps(
            {"note_date": target.isoformat(), "exists": False, "note_path": relative}
        )

    post = frontmatter.load(resolved)
    return json.dumps(
        {
            "note_date": target.isoformat(),
            "exists": True,
            "note_path": paths.relative(resolved),
            "content": post.content,
        },
        indent=2,
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
