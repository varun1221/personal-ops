"""Every write the actions server makes leaves a Change behind.

Driven through the tools on both sides: an actions tool makes the write, and the
memory server's entry_history is asked what happened. A write path that forgets
to record fails here, not in someone's "when did I…" six weeks later.
"""

from __future__ import annotations

import importlib
import json
import shutil
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
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
    monkeypatch.setenv("OBSIDIAN_EVENTS_FOLDER", "Events")
    monkeypatch.setenv("OBSIDIAN_INBOX_NOTE", "Inbox.md")
    monkeypatch.delenv("OBSIDIAN_CALENDAR_FORMAT", raising=False)
    return vault


@pytest.fixture
def actions():
    return importlib.import_module("servers.actions.server")


@pytest.fixture
def memory():
    return importlib.import_module("servers.memory.server")


def _only(memory, text) -> dict:
    [entry] = json.loads(memory.entry_history(text))["entries"]
    return entry


def _kinds(entry) -> list[str]:
    return [c["kind"] for c in entry["changes"]]


def test_creating_a_planner_event_is_recorded(actions, memory, dp_vault):
    actions.create_calendar_event("Dentist", "2026-08-20", "15:00", "16:00")

    entry = _only(memory, "dentist")
    assert _kinds(entry) == ["created"]
    assert entry["changes"][0]["via"] == "approved"
    assert entry["status"] == "open"


def test_creating_a_full_calendar_event_is_recorded(actions, memory, fc_vault):
    actions.create_calendar_event("Dentist", "2026-08-20", "15:00", "16:00")

    entry = _only(memory, "dentist")
    assert _kinds(entry) == ["created"]
    assert entry["status"] == "scheduled"


def test_adding_a_task_is_recorded(actions, memory, fc_vault):
    actions.add_task("Send Sarah the deck", due_date="2026-08-21")

    entry = _only(memory, "send sarah")
    assert entry["note_path"] == "Inbox.md"
    assert _kinds(entry) == ["created"]
    assert entry["status"] == "open"


def test_completing_and_reopening_are_recorded(actions, memory, dp_vault):
    actions.complete_task(DAILY, "Sprint planning")
    entry = _only(memory, "sprint planning")
    assert _kinds(entry) == ["completed"]
    assert entry["status"] == "done"

    actions.complete_task(DAILY, "Sprint planning", done=False)
    entry = _only(memory, "sprint planning")
    assert _kinds(entry) == ["completed", "reopened"]
    assert entry["status"] == "open"


def test_history_follows_a_rename(actions, memory, dp_vault):
    actions.create_calendar_event("Dentist", "2026-08-17", "15:00", "16:00")
    actions.rename_entry(DAILY, "Dentist", "Dentist, bring forms")

    entry = _only(memory, "bring forms")
    assert _kinds(entry) == ["created", "renamed"]
    assert entry["status"] == "open"


def test_history_follows_a_move_to_another_day(actions, memory, dp_vault):
    actions.create_calendar_event("Dentist", "2026-08-17", "15:00", "16:00")
    moved = json.loads(actions.move_event(DAILY, new_date="2026-08-20", title="Dentist"))

    entry = _only(memory, "dentist")
    assert _kinds(entry) == ["created", "moved"]
    assert entry["note_path"] == moved["to"]["note_path"]
    assert entry["status"] == "open"


def test_deleting_is_recorded(actions, memory, dp_vault):
    actions.delete_event(DAILY, title="Sprint planning")

    entry = _only(memory, "sprint planning")
    assert _kinds(entry) == ["deleted"]
    assert entry["status"] == "missing"


def test_deleting_a_full_calendar_note_is_recorded(actions, memory, fc_vault):
    actions.delete_event("Events/2026-08-20 Design Review.md")

    entry = _only(memory, "design review")
    assert _kinds(entry) == ["deleted"]


def test_a_refused_write_records_nothing(actions, memory, dp_vault):
    actions.delete_event(DAILY, title="No such thing")
    actions.complete_task(DAILY, "Standup")  # already done: nothing to change

    assert json.loads(memory.entry_history("no such thing"))["entries"] == []
    assert json.loads(memory.entry_history("standup"))["entries"] == []


def test_a_task_added_with_metadata_in_its_text_is_one_entry(actions, memory, fc_vault):
    # Models do put Tasks-plugin metadata in the text. The line's description is
    # what completing it will find, so that is what creation must record.
    actions.add_task("Call the bank 📅 2026-09-30")
    actions.complete_task("Inbox.md", "Call the bank")

    entry = _only(memory, "call the bank")
    assert _kinds(entry) == ["created", "completed"]
    assert entry["status"] == "done"


def test_history_follows_a_move_onto_a_day_with_the_same_wording(actions, memory, dp_vault):
    actions.create_calendar_event("Dentist", "2026-08-20", "09:00", "09:30")
    actions.create_calendar_event("Dentist", "2026-08-17", "15:00", "16:00")
    actions.move_event(DAILY, new_date="2026-08-20", title="Dentist")

    moved = [
        e for e in json.loads(memory.entry_history("dentist"))["entries"]
        if _kinds(e) == ["created", "moved"]
    ]
    assert moved and moved[0]["note_path"] == "Schedules/2026-08-20.md"
    assert moved[0]["status"] == "open"
