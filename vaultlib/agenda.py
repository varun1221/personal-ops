"""What is on you today: overdue debt, and work due but not yet timed.

Pure selection over the parsing in `tasks` and `dayplanner`. Nothing here
renders — `ops` decides how the result looks — so what counts as overdue is
testable without a terminal.

The vault distinguishes two kinds of open checkbox. A Day Planner entry carries
a time and already appears in the day view as a block; a plain Tasks-plugin line
carries a due date and, until now, was visible only in Obsidian. This module
selects the second kind.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from vaultlib.dayplanner import PlannerConfig, split_time_block
from vaultlib.paths import VaultPaths
from vaultlib.planner_write import _bare_description, description_matches
from vaultlib.tasks import Task, iter_tasks, parse_task_line

# Overdue is debt, not a plan. Past three lines it stops being glanceable, so
# the rest is counted rather than listed — hidden would mean never closed.
OVERDUE_SHOWN = 3


@dataclass(frozen=True)
class Agenda:
    """The untimed work attached to one day."""

    overdue: list[Task]
    due: list[Task]

    @property
    def is_empty(self) -> bool:
        return not self.overdue and not self.due


def is_planner_entry(task: Task, default_duration_minutes: int) -> bool:
    """True when a checkbox is a Day Planner time block rather than a to-do.

    These already appear in the day view as timed blocks, so counting them as
    tasks as well would print the same line twice.
    """
    return split_time_block(task.description, default_duration_minutes) is not None


def collect_open_tasks(
    paths: VaultPaths, config: PlannerConfig | None = None
) -> list[Task]:
    """Every open, untimed task in the vault, soonest due date first.

    Obsidian's config folder and the templates folder are already excluded by
    `markdown_files`. Undated tasks sort last: a task with no date is a backlog
    item, not a deadline.
    """
    config = config or PlannerConfig.from_vault(paths.root)
    collected: list[Task] = []
    for path in paths.markdown_files():
        for task in iter_tasks(path, paths.relative(path)):
            # A bare "- [ ]" is an empty slot in the editor, not a task.
            if not task.description.strip() or not task.is_open:
                continue
            if is_planner_entry(task, config.default_duration_minutes):
                continue
            collected.append(task)
    collected.sort(
        key=lambda t: (t.due is None, t.due or date.min, t.description.lower())
    )
    return collected


def split_agenda(tasks: list[Task], today: date) -> Agenda:
    """Divide dated tasks into what is late and what is due today.

    Undated tasks belong to neither. A task with no due date is not late, and
    treating it as late turns a backlog into a permanent alarm.
    """
    overdue = [t for t in tasks if t.due is not None and t.due < today]
    due = [t for t in tasks if t.due == today]

    # Most recently missed first: the oldest debt is the least actionable, and
    # it is the one that gets collapsed away.
    overdue.sort(key=lambda t: t.description.lower())
    overdue.sort(key=lambda t: t.due, reverse=True)
    due.sort(key=lambda t: t.description.lower())
    return Agenda(overdue=overdue, due=due)


def collapse_overdue(
    overdue: list[Task], limit: int = OVERDUE_SHOWN
) -> tuple[list[Task], int]:
    """The overdue lines to print, and how many are being withheld."""
    limit = max(0, limit)
    return overdue[:limit], max(0, len(overdue) - limit)


def locate_open_task(
    paths: VaultPaths, text: str, config: PlannerConfig | None = None
) -> tuple[Path, int, Task] | dict:
    """Find exactly one open task anywhere in the vault, refusing on ambiguity.

    The day view shows tasks from notes other than today's, so ticking one off
    has to search where they actually live. Restricting the search to *open*
    tasks is what keeps it usable: a "standup" finished three months ago cannot
    collide with today's.

    Returns `(path, line_index, task)` or an `{"error": ...}` dict naming every
    match, on the same principle as `move_event` and `delete_event` — a wrong
    edit here is not something the user can undo from the terminal.
    """
    config = config or PlannerConfig.from_vault(paths.root)
    needle = _bare_description(text, config.default_duration_minutes)

    candidates: list[tuple[Path, int, Task, str]] = []
    for path in paths.markdown_files():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        note_path = paths.relative(path)
        for index, line in enumerate(lines):
            task = parse_task_line(line, note_path=note_path, line_number=index + 1)
            if task is None or not task.description.strip() or not task.is_open:
                continue
            description = task.description
            if block := split_time_block(description, config.default_duration_minutes):
                _, _, description = block
            description = description.strip()
            if description_matches(needle, description.lower()):
                candidates.append((path, index, task, description))

    if not candidates:
        return {"error": f"Nothing open in the vault matches {text!r}."}

    # Prefer an exact match when one exists.
    exact = [c for c in candidates if c[3].lower() == needle]
    candidates = exact or candidates
    if len(candidates) > 1:
        where = ", ".join(
            f"{c[3]!r} in {paths.relative(c[0])}" for c in candidates
        )
        return {
            "error": f"{len(candidates)} open tasks match {text!r}. "
            f"Narrow it down. Matches: {where}"
        }

    path, index, task, _ = candidates[0]
    return path, index, task
