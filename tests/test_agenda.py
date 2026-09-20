"""What lands in the day view, and what ticking it off then finds.

These test the *selection* — which tasks count as overdue, which are due, what
gets collapsed away — not how `ops` renders them. The rendering is Rich markup
and is left untested, on the same principle as the rest of `ops.py`.
"""

from datetime import date

import pytest

from vaultlib.agenda import (
    OVERDUE_SHOWN,
    collapse_overdue,
    collect_open_tasks,
    is_planner_entry,
    locate_open_task,
    split_agenda,
)
from vaultlib.paths import VaultPaths
from vaultlib.tasks import parse_task_line

TODAY = date(2026, 9, 20)


def task(text: str, note_path: str = "Inbox.md"):
    parsed = parse_task_line(text, note_path=note_path, line_number=1)
    assert parsed is not None, f"not a task line: {text}"
    return parsed


@pytest.fixture
def tmp_vault(tmp_path) -> VaultPaths:
    """An empty throwaway vault. Never the real one, never the fixtures."""
    return VaultPaths(root=tmp_path)


def write(paths: VaultPaths, relative: str, body: str) -> None:
    target = paths.root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")


# --------------------------------------------------------------- split_agenda


def test_splits_late_from_due_today():
    tasks = [
        task("- [ ] Renew the domain 📅 2026-09-01"),
        task("- [ ] Send the spec 📅 2026-09-20"),
        task("- [ ] Book flights 📅 2026-09-25"),
    ]
    agenda = split_agenda(tasks, TODAY)

    assert [t.description for t in agenda.overdue] == ["Renew the domain"]
    assert [t.description for t in agenda.due] == ["Send the spec"]


def test_future_task_is_neither():
    agenda = split_agenda([task("- [ ] Book flights 📅 2026-09-25")], TODAY)
    assert agenda.is_empty


def test_undated_task_is_never_overdue():
    """A backlog item with no date is not late. Treating it as late would turn
    the whole backlog into a permanent alarm."""
    agenda = split_agenda([task("- [ ] Finish the pricing section 🔼")], TODAY)
    assert agenda.is_empty


def test_scheduled_date_is_not_a_due_date():
    """⏳ is when you meant to start, 📅 is when it is owed. Only 📅 counts."""
    agenda = split_agenda([task("- [ ] Draft it ⏳ 2026-08-18")], TODAY)
    assert agenda.is_empty


def test_overdue_is_most_recently_missed_first():
    tasks = [
        task("- [ ] Oldest 📅 2026-07-01"),
        task("- [ ] Newest 📅 2026-09-19"),
        task("- [ ] Middle 📅 2026-08-15"),
    ]
    agenda = split_agenda(tasks, TODAY)
    assert [t.description for t in agenda.overdue] == ["Newest", "Middle", "Oldest"]


def test_same_due_date_sorts_by_description():
    tasks = [
        task("- [ ] Beta 📅 2026-09-01"),
        task("- [ ] Alpha 📅 2026-09-01"),
    ]
    agenda = split_agenda(tasks, TODAY)
    assert [t.description for t in agenda.overdue] == ["Alpha", "Beta"]


# ------------------------------------------------------------ collapse_overdue


def test_collapse_keeps_three_and_counts_the_rest():
    overdue = [task(f"- [ ] Thing {n} 📅 2026-09-0{n}") for n in range(1, 7)]
    shown, hidden = collapse_overdue(overdue)

    assert len(shown) == OVERDUE_SHOWN
    assert hidden == 3
    assert shown == overdue[:3]


def test_collapse_hides_nothing_when_under_the_limit():
    overdue = [task("- [ ] Only one 📅 2026-09-01")]
    shown, hidden = collapse_overdue(overdue)
    assert shown == overdue
    assert hidden == 0


def test_collapse_of_nothing():
    assert collapse_overdue([]) == ([], 0)


def test_collapse_counts_everything_when_showing_none():
    overdue = [task(f"- [ ] Thing {n} 📅 2026-09-01") for n in range(3)]
    shown, hidden = collapse_overdue(overdue, limit=0)
    assert shown == []
    assert hidden == 3


# --------------------------------------------------------- collect_open_tasks


def test_collects_open_tasks_across_notes(tmp_vault):
    write(tmp_vault, "Inbox.md", "- [ ] Renew the domain 📅 2026-09-01\n")
    write(tmp_vault, "Projects/Checkout.md", "- [ ] Send the spec 📅 2026-09-20\n")

    found = collect_open_tasks(tmp_vault)
    assert [t.description for t in found] == ["Renew the domain", "Send the spec"]


def test_closed_tasks_are_not_collected(tmp_vault):
    write(
        tmp_vault,
        "Inbox.md",
        "- [x] Already done 📅 2026-09-01\n"
        "- [-] Cancelled 📅 2026-09-01\n"
        "- [ ] Still open 📅 2026-09-01\n",
    )
    assert [t.description for t in collect_open_tasks(tmp_vault)] == ["Still open"]


def test_empty_checkboxes_are_not_tasks(tmp_vault):
    """Real vaults are full of bare "- [ ]" slots left open in the editor."""
    write(tmp_vault, "Inbox.md", "- [ ]\n- [ ] Real one 📅 2026-09-01\n")
    assert [t.description for t in collect_open_tasks(tmp_vault)] == ["Real one"]


def test_planner_entries_are_not_collected(tmp_vault):
    """A timed block already shows in the day view. Counting it as a task too
    would print the same line twice."""
    write(
        tmp_vault,
        "Daily/2026-09-20.md",
        "# Day planner\n\n- [ ] 09:00 - 09:15 Standup\n- [ ] Email Sarah 📅 2026-09-20\n",
    )
    assert [t.description for t in collect_open_tasks(tmp_vault)] == ["Email Sarah"]


def test_is_planner_entry_only_for_timed_lines():
    assert is_planner_entry(task("- [ ] 09:00 - 09:15 Standup"), 30)
    assert not is_planner_entry(task("- [ ] Standup 📅 2026-09-20"), 30)


def test_undated_tasks_sort_last(tmp_vault):
    write(
        tmp_vault,
        "Inbox.md",
        "- [ ] Someday\n- [ ] Dated 📅 2026-09-01\n",
    )
    assert [t.description for t in collect_open_tasks(tmp_vault)] == ["Dated", "Someday"]


def test_templates_are_not_tasks(tmp_vault):
    """A template's placeholder checkbox is not work anyone has to do."""
    write(tmp_vault, ".obsidian/templates.json", '{"folder": "Templates"}')
    write(tmp_vault, "Templates/Daily.md", "- [ ] Placeholder 📅 2026-09-01\n")
    write(tmp_vault, "Inbox.md", "- [ ] Real 📅 2026-09-01\n")

    assert [t.description for t in collect_open_tasks(tmp_vault)] == ["Real"]


# ------------------------------------------------------------ locate_open_task


def test_locates_a_task_in_another_note(tmp_vault):
    write(tmp_vault, "Daily/2026-09-20.md", "# Day planner\n")
    write(tmp_vault, "Projects/Checkout.md", "notes\n- [ ] Send the spec 📅 2026-09-20\n")

    path, index, found = locate_open_task(tmp_vault, "Send the spec")
    assert path == tmp_vault.root / "Projects" / "Checkout.md"
    assert index == 1
    assert found.description.startswith("Send the spec")


def test_locate_refuses_ambiguity_and_names_both_notes(tmp_vault):
    write(tmp_vault, "Inbox.md", "- [ ] Email Sarah 📅 2026-09-01\n")
    write(tmp_vault, "Projects/Checkout.md", "- [ ] Email Sarah 📅 2026-09-20\n")

    result = locate_open_task(tmp_vault, "Email Sarah")
    assert isinstance(result, dict)
    assert "2 open tasks" in result["error"]
    assert "Inbox.md" in result["error"]
    assert "Projects/Checkout.md" in result["error"]


def test_locate_ignores_closed_tasks(tmp_vault):
    """The open-only filter is what stops a finished standup from months ago
    colliding with today's."""
    write(tmp_vault, "Daily/2026-06-01.md", "- [ ] 09:00 - 09:15 Standup\n")
    write(tmp_vault, "Daily/2026-09-20.md", "- [ ] 09:00 - 09:15 Standup\n")
    (tmp_vault.root / "Daily" / "2026-06-01.md").write_text(
        "- [x] 09:00 - 09:15 Standup\n", encoding="utf-8"
    )

    path, _, _ = locate_open_task(tmp_vault, "Standup")
    assert path == tmp_vault.root / "Daily" / "2026-09-20.md"


def test_locate_matches_a_decorated_line(tmp_vault):
    """The day view prints a bare description; a user may paste back the row."""
    write(tmp_vault, "Inbox.md", "- [ ] Send the spec 📅 2026-09-20\n")

    path, index, _ = locate_open_task(tmp_vault, "- [ ] Send the spec 📅 2026-09-20")
    assert (path, index) == (tmp_vault.root / "Inbox.md", 0)


def test_locate_prefers_an_exact_match(tmp_vault):
    write(
        tmp_vault,
        "Inbox.md",
        "- [ ] Spec 📅 2026-09-01\n- [ ] Send Sarah the spec 📅 2026-09-02\n",
    )
    _, index, _ = locate_open_task(tmp_vault, "Spec")
    assert index == 0


def test_locate_reports_nothing_matching(tmp_vault):
    write(tmp_vault, "Inbox.md", "- [ ] Renew the domain 📅 2026-09-01\n")

    result = locate_open_task(tmp_vault, "buy a boat")
    assert isinstance(result, dict)
    assert "buy a boat" in result["error"]
