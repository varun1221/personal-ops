from datetime import date

import pytest

from vaultlib.events import (
    Event,
    _coerce_time,
    expand_events,
    format_event_note,
    parse_event_note,
)


def _events(vault):
    parsed = []
    for path in sorted(vault.events_dir.rglob("*.md")):
        event = parse_event_note(path, vault.relative(path))
        if event is not None:
            parsed.append(event)
    return parsed


def test_parses_every_fixture_event(vault):
    assert len(_events(vault)) == 6


def test_timed_event(vault):
    occurrences = expand_events(_events(vault), date(2026, 8, 17), date(2026, 8, 17))
    planning = next(o for o in occurrences if o.title == "Sprint Planning")
    assert planning.start_time == "10:00"
    assert planning.end_time == "11:00"
    assert planning.all_day is False


def test_all_day_event(vault):
    occurrences = expand_events(_events(vault), date(2026, 8, 21), date(2026, 8, 21))
    offsite = next(o for o in occurrences if o.title == "Team Offsite")
    assert offsite.all_day is True
    assert offsite.start_time is None


def test_multi_day_event_spans_every_day(vault):
    occurrences = expand_events(_events(vault), date(2026, 8, 24), date(2026, 8, 25))
    qbr = [o for o in occurrences if o.title == "Quarterly Business Review"]
    assert [o.date for o in qbr] == [date(2026, 8, 24), date(2026, 8, 25)]


def test_recurring_event_hits_weekdays_only(vault):
    # 2026-08-17 is a Monday, so this window is Mon-Sun.
    occurrences = expand_events(_events(vault), date(2026, 8, 17), date(2026, 8, 23))
    standups = [o for o in occurrences if o.title == "Daily Standup"]
    assert [o.date.strftime("%a") for o in standups] == ["Mon", "Tue", "Wed", "Thu", "Fri"]
    assert all(o.recurring for o in standups)


def test_recurring_event_respects_start_recur():
    event = Event(
        title="Standup",
        note_path="x.md",
        recurring=True,
        days_of_week=[0, 1, 2, 3, 4],
        start_recur=date(2026, 8, 19),
    )
    occurrences = expand_events([event], date(2026, 8, 17), date(2026, 8, 21))
    assert [o.date for o in occurrences] == [
        date(2026, 8, 19),
        date(2026, 8, 20),
        date(2026, 8, 21),
    ]


def test_date_range_filtering_excludes_outside(vault):
    occurrences = expand_events(_events(vault), date(2026, 8, 20), date(2026, 8, 20))
    assert {o.title for o in occurrences} == {"Daily Standup", "Design Review", "1-1 with Priya"}


def test_occurrences_sorted_by_date_then_time(vault):
    occurrences = expand_events(_events(vault), date(2026, 8, 17), date(2026, 8, 23))
    keys = [(o.date, o.start_time or "") for o in occurrences]
    assert keys == sorted(keys)


def test_note_without_title_is_not_an_event(vault, tmp_path):
    note = tmp_path / "plain.md"
    note.write_text("---\ntags: [note]\n---\n\nJust a note.\n")
    assert parse_event_note(note, "plain.md") is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("10:00", "10:00"),
        ("9:5", "09:05"),
        (600, "10:00"),  # YAML 1.1 reads unquoted 10:00 as sexagesimal 600
        (555, "09:15"),
        (None, None),
        ("garbage", None),
    ],
)
def test_time_coercion(raw, expected):
    assert _coerce_time(raw) == expected


def test_unquoted_time_in_frontmatter(tmp_path):
    """A vault written by hand often omits the quotes around times."""
    note = tmp_path / "event.md"
    note.write_text("---\ntitle: Sync\ndate: 2026-08-20\nstartTime: 10:00\n---\n")
    event = parse_event_note(note, "event.md")
    assert event.start_time == "10:00"


def test_format_event_note_round_trip(tmp_path):
    note = tmp_path / "new.md"
    note.write_text(format_event_note("Spec writing", "2026-08-20", "13:00", "15:00"))
    event = parse_event_note(note, "new.md")
    assert event.title == "Spec writing"
    assert event.date == date(2026, 8, 20)
    assert event.start_time == "13:00"
    assert event.all_day is False


def test_format_event_note_all_day_when_no_time(tmp_path):
    note = tmp_path / "new.md"
    note.write_text(format_event_note("Offsite", "2026-08-21", None, None))
    assert parse_event_note(note, "new.md").all_day is True


def test_format_event_note_rejects_bad_date():
    with pytest.raises(ValueError):
        format_event_note("Thing", "next tuesday", None, None)
