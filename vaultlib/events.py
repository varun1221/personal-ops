"""Obsidian Full Calendar plugin — frontmatter event parsing.

Three event shapes live in the same frontmatter vocabulary:

  single     title, date, startTime, endTime, allDay
  multi-day  title, startDate, endDate
  recurring  title, type: recurring, daysOfWeek, startTime, endTime,
             startRecur, endRecur

`expand_events` flattens all three into concrete dated occurrences so callers
never have to care which shape a note used.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import frontmatter

# Full Calendar's single-letter weekday codes. R is Thursday and U is Sunday
# because T and S were already taken.
DAY_CODES = {"U": 6, "M": 0, "T": 1, "W": 2, "R": 3, "F": 4, "S": 5}


class EventParseError(ValueError):
    """Raised when a note claims to be an event but its frontmatter is unusable."""


def _coerce_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def _coerce_time(value) -> str | None:
    """Normalize a time to 'HH:MM'.

    Unquoted `startTime: 10:00` is read by YAML 1.1 as the sexagesimal integer
    600, so an int here means minutes-since-midnight, not an hour.
    """
    if value is None:
        return None
    if isinstance(value, int):
        hours, minutes = divmod(value, 60)
        return f"{hours:02d}:{minutes:02d}"
    text = str(value).strip()
    if not text:
        return None
    parts = text.split(":")
    try:
        hours = int(parts[0])
        minutes = int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        return None
    return f"{hours:02d}:{minutes:02d}"


@dataclass
class Event:
    """A calendar event as written in a note, before recurrence expansion."""

    title: str
    note_path: str
    all_day: bool = False
    start_time: str | None = None
    end_time: str | None = None
    date: date | None = None
    end_date: date | None = None
    recurring: bool = False
    days_of_week: list[int] | None = None
    start_recur: date | None = None
    end_recur: date | None = None


@dataclass
class Occurrence:
    """One concrete dated instance of an event."""

    title: str
    date: date
    note_path: str
    all_day: bool = False
    start_time: str | None = None
    end_time: str | None = None
    recurring: bool = False

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "date": self.date.isoformat(),
            "day_of_week": self.date.strftime("%A"),
            "all_day": self.all_day,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "recurring": self.recurring,
            "note_path": self.note_path,
        }


def parse_event_note(path: Path, note_path: str) -> Event | None:
    """Parse a note into an Event, or return None if it isn't one."""
    try:
        post = frontmatter.load(path)
    except (OSError, UnicodeDecodeError, Exception):  # noqa: B014 - yaml raises broadly
        return None

    meta = post.metadata or {}
    title = meta.get("title")
    if not title:
        return None  # A note without a title is not a Full Calendar event.

    is_recurring = str(meta.get("type", "")).strip().lower() == "recurring"

    days_of_week = None
    if is_recurring:
        raw_days = meta.get("daysOfWeek") or []
        if isinstance(raw_days, str):
            raw_days = [part.strip() for part in raw_days.replace(",", " ").split()]
        days_of_week = [
            DAY_CODES[str(day).strip().upper()]
            for day in raw_days
            if str(day).strip().upper() in DAY_CODES
        ]

    return Event(
        title=str(title),
        note_path=note_path,
        all_day=bool(meta.get("allDay", False)),
        start_time=_coerce_time(meta.get("startTime")),
        end_time=_coerce_time(meta.get("endTime")),
        date=_coerce_date(meta.get("date")) or _coerce_date(meta.get("startDate")),
        end_date=_coerce_date(meta.get("endDate")),
        recurring=is_recurring,
        days_of_week=days_of_week,
        start_recur=_coerce_date(meta.get("startRecur")),
        end_recur=_coerce_date(meta.get("endRecur")),
    )


def expand_events(events: list[Event], start: date, end: date) -> list[Occurrence]:
    """Flatten events into concrete occurrences falling within [start, end]."""
    occurrences: list[Occurrence] = []

    for event in events:
        if event.recurring:
            if not event.days_of_week:
                continue
            window_start = max(start, event.start_recur or start)
            window_end = min(end, event.end_recur or end)
            current = window_start
            while current <= window_end:
                if current.weekday() in event.days_of_week:
                    occurrences.append(
                        Occurrence(
                            title=event.title,
                            date=current,
                            note_path=event.note_path,
                            all_day=event.all_day,
                            start_time=event.start_time,
                            end_time=event.end_time,
                            recurring=True,
                        )
                    )
                current += timedelta(days=1)
            continue

        if event.date is None:
            continue

        # Multi-day events occupy every day in their span.
        span_end = event.end_date or event.date
        current = event.date
        while current <= span_end:
            if start <= current <= end:
                occurrences.append(
                    Occurrence(
                        title=event.title,
                        date=current,
                        note_path=event.note_path,
                        all_day=event.all_day or event.end_date is not None,
                        start_time=event.start_time,
                        end_time=event.end_time,
                    )
                )
            current += timedelta(days=1)

    occurrences.sort(key=lambda o: (o.date, o.start_time or ""))
    return occurrences


def format_event_note(
    title: str, event_date: str, start_time: str | None, end_time: str | None
) -> str:
    """Render a Full Calendar event note. Used by the write server."""
    date.fromisoformat(event_date)  # Validate; raises ValueError on garbage.
    all_day = not start_time

    lines = ["---", f"title: {title}", f"allDay: {str(all_day).lower()}", f"date: {event_date}"]
    if start_time:
        lines.append(f'startTime: "{_coerce_time(start_time)}"')
    if end_time:
        lines.append(f'endTime: "{_coerce_time(end_time)}"')
    lines += ["---", "", "Created by the personal ops agent.", ""]
    return "\n".join(lines)
