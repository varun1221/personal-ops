"""The deterministic capture parser.

This path writes to the vault with no approval prompt, on the grounds that the
user typed the instruction themselves and there is no inference to review. That
makes a wrong parse worse than a failed one: returning None costs a second of
model latency, guessing wrong costs a bad entry the user did not see coming.
So the important tests here are the ones asserting None.
"""

from __future__ import annotations

from datetime import date

import pytest

from vaultlib.quickparse import parse_capture

TUE = date(2026, 8, 18)  # a Tuesday


def p(text, today=TUE, duration=30):
    return parse_capture(text, today, duration)


# --- times ------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,start,end",
    [
        ("gym at 6pm", "18:00", "18:30"),
        ("gym at 6:30pm", "18:30", "19:00"),
        ("gym at 18:00", "18:00", "18:30"),
        ("gym at 9am", "09:00", "09:30"),
        ("gym at 12pm", "12:00", "12:30"),
        ("gym at 12am", "00:00", "00:30"),
        ("lunch at noon", "12:00", "12:30"),
        ("sleep at midnight", "00:00", "00:30"),
        ("retro 2:30pm", "14:30", "15:00"),
    ],
)
def test_single_times(text, start, end):
    result = p(text)
    assert (result.start_time, result.end_time) == (start, end)


@pytest.mark.parametrize(
    "text,start,end",
    [
        ("deep work 14:00-16:00", "14:00", "16:00"),
        ("standup 9-9:15am", "09:00", "09:15"),
        ("coffee 4-4:30pm", "16:00", "16:30"),
        ("call 9am to 10am", "09:00", "10:00"),
        ("gym 1pm until 3pm", "13:00", "15:00"),
    ],
)
def test_ranges(text, start, end):
    result = p(text)
    assert (result.start_time, result.end_time) == (start, end)


def test_trailing_meridiem_governs_both_ends():
    """"6-7pm" means 18:00-19:00, not 06:00-19:00."""
    result = p("gym 6-7pm")
    assert (result.start_time, result.end_time) == ("18:00", "19:00")


@pytest.mark.parametrize(
    "text,minutes",
    [
        ("gym at 6pm for an hour", 60),
        ("gym at 6pm for 2 hours", 120),
        ("gym at 6pm for 90 minutes", 90),
        ("gym at 6pm for 45 mins", 45),
        ("gym at 6pm for half an hour", 30),
    ],
)
def test_durations(text, minutes):
    result = p(text)
    start_h, start_m = map(int, result.start_time.split(":"))
    end_h, end_m = map(int, result.end_time.split(":"))
    assert (end_h * 60 + end_m) - (start_h * 60 + start_m) == minutes


def test_default_duration_comes_from_the_plugin_config():
    assert p("gym at 6pm", duration=45).end_time == "18:45"


# --- dates ------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("gym at 6pm", date(2026, 8, 18)),
        ("gym at 6pm today", date(2026, 8, 18)),
        ("gym at 6pm tomorrow", date(2026, 8, 19)),
        ("gym tomorrow at 6pm", date(2026, 8, 19)),
        ("gym at 6pm on 2026-09-01", date(2026, 9, 1)),
        ("gym at 6pm friday", date(2026, 8, 21)),
        ("gym at 6pm on monday", date(2026, 8, 24)),  # next Monday, not yesterday
    ],
)
def test_dates(text, expected):
    assert p(text).date == expected


def test_same_weekday_means_next_week():
    """Asked on a Tuesday, "tuesday" means the one coming, not today."""
    assert p("gym at 6pm tuesday").date == date(2026, 8, 25)


# --- titles -----------------------------------------------------------------


@pytest.mark.parametrize(
    "text,title",
    [
        ("gym at 6pm", "gym"),
        ("add gym at 6pm", "gym"),
        ("schedule a call with the team at 11am", "call with the team"),
        ("review the PR at 2pm", "review the PR"),
        ("1:1 with Priya at 3pm", "1:1 with Priya"),
        ("sprint 3 planning at 10am", "sprint 3 planning"),
        ("coffee with Sam 4-4:30pm", "coffee with Sam"),
    ],
)
def test_titles(text, title):
    assert p(text).title == title


def test_articles_survive_inside_a_title():
    """Trimming filler mid-string turns titles into telegrams."""
    assert p("review the PR at 2pm").title == "review the PR"
    assert p("call the bank at 10am").title == "call the bank"


# --- refusals (the important ones) ------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "buy milk",                 # no time at all
        "sprint 3",                 # a bare number is not a clock
        "meeting",
        "",
        "   ",
        "at 6pm",                   # a time with nothing to call it
        "gym at 25pm",              # impossible hour
        "gym at 6:99pm",            # impossible minute
        "gym 6-7",                  # ambiguous without am/pm
        "gym at 6pm for 0 minutes", # zero-length block
    ],
)
def test_returns_none_rather_than_guessing(text):
    assert p(text) is None


def test_none_for_backwards_range():
    assert p("gym 4pm-2pm") is None


def test_a_bare_verb_with_no_title_is_refused():
    """"block 1pm until 3pm" names no activity, so there is nothing to write."""
    assert p("block 1pm until 3pm") is None


def test_a_number_in_a_title_is_not_a_time():
    """"sprint 3" must not become 15:00."""
    assert p("sprint 3") is None
    assert p("sprint 3 at 10am").title == "sprint 3"


def test_bare_hour_hint_is_documented_behaviour():
    """"at 6" reads as evening, "at 9" as morning — and it is echoed back."""
    assert p("gym at 6").start_time == "18:00"
    assert p("standup at 9").start_time == "09:00"
