"""move_event and delete_event.

These remove data, so the important cases here are the refusals: ambiguous
matches, non-events, and paths outside the vault.
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

DAILY = "Schedules/2026-08-17.md"


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


def _delete(actions, **kwargs):
    return json.loads(actions.delete_event(**kwargs))


def _move(actions, **kwargs):
    return json.loads(actions.move_event(**kwargs))


# --- delete: Day Planner ---------------------------------------------------


def test_delete_removes_only_the_matching_line(actions, dp_vault):
    result = _delete(actions, note_path=DAILY, title="Sprint planning", start_time="10:00")
    assert result["deleted"] is True
    assert "Sprint planning" in result["removed_line"]

    text = (dp_vault / DAILY).read_text()
    assert "Sprint planning" not in text
    # Neighbours untouched.
    assert "Standup" in text
    assert "Write the retro notes" in text
    assert "Buy milk" in text


def test_delete_refuses_when_ambiguous(actions, dp_vault):
    """Two entries, no disambiguator — refusing beats guessing."""
    note = dp_vault / DAILY
    note.write_text(
        "# Day planner\n\n- [ ] 09:00 - 09:30 Sync\n- [ ] 11:00 - 11:30 Sync\n"
    )
    result = _delete(actions, note_path=DAILY, title="Sync")
    assert "error" in result
    assert "2 entries" in result["error"]
    assert note.read_text().count("Sync") == 2, "nothing may be removed on ambiguity"


def test_ambiguity_resolved_by_start_time(actions, dp_vault):
    note = dp_vault / DAILY
    note.write_text(
        "# Day planner\n\n- [ ] 09:00 - 09:30 Sync\n- [ ] 11:00 - 11:30 Sync\n"
    )
    result = _delete(actions, note_path=DAILY, title="Sync", start_time="11:00")
    assert result["deleted"] is True
    assert "09:00" in note.read_text()
    assert "11:00" not in note.read_text()


def test_delete_reports_options_when_nothing_matches(actions, dp_vault):
    result = _delete(actions, note_path=DAILY, title="Nonexistent thing")
    assert "error" in result
    # The error lists what is actually there, so the model can retry sensibly.
    assert "Sprint planning" in result["error"]


def test_delete_refuses_path_outside_vault(actions, dp_vault):
    result = _delete(actions, note_path="../../../etc/passwd")
    assert "error" in result
    assert "outside the vault" in result["error"]


def test_delete_of_note_without_entries(actions, dp_vault):
    result = _delete(actions, note_path="Schedules/Daily.md")
    assert "error" in result
    assert "No Day Planner entries" in result["error"]


# --- delete: Full Calendar -------------------------------------------------


def test_delete_removes_full_calendar_note(actions, fc_vault):
    target = "Events/2026-08-20 Design Review.md"
    result = _delete(actions, note_path=target)
    assert result["deleted"] is True
    assert not (fc_vault / target).exists()
    assert "title: Design Review" in result["removed_content"]


def test_delete_refuses_non_event_note(actions, fc_vault):
    """A project note is not an event; deleting it would be data loss."""
    result = _delete(actions, note_path="Projects/Checkout Redesign.md")
    assert "error" in result
    assert "not a calendar event" in result["error"]
    assert (fc_vault / "Projects" / "Checkout Redesign.md").exists()


# --- move ------------------------------------------------------------------


def test_move_to_another_day_preserves_duration(actions, dp_vault):
    result = _move(
        actions, note_path=DAILY, title="Sprint planning", start_time="10:00",
        new_date="2026-08-19",
    )
    assert result["moved"] is True
    assert result["to"]["start_time"] == "10:00"
    assert result["to"]["end_time"] == "11:00", "a 1h event stays 1h"

    assert "Sprint planning" not in (dp_vault / DAILY).read_text()
    assert "- [ ] 10:00 - 11:00 Sprint planning" in (
        dp_vault / "Schedules" / "2026-08-19.md"
    ).read_text()


def test_move_to_new_time_same_day(actions, dp_vault):
    result = _move(
        actions, note_path=DAILY, title="Sprint planning", start_time="10:00",
        new_start_time="16:00",
    )
    assert result["moved"] is True
    text = (dp_vault / DAILY).read_text()
    assert "- [ ] 16:00 - 17:00 Sprint planning" in text
    assert "- [ ] 10:00 - 11:00 Sprint planning" not in text


def test_move_with_explicit_new_end_time(actions, dp_vault):
    result = _move(
        actions, note_path=DAILY, title="Sprint planning", start_time="10:00",
        new_start_time="13:00", new_end_time="13:15",
    )
    assert result["to"]["end_time"] == "13:15"


def test_move_leaves_exactly_one_copy(actions, dp_vault):
    """The failure mode this replaces: ending up with the event in both places."""
    _move(actions, note_path=DAILY, title="Standup", start_time="09:15",
          new_date="2026-08-19")
    total = sum(
        note.read_text().count("Standup")
        for note in (dp_vault / "Schedules").glob("*.md")
    )
    assert total == 1


def test_move_requires_a_destination(actions, dp_vault):
    result = _move(actions, note_path=DAILY, title="Sprint planning")
    assert "error" in result
    assert "at least one of" in result["error"]


def test_move_rejects_bad_date(actions, dp_vault):
    result = _move(actions, note_path=DAILY, title="Sprint planning", new_date="monday")
    assert "error" in result
    assert "YYYY-MM-DD" in result["error"]


def test_move_rejects_inverted_times(actions, dp_vault):
    result = _move(
        actions, note_path=DAILY, title="Sprint planning", start_time="10:00",
        new_start_time="14:00", new_end_time="13:00",
    )
    assert "error" in result
    assert "not after" in result["error"]


def test_move_refuses_when_ambiguous(actions, dp_vault):
    note = dp_vault / DAILY
    note.write_text("# Day planner\n\n- [ ] 09:00 - 09:30 Sync\n- [ ] 11:00 - 11:30 Sync\n")
    result = _move(actions, note_path=DAILY, title="Sync", new_date="2026-08-19")
    assert "error" in result
    assert not (dp_vault / "Schedules" / "2026-08-19.md").exists()


def test_move_unsupported_on_full_calendar(actions, fc_vault):
    result = _move(
        actions, note_path="Events/2026-08-20 Design Review.md", new_date="2026-08-19"
    )
    assert "error" in result
    assert "Day Planner" in result["error"]


def test_delete_matches_decorated_title(actions, dp_vault):
    """move/delete take a title too, and the model decorates that the same way."""
    result = _delete(actions, note_path=DAILY, title="Sprint planning — 10:00", start_time="10:00")
    assert result.get("deleted") is True, result
    assert "Sprint planning" not in (dp_vault / DAILY).read_text()


def test_decorated_title_still_refuses_when_ambiguous(actions, dp_vault):
    note = dp_vault / DAILY
    note.write_text("# Day planner\n\n- [ ] 09:00 - 09:30 Sync\n- [ ] 11:00 - 11:30 Sync\n")
    result = _delete(actions, note_path=DAILY, title="Sync — today")
    assert "error" in result
    assert note.read_text().count("Sync") == 2


def _structure(text):
    """Non-empty lines with their kind, for asserting note shape survives edits."""
    return [
        ("task" if l.strip().startswith("- [") else "heading" if l.startswith("#") else "text")
        for l in text.splitlines() if l.strip()
    ]


def test_move_keeps_the_note_tidy(actions, dp_vault):
    """A live run left a hole in the list and welded ## Notes to the entry above."""
    note = dp_vault / DAILY
    note.write_text(
        "# Day planner\n\n"
        "- [ ] 09:00 - 09:15 Standup\n"
        "- [ ] 10:00 - 12:00 Deep work\n"
        "- [ ] 15:00 - 15:30 Priya\n"
        "\n"
        "## Notes\n\n"
        "Something here.\n"
    )
    _move(actions, note_path=DAILY, title="Priya", start_time="15:00", new_start_time="16:00")
    text = note.read_text()
    lines = text.splitlines()

    assert "- [ ] 16:00 - 16:30 Priya" in text
    assert text.count("Priya") == 1

    # No blank line inside the planner list.
    task_indexes = [i for i, l in enumerate(lines) if l.strip().startswith("- [")]
    span = lines[task_indexes[0]: task_indexes[-1] + 1]
    assert all(l.strip() for l in span), f"blank line inside the list: {span}"

    # The heading keeps a blank line above it.
    notes_index = next(i for i, l in enumerate(lines) if l.strip() == "## Notes")
    assert not lines[notes_index - 1].strip(), "## Notes lost its blank line"

    assert _structure(text) == ["heading", "task", "task", "task", "heading", "text"]


def test_repeated_moves_do_not_accumulate_blank_lines(actions, dp_vault):
    note = dp_vault / DAILY
    note.write_text(
        "# Day planner\n\n- [ ] 09:00 - 09:30 Thing\n\n## Notes\n\nBody.\n"
    )
    for new_time in ("10:00", "11:00", "12:00"):
        _move(actions, note_path=DAILY, title="Thing", new_start_time=new_time)
    text = note.read_text()
    assert text.count("Thing") == 1
    assert "\n\n\n" not in text, f"blank lines accumulated:\n{text!r}"
    assert _structure(text) == ["heading", "task", "heading", "text"]
