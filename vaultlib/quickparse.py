"""Deterministic parsing for formulaic capture commands.

Most captures are the same handful of shapes — "gym at 6pm", "standup 9-9:15
tomorrow", "review PR at 2pm for 90 minutes". Sending those to a language model
costs a second of latency, an API call and a rate-limit slot to reach a
conclusion a regex reaches instantly.

So this handles the shapes it is sure about and returns None for everything
else, which the caller sends to the agent instead. Being *sure* is the whole
contract: a wrong guess here writes a wrong entry without anyone reviewing it,
so every ambiguous case must return None rather than a best effort.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2,
    "wed": 2, "thursday": 3, "thu": 3, "thur": 3, "thurs": 3, "friday": 4,
    "fri": 4, "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
}

# Filler that survives extraction and should not end up in a title. Only the
# edges are trimmed: "review the PR" must keep its article, or titles come out
# reading like telegrams.
FILLER = re.compile(
    r"^(?:add|create|schedule|book|set|put|block|new)\b\s*"
    r"|^(?:a|an|the|my)\b\s*"
    r"|^(?:at|on|for|to|in|from)\b\s*"
    r"|\s*\b(?:at|on|for|to|in|from)$",
    re.IGNORECASE,
)

_MERIDIEM = r"(?:\s*(?P<mer>am|pm|a\.m\.|p\.m\.))"
_HHMM = r"(?P<h>\d{1,2})(?::(?P<m>\d{2}))?"

TIME_RANGE = re.compile(
    rf"\b(?P<h1>\d{{1,2}})(?::(?P<m1>\d{{2}}))?(?:\s*(?P<mer1>am|pm))?"
    rf"\s*(?:-|–|—|to|until|till)\s*"
    rf"(?P<h2>\d{{1,2}})(?::(?P<m2>\d{{2}}))?(?:\s*(?P<mer2>am|pm))?\b",
    re.IGNORECASE,
)
# Anchored first: "1:1 with Priya at 3pm" must take 3pm, not read the meeting's
# name as a clock. Only if nothing is anchored do we accept a loose time.
TIME_ANCHORED = re.compile(rf"\b(?:at|@|from)\s*{_HHMM}{_MERIDIEM}?\b", re.IGNORECASE)
TIME_AT = re.compile(rf"\b{_HHMM}{_MERIDIEM}?\b", re.IGNORECASE)
NAMED_TIME = re.compile(r"\b(?P<name>noon|midday|midnight)\b", re.IGNORECASE)
DURATION = re.compile(
    r"\bfor\s+(?:(?P<num>\d+(?:\.\d+)?)|(?P<word>an?|half\s+an?))\s*"
    r"(?P<unit>hours?|hrs?|h|minutes?|mins?|m)\b",
    re.IGNORECASE,
)
ISO_DATE = re.compile(r"\b(?P<iso>\d{4}-\d{2}-\d{2})\b")
REL_DATE = re.compile(r"\b(?P<rel>today|tonight|tomorrow|tmr|tmrw)\b", re.IGNORECASE)
WEEKDAY = re.compile(
    rf"\b(?:(?P<next>next|this)\s+)?(?P<day>{'|'.join(WEEKDAYS)})\b", re.IGNORECASE
)


@dataclass
class Capture:
    title: str
    date: date
    start_time: str | None = None
    end_time: str | None = None

    @property
    def date_iso(self) -> str:
        return self.date.isoformat()


def _clock(hour: int, minute: int, meridiem: str | None, *, bare_hint: bool = False) -> str | None:
    """Normalise to HH:MM, or None when the input cannot be trusted."""
    if meridiem:
        meridiem = meridiem.replace(".", "").lower()
        if hour < 1 or hour > 12:
            return None
        if meridiem == "pm" and hour != 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
    elif bare_hint and hour <= 7:
        # "at 6" almost always means the evening; "at 9" the morning. Stated
        # plainly because the caller echoes the resolved time back.
        hour += 12
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


def _add_minutes(clock: str, minutes: int) -> str:
    moment = datetime.strptime(clock, "%H:%M") + timedelta(minutes=minutes)
    return moment.strftime("%H:%M")


def _duration_minutes(match: re.Match) -> int | None:
    unit = match.group("unit").lower()
    if word := match.group("word"):
        amount = 0.5 if word.lower().startswith("half") else 1.0
    else:
        amount = float(match.group("num"))
    if unit.startswith(("hour", "hr", "h")):
        return int(amount * 60)
    return int(amount)


def _resolve_date(text: str, today: date) -> tuple[date, str]:
    """Return the date and the text with its date phrase removed."""
    if match := ISO_DATE.search(text):
        try:
            return date.fromisoformat(match.group("iso")), _cut(text, match)
        except ValueError:
            pass

    if match := REL_DATE.search(text):
        word = match.group("rel").lower()
        offset = 1 if word in ("tomorrow", "tmr", "tmrw") else 0
        return today + timedelta(days=offset), _cut(text, match)

    if match := WEEKDAY.search(text):
        target = WEEKDAYS[match.group("day").lower()]
        ahead = (target - today.weekday()) % 7
        if ahead == 0 or (match.group("next") or "").lower() == "next":
            ahead = ahead or 7
            if (match.group("next") or "").lower() == "next" and ahead < 7:
                ahead += 7
        return today + timedelta(days=ahead), _cut(text, match)

    return today, text


def _cut(text: str, match: re.Match) -> str:
    return (text[: match.start()] + " " + text[match.end() :]).strip()


def _clean_title(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip(" ,.-–—:")
    previous = None
    while previous != text:
        previous = text
        text = FILLER.sub(" ", text).strip(" ,.-–—:")
        text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_capture(
    text: str, today: date | None = None, default_duration_minutes: int = 30
) -> Capture | None:
    """Parse "gym at 6pm tomorrow for an hour" into a dated time block.

    Returns None whenever the result would be a guess — no time found, an
    unparseable clock, or nothing left over to use as a title.
    """
    today = today or date.today()
    remaining = text.strip()
    if not remaining:
        return None

    when, remaining = _resolve_date(remaining, today)

    duration = None
    if match := DURATION.search(remaining):
        duration = _duration_minutes(match)
        remaining = _cut(remaining, match)

    start = end = None

    if match := TIME_RANGE.search(remaining):
        mer2 = match.group("mer2")
        # "6-7pm" means both are pm; a trailing meridiem governs both ends.
        mer1 = match.group("mer1") or mer2
        start = _clock(int(match.group("h1")), int(match.group("m1") or 0), mer1)
        end = _clock(int(match.group("h2")), int(match.group("m2") or 0), mer2)
        if start and end:
            # "6-7" could be morning or evening. Refuse unless something pins it:
            # a meridiem, explicit minutes, or a 24-hour value.
            pinned = bool(mer1 or mer2 or match.group("m1") or match.group("m2")
                          or int(match.group("h1")) > 12 or int(match.group("h2")) > 12)
            if not pinned or end <= start:
                return None
            remaining = _cut(remaining, match)
        else:
            start = end = None

    if start is None:
        if match := NAMED_TIME.search(remaining):
            start = {"noon": "12:00", "midday": "12:00", "midnight": "00:00"}[
                match.group("name").lower()
            ]
            remaining = _cut(remaining, match)
        else:
            match = TIME_ANCHORED.search(remaining)
            if match is None:
                match = TIME_AT.search(remaining)
                # An unanchored number is only a time if it carries am/pm or
                # minutes; otherwise it is part of the title ("sprint 3").
                if match and not (match.group("mer") or match.group("m")):
                    return None
            if match is None:
                return None
            start = _clock(
                int(match.group("h")),
                int(match.group("m") or 0),
                match.group("mer"),
                bare_hint=not match.group("mer"),
            )
            if start is None:
                return None
            remaining = _cut(remaining, match)

    if start is None:
        return None

    if duration is not None and duration <= 0:
        return None  # "for 0 minutes" is not a block; 0 is falsy, so check first.
    if end is None:
        end = _add_minutes(start, default_duration_minutes if duration is None else duration)
    elif duration is not None:
        end = _add_minutes(start, duration)

    if end <= start:
        return None

    title = _clean_title(remaining)
    if not title:
        return None

    return Capture(title=title, date=when, start_time=start, end_time=end)
