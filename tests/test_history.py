"""The history store: Changes, Sightings, and Approvals.

History records *when*. Whether an Entry is open is always read from the vault,
so nothing here is asked that question.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

import history

# --- where state lives ------------------------------------------------------


def test_state_dir_prefers_ops_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_STATE_DIR", str(tmp_path / "explicit"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    assert history.state_dir() == tmp_path / "explicit"


def test_state_dir_falls_back_to_xdg(tmp_path, monkeypatch):
    monkeypatch.delenv("OPS_STATE_DIR", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    assert history.state_dir() == tmp_path / "xdg" / "ops"


def test_state_dir_defaults_to_local_state(monkeypatch):
    monkeypatch.delenv("OPS_STATE_DIR", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    assert history.state_dir() == Path.home() / ".local" / "state" / "ops"


# --- Changes and History ----------------------------------------------------

MON = datetime(2026, 9, 14, 9, 0)
SAT = datetime(2026, 9, 19, 17, 30)


def test_history_lists_changes_in_order():
    history.record_change("created", "Inbox.md", "Send Sarah the deck", via="fast_path", at=MON)
    history.record_change("completed", "Inbox.md", "Send Sarah the deck", via="approved", at=SAT)

    [entry] = history.history_for("send sarah")
    assert entry.note_path == "Inbox.md"
    assert entry.description == "Send Sarah the deck"
    assert [(c.kind, c.at, c.via) for c in entry.changes] == [
        ("created", MON, "fast_path"),
        ("completed", SAT, "approved"),
    ]


def test_history_is_empty_for_an_unknown_entry():
    assert history.history_for("nothing like this") == []


def test_history_follows_an_entry_through_rename_and_move():
    history.record_change("created", "Inbox.md", "Send Sarah the deck", via="fast_path", at=MON)
    history.record_change(
        "renamed", "Inbox.md", "Send Sarah the deck",
        via="approved", at=MON.replace(hour=10), new_description="Send Sarah the Q3 deck",
    )
    history.record_change(
        "moved", "Inbox.md", "Send Sarah the Q3 deck",
        via="approved", at=MON.replace(hour=11), new_note_path="Daily/2026-09-15.md",
    )

    [entry] = history.history_for("q3 deck")
    assert (entry.note_path, entry.description) == ("Daily/2026-09-15.md", "Send Sarah the Q3 deck")
    assert [c.kind for c in entry.changes] == ["created", "renamed", "moved"]


def test_same_wording_in_two_notes_is_two_entries():
    history.record_change("created", "Inbox.md", "Standup", via="fast_path", at=MON)
    history.record_change("created", "Daily/2026-09-15.md", "Standup", via="fast_path", at=MON)

    assert sorted(e.note_path for e in history.history_for("standup")) == [
        "Daily/2026-09-15.md",
        "Inbox.md",
    ]


def test_recording_into_an_unusable_store_does_not_raise(tmp_path, monkeypatch, capsys):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("")
    monkeypatch.setenv("OPS_STATE_DIR", str(blocker))

    history.record_change("created", "Inbox.md", "Anything", via="fast_path")

    assert "could not record" in capsys.readouterr().err


# --- Sightings --------------------------------------------------------------

EMAIL = "gmail:18f2a"


def test_a_sighting_raised_again_keeps_its_first_date():
    first = history.record_sighting(EMAIL, "I'll send you the deck", at=MON)
    again = history.record_sighting(EMAIL, "I'll  send you the deck ", at=SAT)

    assert again.id == first.id
    assert again.first_raised == MON
    assert again.status == "open"


def test_the_same_promise_in_two_sources_is_two_sightings():
    history.record_sighting(EMAIL, "I'll send you the deck", at=MON)
    history.record_sighting("Meetings/Sync with Sarah.md", "I'll send you the deck", at=MON)

    assert len(history.open_sightings()) == 2


def test_a_resolved_sighting_is_no_longer_open():
    kept = history.record_sighting(EMAIL, "I'll send you the deck", at=MON)
    dropped = history.record_sighting(EMAIL, "maybe lunch sometime", at=MON)

    history.resolve_sighting(
        kept.id, "captured", note_path="Inbox.md", description="Send Sarah the deck", at=SAT
    )
    history.resolve_sighting(dropped.id, "dismissed", at=SAT)

    assert history.open_sightings() == []
    raised_again = history.record_sighting(EMAIL, "I'll send you the deck", at=SAT)
    assert raised_again.status == "captured"
    assert raised_again.entry == ("Inbox.md", "Send Sarah the deck")


def test_resolving_refuses_an_unknown_outcome_or_id():
    sighting = history.record_sighting(EMAIL, "I'll send you the deck", at=MON)

    with pytest.raises(ValueError):
        history.resolve_sighting(sighting.id, "forgotten")
    with pytest.raises(LookupError):
        history.resolve_sighting(sighting.id + 99, "dismissed")


# --- Approvals --------------------------------------------------------------


def test_past_rejections_lists_the_noes_newest_first():
    history.record_approval(
        "delete_event", {"note_path": "a.md"}, "Delete “Gym”", approved=False,
        reason="keep it", thread_id="t1", at=MON,
    )
    history.record_approval(
        "add_task", {"text": "x"}, "Add “x”", approved=True, reason=None, thread_id="t1", at=MON,
    )
    history.record_approval(
        "delete_event", {"note_path": "b.md"}, "Delete “Run”", approved=False,
        reason=None, thread_id="t2", at=SAT,
    )

    rejections = history.past_rejections()
    assert [(r.description, r.reason) for r in rejections] == [
        ("Delete “Run”", None),
        ("Delete “Gym”", "keep it"),
    ]
    assert rejections[1].args == {"note_path": "a.md"}
    assert history.past_rejections(tool="add_task") == []


def test_an_answer_recorded_twice_is_kept_once():
    # A crash after recording and before the node finishes re-runs the node.
    for _ in range(2):
        history.record_approval(
            "delete_event", {"note_path": "a.md"}, "Delete “Gym”", approved=False,
            reason=None, thread_id="t1", tool_call_id="call_1", at=MON,
        )

    assert len(history.past_rejections()) == 1


def test_the_same_call_id_in_another_thread_is_a_different_answer():
    for thread in ("t1", "t2"):
        history.record_approval(
            "delete_event", {"note_path": "a.md"}, "Delete “Gym”", approved=False,
            reason=None, thread_id=thread, tool_call_id="call_1", at=MON,
        )

    assert len(history.past_rejections()) == 2


def test_migration_2_upgrades_a_version_1_database(state_dir):
    import sqlite3

    state_dir.mkdir(parents=True)
    conn = sqlite3.connect(state_dir / history.DB_NAME)
    conn.executescript(f"BEGIN; {history.MIGRATIONS[0]} PRAGMA user_version = 1; COMMIT;")
    conn.execute(
        "INSERT INTO approvals (at, tool, args, description, approved, reason, thread_id) "
        "VALUES (?, 'delete_event', '{}', 'Delete “Gym”', 0, NULL, 't1')",
        (MON.isoformat(),),
    )
    conn.commit()
    conn.close()

    assert [r.description for r in history.past_rejections()] == ["Delete “Gym”"]
    history.record_approval(
        "delete_event", {}, "Delete “Run”", approved=False,
        reason=None, thread_id="t1", tool_call_id="call_1", at=SAT,
    )
    assert len(history.past_rejections()) == 2
