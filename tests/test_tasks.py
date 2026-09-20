from datetime import date

import pytest

from vaultlib.tasks import format_task_line, parse_task_line


def test_plain_task():
    task = parse_task_line("- [ ] Write the spec")
    assert task.description == "Write the spec"
    assert task.status == "todo"
    assert task.due is None


def test_all_date_fields():
    line = "- [ ] Ship it 📅 2026-08-21 ⏳ 2026-08-18 🛫 2026-08-17 ➕ 2026-08-13"
    task = parse_task_line(line)
    assert task.description == "Ship it"
    assert task.dates == {
        "due": date(2026, 8, 21),
        "scheduled": date(2026, 8, 18),
        "start": date(2026, 8, 17),
        "created": date(2026, 8, 13),
    }


@pytest.mark.parametrize(
    "symbol,expected",
    [("🔺", "highest"), ("⏫", "high"), ("🔼", "medium"), ("🔽", "low"), ("⏬", "lowest")],
)
def test_priorities(symbol, expected):
    task = parse_task_line(f"- [ ] Task {symbol}")
    assert task.priority == expected
    assert task.description == "Task"


@pytest.mark.parametrize(
    "marker,status",
    [(" ", "todo"), ("x", "done"), ("X", "done"), ("/", "in_progress"), ("-", "cancelled")],
)
def test_statuses(marker, status):
    assert parse_task_line(f"- [{marker}] Task").status == status


def test_variation_selector_does_not_break_matching():
    """U+FE0F rides along on emoji copied between apps and breaks naive matching."""
    task = parse_task_line("- [ ] Task ⏳️ 2026-08-18 ⏫️")
    assert task.dates["scheduled"] == date(2026, 8, 18)
    assert task.priority == "high"
    assert task.description == "Task"


def test_non_breaking_space_does_not_break_matching():
    task = parse_task_line("- [ ] Task 📅 2026-08-21")
    assert task.due == date(2026, 8, 21)
    assert task.description == "Task"


def test_recurrence_and_due_together():
    task = parse_task_line("- [ ] Weekly review 🔁 every week 📅 2026-08-21")
    assert task.recurrence == "every week"
    assert task.due == date(2026, 8, 21)
    assert task.description == "Weekly review"


def test_dependencies():
    task = parse_task_line("- [ ] Deploy 🆔 abc123 ⛔ def456, ghi789")
    assert task.task_id == "abc123"
    assert task.depends_on == ["def456", "ghi789"]


def test_malformed_date_drops_field_not_task():
    task = parse_task_line("- [ ] Task 📅 not-a-date")
    assert task.description.startswith("Task")
    assert task.due is None


def test_bullet_variants_and_indentation():
    for line in ["* [ ] Task", "+ [ ] Task", "    - [ ] Task"]:
        assert parse_task_line(line).description == "Task"


def test_non_task_lines_return_none():
    for line in ["Just a sentence", "- a bullet, not a task", "# Heading", ""]:
        assert parse_task_line(line) is None


def test_format_round_trip():
    line = format_task_line("Send the spec", due_date="2026-08-21", priority="high")
    task = parse_task_line(line)
    assert task.description == "Send the spec"
    assert task.due == date(2026, 8, 21)
    assert task.priority == "high"


def test_format_rejects_bad_input():
    with pytest.raises(ValueError):
        format_task_line("Task", due_date="tomorrow")
    with pytest.raises(ValueError):
        format_task_line("Task", priority="urgent")
