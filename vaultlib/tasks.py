"""Obsidian Tasks plugin — emoji format parsing.

Signifiers follow the official reference:
https://publish.obsidian.md/tasks/Reference/Task+Formats/Tasks+Emoji+Format

The fiddly part is not the emoji themselves but what surrounds them. Copying task
lines between apps routinely introduces U+FE0F variation selectors and non-breaking
spaces, which make a naive `"📅" in line` check fail on lines that look identical
in the editor. `_normalize` strips both before any matching happens.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

# Codepoints spelled out — several of these are visually identical to
# near-neighbours and easy to mistype.
DUE = "\U0001f4c5"  # 📅
SCHEDULED = "⏳"  # ⏳
START = "\U0001f6eb"  # 🛫
CREATED = "➕"  # ➕
DONE = "✅"  # ✅
CANCELLED = "❌"  # ❌
RECURRENCE = "\U0001f501"  # 🔁
ID = "\U0001f194"  # 🆔
DEPENDS_ON = "⛔"  # ⛔
ON_COMPLETION = "\U0001f3c1"  # 🏁

PRIORITIES = {
    "\U0001f53a": "highest",  # 🔺
    "⏫": "high",  # ⏫
    "\U0001f53c": "medium",  # 🔼
    "\U0001f53d": "low",  # 🔽
    "⏬": "lowest",  # ⏬
}

DATE_FIELDS = {
    DUE: "due",
    SCHEDULED: "scheduled",
    START: "start",
    CREATED: "created",
    DONE: "done",
    CANCELLED: "cancelled",
}

# Status characters inside the checkbox. Obsidian themes support many more, but
# these four carry the meanings the Tasks plugin itself acts on.
STATUS = {" ": "todo", "x": "done", "X": "done", "/": "in_progress", "-": "cancelled"}

TASK_LINE = re.compile(r"^(?P<indent>\s*)[-*+]\s+\[(?P<status>.)\]\s+(?P<body>.*)$")

_DATE_AFTER = re.compile(r"\s*(\d{4}-\d{2}-\d{2})")
# Text signifiers (recurrence, ids) run until the next signifier or end of line.
_ALL_SIGNIFIERS = "".join(
    [*DATE_FIELDS, *PRIORITIES, RECURRENCE, ID, DEPENDS_ON, ON_COMPLETION]
)
_TEXT_AFTER = re.compile(rf"\s*([^{re.escape(_ALL_SIGNIFIERS)}]*)")


def _normalize(text: str) -> str:
    """Strip the invisible characters that break emoji matching."""
    return text.replace("️", "").replace("︎", "").replace(" ", " ")


@dataclass
class Task:
    description: str
    status: str
    raw: str
    note_path: str
    line_number: int
    priority: str | None = None
    recurrence: str | None = None
    task_id: str | None = None
    depends_on: list[str] = field(default_factory=list)
    dates: dict[str, date] = field(default_factory=dict)

    @property
    def due(self) -> date | None:
        return self.dates.get("due")

    @property
    def is_open(self) -> bool:
        return self.status in ("todo", "in_progress")

    def to_dict(self) -> dict:
        return {
            "description": self.description,
            "status": self.status,
            "priority": self.priority,
            "recurrence": self.recurrence,
            "note_path": self.note_path,
            "line_number": self.line_number,
            **{name: value.isoformat() for name, value in self.dates.items()},
            **({"task_id": self.task_id} if self.task_id else {}),
            **({"depends_on": self.depends_on} if self.depends_on else {}),
        }


def parse_task_line(line: str, note_path: str = "", line_number: int = 0) -> Task | None:
    """Parse one markdown line into a Task, or return None if it isn't a task."""
    normalized = _normalize(line)
    match = TASK_LINE.match(normalized)
    if not match:
        return None

    status = STATUS.get(match.group("status"))
    if status is None:
        return None

    body = match.group("body")
    task = Task(
        description="",
        status=status,
        raw=line.rstrip("\n"),
        note_path=note_path,
        line_number=line_number,
    )

    # Walk the body left to right. Everything before the first signifier is the
    # description; each signifier consumes its own value.
    description_parts: list[str] = []
    index = 0
    while index < len(body):
        char = body[index]

        if char in DATE_FIELDS:
            date_match = _DATE_AFTER.match(body, index + 1)
            if date_match:
                try:
                    task.dates[DATE_FIELDS[char]] = date.fromisoformat(date_match.group(1))
                except ValueError:
                    pass  # Malformed date: drop the field rather than the task.
                index = date_match.end()
                continue

        elif char in PRIORITIES:
            task.priority = PRIORITIES[char]
            index += 1
            continue

        elif char in (RECURRENCE, ID, DEPENDS_ON, ON_COMPLETION):
            text_match = _TEXT_AFTER.match(body, index + 1)
            value = text_match.group(1).strip() if text_match else ""
            if char == RECURRENCE:
                task.recurrence = value or None
            elif char == ID:
                task.task_id = value or None
            elif char == DEPENDS_ON:
                task.depends_on = [part.strip() for part in value.split(",") if part.strip()]
            index = text_match.end() if text_match else index + 1
            continue

        description_parts.append(char)
        index += 1

    task.description = "".join(description_parts).strip()
    return task


def iter_tasks(path: Path, note_path: str):
    """Yield every task in a markdown file."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return
    for number, line in enumerate(lines, start=1):
        task = parse_task_line(line, note_path=note_path, line_number=number)
        if task is not None:
            yield task


CHECKBOX = re.compile(r"^(?P<head>\s*[-*+]\s+\[)(?P<marker>.)(?P<tail>\])")


def set_checkbox(line: str, marker: str) -> str:
    """Flip a task line's checkbox marker, leaving everything else byte-identical."""
    return CHECKBOX.sub(
        lambda match: f"{match.group('head')}{marker}{match.group('tail')}", line, count=1
    )


def format_task_line(
    description: str,
    due_date: str | None = None,
    priority: str | None = None,
) -> str:
    """Render a task back into Tasks-plugin emoji format."""
    parts = [f"- [ ] {description.strip()}"]
    if priority:
        symbols = {name: symbol for symbol, name in PRIORITIES.items()}
        if priority not in symbols:
            raise ValueError(
                f"Unknown priority {priority!r}; expected one of {sorted(symbols)}"
            )
        parts.append(symbols[priority])
    if due_date:
        date.fromisoformat(due_date)  # Validate; raises ValueError on garbage.
        parts.append(f"{DUE} {due_date}")
    return " ".join(parts)
