"""`ops rm` and `ops undone` act on the day you name, not always today.

Driven through `ops.main`, so the argument splitting is what is under test.
"""

from __future__ import annotations

import io
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
from rich.console import Console

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import ops

TODAY = date.today()


@pytest.fixture
def dp_vault(tmp_path, monkeypatch):
    vault = tmp_path / "dp"
    shutil.copytree(PROJECT_ROOT / "fixtures" / "dayplanner_vault", vault)
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
    monkeypatch.delenv("OBSIDIAN_DAILY_FOLDER", raising=False)
    monkeypatch.delenv("OBSIDIAN_CALENDAR_FORMAT", raising=False)
    return vault


@pytest.fixture(autouse=True)
def no_agent(monkeypatch):
    def refuse(question):
        pytest.fail(f"ops fell back to the agent for {question!r}")

    monkeypatch.setattr(ops, "run_agent", refuse)


@pytest.fixture
def screen(monkeypatch) -> io.StringIO:
    out = io.StringIO()
    console = Console(file=out, width=200, color_system=None)
    monkeypatch.setattr(console, "input", lambda *_: "y")
    monkeypatch.setattr(ops, "console", console)
    return out


def note(vault: Path, day: date, body: str) -> Path:
    path = vault / "Schedules" / f"{day.isoformat()}.md"
    path.write_text(f"# Day planner\n\n{body}\n", encoding="utf-8")
    return path


def test_rm_removes_the_named_days_entry(dp_vault, screen):
    today = note(dp_vault, TODAY, "- [ ] 18:00 - 19:00 Gym")
    tomorrow = note(dp_vault, TODAY + timedelta(days=1), "- [ ] 18:00 - 19:00 Gym")

    assert ops.main(["rm", "gym", "tomorrow"]) == 0
    assert "Gym" not in tomorrow.read_text()
    assert "Gym" in today.read_text()


def test_rm_accepts_an_iso_date(dp_vault, screen):
    day = TODAY + timedelta(days=3)
    planned = note(dp_vault, day, "- [ ] 18:00 - 19:00 Gym")

    assert ops.main(["rm", "gym", day.isoformat()]) == 0
    assert "Gym" not in planned.read_text()


def test_undone_reopens_the_named_days_task(dp_vault, screen):
    yesterday = note(dp_vault, TODAY - timedelta(days=1), "- [x] 09:15 - 09:30 Standup")

    assert ops.main(["undone", "standup", "yesterday"]) == 0
    assert "- [ ] 09:15 - 09:30 Standup" in yesterday.read_text()


def test_a_quoted_title_keeps_its_day_word(dp_vault, screen):
    planned = note(dp_vault, TODAY, "- [ ] 17:00 - 17:30 Prep for tomorrow")

    assert ops.main(["rm", "prep for tomorrow"]) == 0
    assert "Prep for tomorrow" not in planned.read_text()


def test_a_day_word_alone_is_the_title(dp_vault, screen):
    planned = note(dp_vault, TODAY, "- [ ] 17:00 - 17:30 Tomorrow")

    assert ops.main(["rm", "tomorrow"]) == 0
    assert "Tomorrow" not in planned.read_text()
