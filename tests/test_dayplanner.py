"""Day Planner parsing.

Written after discovering the real vault uses Day Planner rather than Full
Calendar or the Tasks plugin — a checkbox line carrying a time range, inside a
daily note, under a configured heading.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from vaultlib.dayplanner import (
    PlannerConfig,
    add_minutes,
    daily_notes_folder,
    format_planner_line,
    note_date,
    parse_planner_note,
    split_time_block,
)
from vaultlib.paths import VaultPaths

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DP_VAULT = PROJECT_ROOT / "fixtures" / "dayplanner_vault"


@pytest.fixture
def config() -> PlannerConfig:
    return PlannerConfig.from_vault(DP_VAULT)


def test_config_read_from_vault(config):
    assert config.installed is True
    assert config.heading == "Day planner"
    assert config.heading_level == 1
    assert config.default_duration_minutes == 30
    assert config.heading_markdown == "# Day planner"


def test_config_absent_when_plugin_not_installed():
    assert PlannerConfig.from_vault(PROJECT_ROOT / "fixtures" / "vault").installed is False


def test_daily_notes_folder_read_from_obsidian_config():
    assert daily_notes_folder(DP_VAULT) == "Schedules"
    assert daily_notes_folder(PROJECT_ROOT / "fixtures" / "vault") is None


def test_vault_paths_picks_up_configured_daily_folder(monkeypatch):
    """The real vault uses "Schedules", not the "Daily" default."""
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(DP_VAULT))
    monkeypatch.delenv("OBSIDIAN_DAILY_FOLDER", raising=False)
    assert VaultPaths.from_env().daily_folder == "Schedules"


def test_note_date_from_filename():
    assert note_date(Path("Schedules/2026-08-17.md")) == date(2026, 8, 17)
    assert note_date(Path("Schedules/Daily.md")) is None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("06:10 - 06:20 Work", ("06:10", "06:20", "Work")),
        ("6:10 - 6:20 Unpadded", ("06:10", "06:20", "Unpadded")),
        ("15:30 – 16:00 En dash", ("15:30", "16:00", "En dash")),
        ("15:30 — 16:00 Em dash", ("15:30", "16:00", "Em dash")),
        ("09:00 to 09:45 Worded", ("09:00", "09:45", "Worded")),
        ("14:00 Start only", ("14:00", "14:30", "Start only")),
    ],
)
def test_split_time_block(text, expected):
    assert split_time_block(text, 30) == expected


@pytest.mark.parametrize("text", ["Buy milk", "", "Meeting at 3", "99:99 - 10:00 Bad hour"])
def test_split_time_block_rejects_non_planner_text(text):
    assert split_time_block(text, 30) is None


def test_default_duration_applies_to_start_only_entries():
    assert split_time_block("14:00 Thing", 45) == ("14:00", "14:45", "Thing")


def test_parses_entries_from_planner_section(config):
    entries = parse_planner_note(DP_VAULT / "Schedules/2026-08-17.md", "x.md", config)
    assert [e.description for e in entries] == [
        "Standup",
        "Sprint planning",
        "Write the retro notes",
    ]


def test_ignores_checkboxes_outside_the_planner_heading(config):
    """"Buy milk" sits under ## Notes and has no time — it is a task, not a block."""
    entries = parse_planner_note(DP_VAULT / "Schedules/2026-08-17.md", "x.md", config)
    assert "Buy milk" not in [e.description for e in entries]


def test_status_is_captured(config):
    entries = parse_planner_note(DP_VAULT / "Schedules/2026-08-17.md", "x.md", config)
    assert entries[0].status == "done"
    assert entries[1].status == "todo"


def test_date_comes_from_the_filename(config):
    entries = parse_planner_note(DP_VAULT / "Schedules/2026-08-17.md", "x.md", config)
    assert all(e.date == date(2026, 8, 17) for e in entries)


def test_time_format_variants_all_parse(config):
    """Entries come back in file order, not sorted."""
    entries = parse_planner_note(DP_VAULT / "Schedules/2026-08-20.md", "y.md", config)
    assert [(e.start_time, e.end_time) for e in entries] == [
        ("06:10", "06:20"),
        ("15:30", "16:00"),
        ("09:00", "09:45"),
    ]


def test_empty_checkbox_is_skipped(config):
    """The real vault contains a bare "- [ ]" line."""
    entries = parse_planner_note(DP_VAULT / "Schedules/2026-08-20.md", "y.md", config)
    assert all(e.description for e in entries)


def test_to_event_dict_shape(config):
    entry = parse_planner_note(DP_VAULT / "Schedules/2026-08-17.md", "x.md", config)[1]
    payload = entry.to_event_dict()
    assert payload["title"] == "Sprint planning"
    assert payload["date"] == "2026-08-17"
    assert payload["day_of_week"] == "Monday"
    assert payload["source"] == "day-planner"
    assert payload["all_day"] is False


def test_missing_note_returns_nothing(config, tmp_path):
    assert parse_planner_note(tmp_path / "2026-08-17.md", "z.md", config) == []


def test_format_round_trip(config):
    line = format_planner_line("Spec writing", "13:00", "15:00")
    assert line == "- [ ] 13:00 - 15:00 Spec writing"
    assert split_time_block(line.split("] ", 1)[1], 30) == ("13:00", "15:00", "Spec writing")


@pytest.mark.parametrize(
    "clock,minutes,expected",
    [("09:00", 30, "09:30"), ("23:45", 30, "00:15"), ("06:10", 10, "06:20")],
)
def test_add_minutes(clock, minutes, expected):
    assert add_minutes(clock, minutes) == expected
