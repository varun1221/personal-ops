"""find_free_slots, week_overview, complete_task, rename_entry."""

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
def obsidian():
    return importlib.import_module("servers.obsidian.server")


@pytest.fixture
def actions():
    return importlib.import_module("servers.actions.server")


# --- find_free_slots -------------------------------------------------------


def test_free_slots_around_existing_blocks(obsidian, dp_vault):
    """2026-08-17 has 09:15-09:30, 10:00-11:00 and 14:00-14:30."""
    result = json.loads(obsidian.find_free_slots("2026-08-17", 30, "09:00", "18:00"))
    slots = [(s["start_time"], s["end_time"]) for s in result["free_slots"]]
    assert ("11:00", "14:00") in slots
    assert ("14:30", "18:00") in slots
    # The 30-minute gap between 09:30 and 10:00 exactly meets the minimum.
    assert ("09:30", "10:00") in slots


def test_free_slots_respects_minimum_duration(obsidian, dp_vault):
    result = json.loads(obsidian.find_free_slots("2026-08-17", 120, "09:00", "18:00"))
    assert all(s["minutes"] >= 120 for s in result["free_slots"])
    assert ("09:30", "10:00") not in [
        (s["start_time"], s["end_time"]) for s in result["free_slots"]
    ]


def test_free_slots_on_empty_day_is_the_whole_window(obsidian, dp_vault):
    result = json.loads(obsidian.find_free_slots("2026-08-19", 30, "09:00", "17:00"))
    assert result["free_slots"] == [
        {"start_time": "09:00", "end_time": "17:00", "minutes": 480}
    ]


def test_free_slots_reports_what_is_busy(obsidian, dp_vault):
    result = json.loads(obsidian.find_free_slots("2026-08-17", 30))
    assert [b["title"] for b in result["busy"]] == [
        "Standup",
        "Sprint planning",
        "Write the retro notes",
    ]


def test_free_slots_validates_window(obsidian, dp_vault):
    assert "error" in json.loads(obsidian.find_free_slots("2026-08-17", 30, "18:00", "09:00"))
    assert "error" in json.loads(obsidian.find_free_slots("2026-08-17", 0))
    assert "error" in json.loads(obsidian.find_free_slots("not-a-date"))


# --- week_overview ---------------------------------------------------------


def test_week_overview_snaps_to_monday(obsidian, dp_vault):
    """2026-08-20 is a Thursday; the week starts Monday the 17th."""
    result = json.loads(obsidian.week_overview("2026-08-20"))
    assert result["week_start"] == "2026-08-17"
    assert result["week_end"] == "2026-08-23"
    assert [d["day_of_week"] for d in result["days"]][0] == "Monday"
    assert len(result["days"]) == 7


def test_week_overview_counts_booked_time(obsidian, dp_vault):
    result = json.loads(obsidian.week_overview("2026-08-17"))
    monday = next(d for d in result["days"] if d["date"] == "2026-08-17")
    # 09:15-09:30 (15) + 10:00-11:00 (60) + 14:00-14:30 (30)
    assert monday["booked_minutes"] == 105
    assert monday["event_count"] == 3


def test_week_overview_identifies_busiest_day(obsidian, dp_vault):
    result = json.loads(obsidian.week_overview("2026-08-17"))
    assert result["busiest_day"] == "2026-08-17"


def test_week_overview_includes_empty_days(obsidian, dp_vault):
    result = json.loads(obsidian.week_overview("2026-08-17"))
    sunday = next(d for d in result["days"] if d["day_of_week"] == "Sunday")
    assert sunday["event_count"] == 0
    assert sunday["events"] == []


# --- complete_task ---------------------------------------------------------


def test_complete_planner_entry(actions, dp_vault):
    result = json.loads(actions.complete_task(note_path=DAILY, text="Sprint planning"))
    assert result["updated"] is True
    assert "- [x] 10:00 - 11:00 Sprint planning" in (dp_vault / DAILY).read_text()


def test_complete_preserves_the_rest_of_the_line(actions, dp_vault):
    """Times, priorities and dates must survive the tick."""
    note = dp_vault / DAILY
    note.write_text("# Day planner\n\n- [ ] Send the spec 📅 2026-08-21 ⏫\n")
    actions.complete_task(note_path=DAILY, text="Send the spec")
    assert "- [x] Send the spec 📅 2026-08-21 ⏫" in note.read_text()


def test_uncomplete_a_done_task(actions, dp_vault):
    result = json.loads(
        actions.complete_task(note_path=DAILY, text="Standup", done=False)
    )
    assert result["updated"] is True
    assert "- [ ] 09:15 - 09:30 Standup" in (dp_vault / DAILY).read_text()


def test_complete_is_a_noop_when_already_done(actions, dp_vault):
    result = json.loads(actions.complete_task(note_path=DAILY, text="Standup"))
    assert "error" in result
    assert "already done" in result["error"]


def test_complete_refuses_when_ambiguous(actions, dp_vault):
    note = dp_vault / DAILY
    note.write_text("# Day planner\n\n- [ ] 09:00 - 09:30 Sync\n- [ ] 11:00 - 11:30 Sync\n")
    result = json.loads(actions.complete_task(note_path=DAILY, text="Sync"))
    assert "error" in result
    assert "2 lines" in result["error"]
    assert note.read_text().count("- [ ]") == 2


def test_complete_disambiguated_by_start_time(actions, dp_vault):
    note = dp_vault / DAILY
    note.write_text("# Day planner\n\n- [ ] 09:00 - 09:30 Sync\n- [ ] 11:00 - 11:30 Sync\n")
    result = json.loads(
        actions.complete_task(note_path=DAILY, text="Sync", start_time="11:00")
    )
    assert result["updated"] is True
    text = note.read_text()
    assert "- [ ] 09:00 - 09:30 Sync" in text
    assert "- [x] 11:00 - 11:30 Sync" in text


@pytest.mark.parametrize(
    "quoted",
    [
        "Sprint planning",
        "10:00 - 11:00 Sprint planning",
        "- [ ] 10:00 - 11:00 Sprint planning",
        "  - [ ] 10:00 - 11:00 Sprint planning  ",
    ],
)
def test_complete_accepts_any_way_the_model_quotes_the_line(actions, dp_vault, quoted):
    """A live run had the model retry three phrasings, prompting the user each time."""
    result = json.loads(actions.complete_task(note_path=DAILY, text=quoted))
    assert result.get("updated") is True, result
    assert "- [x] 10:00 - 11:00 Sprint planning" in (dp_vault / DAILY).read_text()


def test_rename_accepts_a_quoted_full_line(actions, dp_vault):
    result = json.loads(
        actions.rename_entry(
            note_path=DAILY,
            text="- [ ] 10:00 - 11:00 Sprint planning",
            new_text="Sprint kickoff",
        )
    )
    assert result.get("updated") is True, result
    assert "- [ ] 10:00 - 11:00 Sprint kickoff" in (dp_vault / DAILY).read_text()


def test_complete_reports_options_when_no_match(actions, dp_vault):
    result = json.loads(actions.complete_task(note_path=DAILY, text="Nothing like this"))
    assert "error" in result
    assert "Sprint planning" in result["error"]


def test_complete_refuses_path_outside_vault(actions, dp_vault):
    result = json.loads(actions.complete_task(note_path="../../etc/passwd", text="x"))
    assert "error" in result
    assert "outside the vault" in result["error"]


# --- rename_entry ----------------------------------------------------------


def test_rename_planner_entry_keeps_its_time(actions, dp_vault):
    result = json.loads(
        actions.rename_entry(note_path=DAILY, text="Sprint planning", new_text="Sprint kickoff")
    )
    assert result["updated"] is True
    assert "- [ ] 10:00 - 11:00 Sprint kickoff" in (dp_vault / DAILY).read_text()


def test_rename_keeps_task_metadata(actions, dp_vault):
    note = dp_vault / DAILY
    note.write_text("# Day planner\n\n- [ ] Send the spec 📅 2026-08-21 ⏫\n")
    actions.rename_entry(note_path=DAILY, text="Send the spec", new_text="Send the draft")
    text = note.read_text()
    assert "Send the draft" in text
    assert "📅 2026-08-21" in text
    assert "⏫" in text


def test_rename_preserves_completed_state(actions, dp_vault):
    actions.rename_entry(note_path=DAILY, text="Standup", new_text="Team sync")
    assert "- [x] 09:15 - 09:30 Team sync" in (dp_vault / DAILY).read_text()


def test_rename_rejects_empty_text(actions, dp_vault):
    result = json.loads(
        actions.rename_entry(note_path=DAILY, text="Sprint planning", new_text="  ")
    )
    assert "error" in result


def test_rename_refuses_when_ambiguous(actions, dp_vault):
    note = dp_vault / DAILY
    note.write_text("# Day planner\n\n- [ ] 09:00 - 09:30 Sync\n- [ ] 11:00 - 11:30 Sync\n")
    result = json.loads(actions.rename_entry(note_path=DAILY, text="Sync", new_text="Huddle"))
    assert "error" in result
    assert "Huddle" not in note.read_text()


@pytest.mark.parametrize(
    "quoted",
    [
        "Sprint planning",
        "10:00 - 11:00 Sprint planning",
        "- [ ] 10:00 - 11:00 Sprint planning",
        # How the model actually phrased it in a live run: title first, time after.
        "- [ ] Sprint planning — 2026-08-17 10:00",
        "Sprint planning — 10:00",
        "Sprint planning (10:00-11:00)",
    ],
)
def test_complete_matches_decorated_phrasings(actions, dp_vault, quoted):
    """Each unmatched phrasing costs the user a wasted approval prompt."""
    result = json.loads(actions.complete_task(note_path=DAILY, text=quoted))
    assert result.get("updated") is True, result
    assert "- [x] 10:00 - 11:00 Sprint planning" in (dp_vault / DAILY).read_text()


def test_decorated_phrasing_still_refuses_when_ambiguous(actions, dp_vault):
    """Looser matching must not become guessing."""
    note = dp_vault / DAILY
    note.write_text("# Day planner\n\n- [ ] 09:00 - 09:30 Sync\n- [ ] 11:00 - 11:30 Sync\n")
    result = json.loads(actions.complete_task(note_path=DAILY, text="Sync — today"))
    assert "error" in result
    assert "2 lines" in result["error"]
    assert note.read_text().count("- [ ]") == 2


@pytest.mark.parametrize(
    "new_text",
    [
        "Checkout spec drafting",
        "10:00 - 11:00 Checkout spec drafting",
        "- [ ] 10:00 - 11:00 Checkout spec drafting",
    ],
)
def test_rename_never_doubles_the_prefix(actions, dp_vault, new_text):
    """A live run produced '- [ ] 10:00 - 11:00 - [ ] 10:00 - 11:00 Title'."""
    result = json.loads(
        actions.rename_entry(note_path=DAILY, text="Sprint planning", new_text=new_text)
    )
    assert result.get("updated") is True, result
    line = next(
        l for l in (dp_vault / DAILY).read_text().splitlines() if "Checkout spec drafting" in l
    )
    assert line == "- [ ] 10:00 - 11:00 Checkout spec drafting", f"corrupted: {line!r}"
    assert line.count("- [") == 1
    assert line.count("10:00") == 1


def test_rename_rejects_replacement_with_no_description(actions, dp_vault):
    result = json.loads(
        actions.rename_entry(note_path=DAILY, text="Sprint planning", new_text="- [ ] 10:00 - 11:00")
    )
    assert "error" in result
