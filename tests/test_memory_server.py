"""The memory server: History, Sightings and past rejections, as tools.

The vault is the authority on whether an Entry is open. History only says when,
so every answer here is checked against the vault as it is right now.
"""

from __future__ import annotations

import importlib
import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import history

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DAILY = "Schedules/2026-08-17.md"
NOW = datetime.now()
SIX_DAYS_AGO = NOW - timedelta(days=6)


@pytest.fixture
def dp_vault(tmp_path, monkeypatch):
    vault = tmp_path / "dp"
    shutil.copytree(PROJECT_ROOT / "fixtures" / "dayplanner_vault", vault)
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
    monkeypatch.delenv("OBSIDIAN_DAILY_FOLDER", raising=False)
    monkeypatch.delenv("OBSIDIAN_CALENDAR_FORMAT", raising=False)
    return vault


@pytest.fixture
def memory():
    return importlib.import_module("servers.memory.server")


def _history(memory, text):
    return json.loads(memory.entry_history(text))


# --- entry_history ------------------------------------------------------------


def test_a_recorded_completion_says_when_and_how_long(memory, dp_vault):
    history.record_change("created", DAILY, "Standup", via="fast_path", at=SIX_DAYS_AGO)
    history.record_change("completed", DAILY, "Standup", via="fast_path", at=NOW)

    [entry] = _history(memory, "standup")["entries"]
    assert entry["status"] == "done"
    assert entry["completed_at"] == NOW.isoformat(timespec="minutes")
    assert entry["open_for_days"] == 6


def test_done_in_the_vault_without_a_recorded_completion_is_not_given_a_date(memory, dp_vault):
    # The fixture's Standup is already ticked; nothing here recorded that.
    history.record_change("created", DAILY, "Standup", via="fast_path", at=SIX_DAYS_AGO)

    [entry] = _history(memory, "standup")["entries"]
    assert entry["status"] == "done"
    assert entry["completed_at"] is None
    assert "unknown" in entry["summary"]


def test_an_open_entry_says_how_long_it_has_been_open(memory, dp_vault):
    history.record_change("created", DAILY, "Sprint planning", via="approved", at=SIX_DAYS_AGO)

    [entry] = _history(memory, "sprint")["entries"]
    assert entry["status"] == "open"
    assert entry["open_for_days"] == 6


def test_the_vault_wins_over_a_recorded_completion(memory, dp_vault):
    # Recorded as done, then reopened by hand in Obsidian.
    history.record_change("completed", DAILY, "Sprint planning", via="fast_path", at=SIX_DAYS_AGO)

    [entry] = _history(memory, "sprint")["entries"]
    assert entry["status"] == "open"


def test_an_entry_gone_from_the_vault_is_reported_missing(memory, dp_vault):
    history.record_change("created", DAILY, "Dentist", via="fast_path", at=SIX_DAYS_AGO)

    [entry] = _history(memory, "dentist")["entries"]
    assert entry["status"] == "missing"


def test_no_history_is_inconclusive_not_a_no(memory, dp_vault):
    result = _history(memory, "send sarah")
    assert result["entries"] == []
    assert "inconclusive" in result["note"]


# --- Sightings ---------------------------------------------------------------


def test_raising_a_sighting_again_reports_its_age(memory, dp_vault):
    history.record_sighting("gmail:18f2a", "I'll send you the deck", at=SIX_DAYS_AGO)

    again = json.loads(memory.record_sighting("gmail:18f2a", "I'll send you the deck"))
    assert again["days_since_first_raised"] == 6
    assert again["status"] == "open"

    [listed] = json.loads(memory.open_sightings())["sightings"]
    assert listed["id"] == again["id"]


def test_resolving_with_a_bad_outcome_is_an_error_not_a_crash(memory, dp_vault):
    sighting = json.loads(memory.record_sighting("gmail:18f2a", "I'll send you the deck"))

    result = json.loads(memory.resolve_sighting(sighting["id"], "forgotten"))
    assert "error" in result
    assert json.loads(memory.open_sightings())["sightings"]


def test_a_dismissed_sighting_leaves_the_open_list(memory, dp_vault):
    sighting = json.loads(memory.record_sighting("gmail:18f2a", "maybe lunch sometime"))

    json.loads(memory.resolve_sighting(sighting["id"], "dismissed"))
    assert json.loads(memory.open_sightings())["sightings"] == []


# --- past_rejections ---------------------------------------------------------


def test_past_rejections_are_listed(memory, dp_vault):
    history.record_approval(
        "delete_event", {"note_path": DAILY}, "Delete “Standup”",
        approved=False, reason="keep it", thread_id="t1",
    )

    [rejection] = json.loads(memory.past_rejections())["rejections"]
    assert rejection["description"] == "Delete “Standup”"
    assert rejection["reason"] == "keep it"


def test_the_same_wording_twice_in_different_states_is_ambiguous(memory, dp_vault):
    note = dp_vault / DAILY
    note.write_text(note.read_text() + "- [x] Call mum\n- [ ] Call mum\n")
    history.record_change("created", DAILY, "Call mum", via="fast_path", at=SIX_DAYS_AGO)

    [entry] = _history(memory, "call mum")["entries"]
    assert entry["status"] == "ambiguous"
    assert entry["lines"] == [12, 13]
    assert "more than once" in entry["summary"]


def test_the_same_wording_twice_in_one_state_is_not_ambiguous(memory, dp_vault):
    note = dp_vault / DAILY
    note.write_text(note.read_text() + "- [ ] Call mum\n- [ ] Call mum\n")
    history.record_change("created", DAILY, "Call mum", via="fast_path", at=SIX_DAYS_AGO)

    [entry] = _history(memory, "call mum")["entries"]
    assert entry["status"] == "open"
