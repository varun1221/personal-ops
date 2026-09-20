"""Obsidian Day Planner plugin support.

Day Planner is a third calendar format alongside Full Calendar and the Tasks
plugin, and it works differently from both: an entry is a checkbox line carrying
a time range, living in a daily note, under a configured heading.

    # Day planner

    - [ ] 06:10 - 06:20 Work
    - [x] 09:00 - 09:30 Standup
    - [ ] 14:00 Design review          <- no end time; default duration applies

The date is not in the line — it comes from the daily note's filename. So a
planner entry is simultaneously a task (it has a checkbox) and a calendar event
(it has a time on a date), and this module exposes it as both.

Plugin settings are read from the vault's own .obsidian config where present, so
the parser follows the user's configuration rather than assuming defaults.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from vaultlib.tasks import STATUS, TASK_LINE, _normalize

PLUGIN_ID = "obsidian-day-planner"

# "06:10 - 06:20 Work", tolerating en/em dashes, "to", and missing zero padding.
TIME_RANGE = re.compile(
    r"^(?P<sh>\d{1,2}):(?P<sm>\d{2})\s*(?:-|–|—|to)\s*(?P<eh>\d{1,2}):(?P<em>\d{2})\s*(?P<rest>.*)$"
)
# "14:00 Design review" — start only.
TIME_START = re.compile(r"^(?P<sh>\d{1,2}):(?P<sm>\d{2})\s+(?P<rest>.+)$")

DEFAULT_HEADING = "Day planner"
DEFAULT_DURATION_MINUTES = 30


@dataclass(frozen=True)
class PlannerConfig:
    heading: str = DEFAULT_HEADING
    heading_level: int = 1
    default_duration_minutes: int = DEFAULT_DURATION_MINUTES
    installed: bool = False

    @classmethod
    def from_vault(cls, vault_root: Path) -> "PlannerConfig":
        """Read the plugin's own settings, falling back to its defaults."""
        plugin_dir = vault_root / ".obsidian" / "plugins" / PLUGIN_ID
        if not plugin_dir.is_dir():
            return cls(installed=False)

        heading, level, duration = DEFAULT_HEADING, 1, DEFAULT_DURATION_MINUTES
        try:
            data = json.loads((plugin_dir / "data.json").read_text(encoding="utf-8"))
            heading = data.get("plannerHeading") or heading
            level = int(data.get("plannerHeadingLevel") or level)
            duration = int(data.get("defaultDurationMinutes") or duration)
        except (OSError, ValueError, TypeError):
            pass  # Plugin present but unconfigured: its defaults are correct.
        return cls(
            heading=heading,
            heading_level=max(1, min(level, 6)),
            default_duration_minutes=duration,
            installed=True,
        )

    @property
    def heading_markdown(self) -> str:
        return f"{'#' * self.heading_level} {self.heading}"


def daily_notes_folder(vault_root: Path) -> str | None:
    """The folder Obsidian's daily-notes plugin is pointed at, if configured."""
    try:
        data = json.loads(
            (vault_root / ".obsidian" / "daily-notes.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    folder = data.get("folder")
    return folder.strip("/") if isinstance(folder, str) and folder.strip("/") else None


def note_date(path: Path) -> date | None:
    """A daily note's date comes from its filename, e.g. 2026-07-04.md."""
    try:
        return datetime.strptime(path.stem, "%Y-%m-%d").date()
    except ValueError:
        return None


@dataclass
class PlannerEntry:
    description: str
    start_time: str
    end_time: str
    status: str
    date: date
    note_path: str
    line_number: int

    def to_event_dict(self) -> dict:
        return {
            "title": self.description,
            "date": self.date.isoformat(),
            "day_of_week": self.date.strftime("%A"),
            "all_day": False,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "recurring": False,
            "note_path": self.note_path,
            "source": "day-planner",
            "status": self.status,
        }


def split_time_block(text: str, default_duration_minutes: int) -> tuple[str, str, str] | None:
    """Split "06:10 - 06:20 Work" into (start, end, description).

    Returns None when the text carries no leading time, which is how an ordinary
    checkbox is told apart from a planner entry.
    """
    text = text.strip()

    if match := TIME_RANGE.match(text):
        start = _clamp(int(match.group("sh")), int(match.group("sm")))
        end = _clamp(int(match.group("eh")), int(match.group("em")))
        if start is None or end is None:
            return None
        return start, end, match.group("rest").strip()

    if match := TIME_START.match(text):
        start = _clamp(int(match.group("sh")), int(match.group("sm")))
        if start is None:
            return None
        hours, minutes = divmod(
            int(start[:2]) * 60 + int(start[3:]) + default_duration_minutes, 60
        )
        return start, f"{hours % 24:02d}:{minutes:02d}", match.group("rest").strip()

    return None


def _clamp(hours: int, minutes: int) -> str | None:
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        return None
    return f"{hours:02d}:{minutes:02d}"


def parse_planner_note(
    path: Path, note_path: str, config: PlannerConfig, entry_date: date | None = None
) -> list[PlannerEntry]:
    """Extract planner entries from one daily note.

    Entries are taken from the planner heading's section when that heading is
    present, and from the whole note otherwise — Day Planner itself tolerates
    both, and a note that has entries but lost its heading should still parse.
    """
    entry_date = entry_date or note_date(path)
    if entry_date is None:
        return []

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []

    heading = config.heading.strip().lower()
    heading_lines = [
        index
        for index, line in enumerate(lines)
        if line.lstrip().startswith("#") and line.lstrip("#").strip().lower() == heading
    ]

    if heading_lines:
        start = heading_lines[0] + 1
        level = len(lines[heading_lines[0]]) - len(lines[heading_lines[0]].lstrip("#"))
        end = len(lines)
        for index in range(start, len(lines)):
            stripped = lines[index].lstrip()
            if stripped.startswith("#"):
                this_level = len(stripped) - len(stripped.lstrip("#"))
                if this_level <= level:
                    end = index
                    break
        window = range(start, end)
    else:
        window = range(len(lines))

    entries = []
    for index in window:
        match = TASK_LINE.match(_normalize(lines[index]))
        if not match:
            continue
        status = STATUS.get(match.group("status"))
        if status is None:
            continue
        block = split_time_block(match.group("body"), config.default_duration_minutes)
        if block is None:
            continue  # A checkbox with no time is a plain task, not a planner entry.
        start_time, end_time, description = block
        if not description:
            continue  # "- [ ] 06:10 - 06:20" with no text is an empty slot.
        entries.append(
            PlannerEntry(
                description=description,
                start_time=start_time,
                end_time=end_time,
                status=status,
                date=entry_date,
                note_path=note_path,
                line_number=index + 1,
            )
        )
    return entries


def format_planner_line(description: str, start_time: str, end_time: str) -> str:
    return f"- [ ] {start_time} - {end_time} {description.strip()}"


def match_entries(
    entries: list[PlannerEntry], title: str | None = None, start_time: str | None = None
) -> list[PlannerEntry]:
    """Narrow entries by title and/or start time.

    Title matching is case-insensitive and accepts a substring, because the model
    is quoting a title back from a listing and may not reproduce it exactly.
    """
    matched = entries
    if start_time:
        padded = f"{int(start_time.split(':')[0]):02d}:{start_time.split(':')[1]}"
        matched = [entry for entry in matched if entry.start_time == padded]
    if title:
        needle = title.strip().lower()
        exact = [entry for entry in matched if entry.description.strip().lower() == needle]
        # Prefer an exact title match; otherwise match in both directions, since
        # the model may quote a title back with the date or time appended.
        matched = exact or [
            entry
            for entry in matched
            if needle in entry.description.lower()
            or entry.description.strip().lower() in needle
        ]
    return matched


def add_minutes(clock: str, minutes: int) -> str:
    moment = datetime.strptime(clock, "%H:%M") + timedelta(minutes=minutes)
    return moment.strftime("%H:%M")
