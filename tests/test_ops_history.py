"""The fast path records its writes, and `ops` can show what it recorded.

Driven through `ops.main` with the console captured, so these read the same
output a person would.
"""

from __future__ import annotations

import io
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from rich.console import Console

import history

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import ops

DAILY = "Schedules/2026-08-17.md"


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
    """Anything ops cannot parse falls back to a real model. Never here."""
    def refuse(question):
        pytest.fail(f"ops fell back to the agent for {question!r}")

    monkeypatch.setattr(ops, "run_agent", refuse)


@pytest.fixture
def screen(monkeypatch) -> io.StringIO:
    out = io.StringIO()
    monkeypatch.setattr(ops, "console", Console(file=out, width=200, color_system=None))
    return out


def test_done_says_how_long_it_had_been_open(dp_vault, screen):
    history.record_change(
        "created", DAILY, "Sprint planning", via="approved",
        at=datetime.now() - timedelta(days=6),
    )

    assert ops.main(["done", "sprint planning"]) == 0
    assert "it had been open 6 days" in screen.getvalue()


def test_done_without_history_says_nothing_about_age(dp_vault, screen):
    assert ops.main(["done", "sprint planning"]) == 0
    assert "open" not in screen.getvalue().replace("reopen", "")


def test_history_shows_fast_path_writes(dp_vault, screen):
    ops.main(["add", "dentist 3pm"])
    ops.main(["done", "dentist"])
    ops.main(["undone", "dentist"])
    screen.seek(0)
    screen.truncate(0)

    assert ops.main(["history", "dentist"]) == 0
    shown = screen.getvalue()
    assert "dentist" in shown
    for kind in ("created", "completed", "reopened"):
        assert kind in shown
    assert "fast path" in shown
    assert "still open" in shown.lower()


def test_removing_is_recorded(dp_vault, screen, monkeypatch):
    ops.main(["add", "gym 6pm"])
    monkeypatch.setattr(ops.console, "input", lambda *_: "y")
    ops.main(["rm", "gym"])
    screen.seek(0)
    screen.truncate(0)

    ops.main(["history", "gym"])
    assert "deleted" in screen.getvalue()


def test_history_of_something_never_recorded_is_inconclusive(dp_vault, screen):
    assert ops.main(["history", "send sarah"]) == 0
    assert "no history" in screen.getvalue().lower()
