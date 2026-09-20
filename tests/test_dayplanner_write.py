"""Writing Day Planner blocks.

A Full Calendar note written into a Day Planner vault renders nowhere, so the
write path has to follow the vault's format rather than a default.
"""

from __future__ import annotations

import importlib
import json
import shutil
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def dp_vault(tmp_path, monkeypatch):
    vault = tmp_path / "dp"
    shutil.copytree(PROJECT_ROOT / "fixtures" / "dayplanner_vault", vault)
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
    monkeypatch.delenv("OBSIDIAN_DAILY_FOLDER", raising=False)
    monkeypatch.delenv("OBSIDIAN_CALENDAR_FORMAT", raising=False)
    return vault


@pytest.fixture
def fc_vault(tmp_path, monkeypatch):
    vault = tmp_path / "fc"
    shutil.copytree(PROJECT_ROOT / "fixtures" / "vault", vault)
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
    monkeypatch.delenv("OBSIDIAN_CALENDAR_FORMAT", raising=False)
    return vault


@pytest.fixture
def actions():
    return importlib.import_module("servers.actions.server")


def _call(actions, **kwargs):
    return json.loads(actions.create_calendar_event(**kwargs))


def test_format_detected_from_installed_plugins(actions, dp_vault, fc_vault, monkeypatch):
    from vaultlib.paths import VaultPaths

    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(dp_vault))
    assert actions.calendar_format(VaultPaths.from_env()) == "day_planner"

    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(fc_vault))
    assert actions.calendar_format(VaultPaths.from_env()) == "full_calendar"


def test_env_overrides_detection(actions, dp_vault, monkeypatch):
    from vaultlib.paths import VaultPaths

    monkeypatch.setenv("OBSIDIAN_CALENDAR_FORMAT", "full_calendar")
    assert actions.calendar_format(VaultPaths.from_env()) == "full_calendar"


def test_writes_planner_line_into_daily_note(actions, dp_vault):
    result = _call(
        actions, title="Spec writing", date="2026-08-17", start_time="13:00", end_time="15:00"
    )
    assert result["created"] is True
    assert result["format"] == "day-planner"
    assert result["note_path"] == "Schedules/2026-08-17.md"

    text = (dp_vault / "Schedules" / "2026-08-17.md").read_text()
    assert "- [ ] 13:00 - 15:00 Spec writing" in text
    # No stray Full Calendar note.
    assert not (dp_vault / "Events").exists()


def test_inserted_in_time_order(actions, dp_vault):
    _call(actions, title="Mid morning", date="2026-08-17", start_time="09:45", end_time="10:00")
    lines = (dp_vault / "Schedules" / "2026-08-17.md").read_text().splitlines()
    times = [line.split("] ")[1].split(" -")[0] for line in lines if line.startswith("- [")]
    assert times == sorted(times), f"planner entries should stay ordered, got {times}"


def test_default_duration_when_end_time_omitted(actions, dp_vault):
    result = _call(actions, title="Quick call", date="2026-08-17", start_time="16:00")
    assert result["end_time"] == "16:30"


def test_all_day_rejected_with_actionable_message(actions, dp_vault):
    """Day Planner has no all-day concept; the error must tell the model what to do."""
    result = _call(actions, title="Offsite", date="2026-08-17")
    assert "error" in result
    assert "start_time" in result["error"]
    assert "created" not in result


def test_duplicate_block_refused(actions, dp_vault):
    first = _call(actions, title="Standup", date="2026-08-17", start_time="09:15", end_time="09:30")
    assert "error" in first, "an identical block already exists in the fixture"
    assert "already planned" in first["error"]


def test_creates_daily_note_when_absent(actions, dp_vault):
    result = _call(
        actions, title="New day", date="2026-09-01", start_time="08:00", end_time="08:30"
    )
    assert result["created"] is True
    assert result["created_note"] is True
    text = (dp_vault / "Schedules" / "2026-09-01.md").read_text()
    assert "# Day planner" in text
    assert "- [ ] 08:00 - 08:30 New day" in text


def test_existing_content_preserved(actions, dp_vault):
    before = (dp_vault / "Schedules" / "2026-08-17.md").read_text()
    _call(actions, title="Spec writing", date="2026-08-17", start_time="13:00", end_time="15:00")
    after = (dp_vault / "Schedules" / "2026-08-17.md").read_text()
    for line in before.splitlines():
        if line.strip():
            assert line in after, f"lost a line: {line!r}"


def test_full_calendar_vault_still_writes_notes(actions, fc_vault):
    """The original format must keep working; this is additive, not a replacement."""
    result = _call(
        actions, title="Spec writing", date="2026-08-28", start_time="13:00", end_time="15:00"
    )
    assert result["created"] is True
    assert result.get("format") != "day-planner"
    assert (fc_vault / "Events" / "2026-08-28 Spec writing.md").exists()
