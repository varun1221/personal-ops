"""What the agent remembers: Changes, Sightings, and Approvals.

History records *when*. Whether an Entry is open or done is always read from the
vault, never from here — see docs/adr/0004-history-is-recorded-at-write-time.md.

Stdlib sqlite3 and hand-written SQL: three small tables do not earn an ORM. The
file is shared by the actions server, the memory server and the fast path, each
its own process, so it runs in WAL mode and every call opens its own connection.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from vaultlib.tasks import _normalize

DB_NAME = "history.sqlite"

# One entry per schema version. A database at user_version N has had the first
# N applied; append, never edit one that has shipped.
MIGRATIONS = [
    """
    CREATE TABLE entries (
        id          INTEGER PRIMARY KEY,
        note_path   TEXT NOT NULL,
        description TEXT NOT NULL,
        key         TEXT NOT NULL
    );
    CREATE INDEX entries_by_key ON entries (note_path, key);
    CREATE TABLE changes (
        id        INTEGER PRIMARY KEY,
        entry_id  INTEGER NOT NULL REFERENCES entries(id),
        kind      TEXT NOT NULL,
        at        TEXT NOT NULL,
        via       TEXT NOT NULL,
        thread_id TEXT
    );
    CREATE INDEX changes_by_entry ON changes (entry_id, at);
    CREATE TABLE sightings (
        id                INTEGER PRIMARY KEY,
        source            TEXT NOT NULL,
        quote             TEXT NOT NULL,
        quote_key         TEXT NOT NULL,
        first_raised      TEXT NOT NULL,
        last_raised       TEXT NOT NULL,
        status            TEXT NOT NULL DEFAULT 'open',
        entry_note_path   TEXT,
        entry_description TEXT,
        resolved_at       TEXT,
        UNIQUE (source, quote_key)
    );
    CREATE TABLE approvals (
        id          INTEGER PRIMARY KEY,
        at          TEXT NOT NULL,
        tool        TEXT NOT NULL,
        args        TEXT NOT NULL,
        description TEXT NOT NULL,
        approved    INTEGER NOT NULL,
        reason      TEXT,
        thread_id   TEXT
    );
    """,
    # An Approval answers one tool call. Keyed on it, so a node re-run after a
    # crash between recording and finishing cannot record the answer twice.
    """
    ALTER TABLE approvals ADD COLUMN tool_call_id TEXT;
    CREATE UNIQUE INDEX approvals_once ON approvals (thread_id, tool_call_id);
    """,
]


def state_dir() -> Path:
    """Where ops keeps its databases. Never inside the vault, which syncs."""
    if explicit := os.environ.get("OPS_STATE_DIR"):
        return Path(explicit)
    if xdg := os.environ.get("XDG_STATE_HOME"):
        return Path(xdg) / "ops"
    return Path.home() / ".local" / "state" / "ops"


def _connect() -> sqlite3.Connection:
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(directory / DB_NAME, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
        conn.executescript(f"BEGIN; {script} PRAGMA user_version = {number}; COMMIT;")
    return conn


def entry_key(description: str) -> str:
    """How two wordings of one Entry are recognised as the same."""
    return re.sub(r"\s+", " ", _normalize(description)).strip().lower()


# --- Changes ----------------------------------------------------------------


@dataclass
class Change:
    kind: str
    at: datetime
    via: str


@dataclass
class EntryHistory:
    note_path: str
    description: str
    changes: list[Change] = field(default_factory=list)


def record_change(
    kind: str,
    note_path: str,
    description: str,
    *,
    via: str,
    at: datetime | None = None,
    new_note_path: str | None = None,
    new_description: str | None = None,
) -> None:
    """Record one Change to an Entry. Never raises.

    A rename or a move passes where the Entry ends up, so its History follows it
    there instead of starting over under the new wording.

    The write to the vault has already happened by the time this is called, and
    the vault is what the person cares about: a Change that could not be stored
    is a gap in History, not a reason to report the write as failed.
    """
    at = at or datetime.now()
    try:
        with closing(_connect()) as conn, conn:
            entry_id = _entry_id(conn, note_path, description)
            conn.execute(
                "INSERT INTO changes (entry_id, kind, at, via, thread_id) VALUES (?, ?, ?, ?, ?)",
                (entry_id, kind, at.isoformat(), via, os.environ.get("OPS_THREAD_ID")),
            )
            if new_note_path or new_description:
                _rekey(conn, entry_id, new_note_path or note_path, new_description or description)
    except (sqlite3.Error, OSError) as exc:
        print(f"history: could not record {kind} of {description!r}: {exc}", file=sys.stderr)


def _entry_id(conn: sqlite3.Connection, note_path: str, description: str) -> int:
    key = entry_key(description)
    # Newest first: two Entries can share wording in one note — a move lands
    # "Dentist" beside another "Dentist" — and the latest is the one being acted on.
    row = conn.execute(
        "SELECT id FROM entries WHERE note_path = ? AND key = ? ORDER BY id DESC",
        (note_path, key),
    ).fetchone()
    if row:
        conn.execute("UPDATE entries SET description = ? WHERE id = ?", (description, row["id"]))
        return row["id"]
    return conn.execute(
        "INSERT INTO entries (note_path, description, key) VALUES (?, ?, ?)",
        (note_path, description, key),
    ).lastrowid


def _rekey(conn: sqlite3.Connection, entry_id: int, note_path: str, description: str) -> None:
    conn.execute(
        "UPDATE entries SET note_path = ?, description = ?, key = ? WHERE id = ?",
        (note_path, description, entry_key(description), entry_id),
    )


def history_for(text: str) -> list[EntryHistory]:
    """Every Entry whose wording contains `text`, each with its Changes in order."""
    with closing(_connect()) as conn:
        entries = conn.execute(
            "SELECT * FROM entries WHERE key LIKE ? ESCAPE '\\' ORDER BY id",
            (f"%{_escape_like(entry_key(text))}%",),
        ).fetchall()
        return [_with_changes(conn, entry) for entry in entries]


def entry(note_path: str, description: str) -> EntryHistory | None:
    """One Entry's History, looked up exactly. None when nothing was recorded."""
    try:
        with closing(_connect()) as conn:
            row = conn.execute(
                "SELECT * FROM entries WHERE note_path = ? AND key = ? ORDER BY id DESC",
                (note_path, entry_key(description)),
            ).fetchone()
            return _with_changes(conn, row) if row else None
    except (sqlite3.Error, OSError):
        return None


def _with_changes(conn: sqlite3.Connection, entry: sqlite3.Row) -> EntryHistory:
    changes = conn.execute(
        "SELECT kind, at, via FROM changes WHERE entry_id = ? ORDER BY at, id",
        (entry["id"],),
    ).fetchall()
    return EntryHistory(
        note_path=entry["note_path"],
        description=entry["description"],
        changes=[Change(c["kind"], datetime.fromisoformat(c["at"]), c["via"]) for c in changes],
    )


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# --- Sightings --------------------------------------------------------------

OUTCOMES = ("captured", "dismissed")


@dataclass
class Sighting:
    id: int
    source: str
    quote: str
    first_raised: datetime
    last_raised: datetime
    status: str
    entry: tuple[str, str] | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Sighting:
        return cls(
            id=row["id"],
            source=row["source"],
            quote=row["quote"],
            first_raised=datetime.fromisoformat(row["first_raised"]),
            last_raised=datetime.fromisoformat(row["last_raised"]),
            status=row["status"],
            entry=(
                (row["entry_note_path"], row["entry_description"])
                if row["entry_note_path"]
                else None
            ),
        )


def record_sighting(source: str, quote: str, *, at: datetime | None = None) -> Sighting:
    """Note that a Commitment was shown to the person, and return what is known of it.

    Raising the same promise from the same source again keeps its first date — the
    point is being able to say "first raised six days ago" rather than "new".
    """
    at = at or datetime.now()
    with closing(_connect()) as conn, conn:
        conn.execute(
            """
            INSERT INTO sightings (source, quote, quote_key, first_raised, last_raised)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (source, quote_key) DO UPDATE SET last_raised = excluded.last_raised
            """,
            (source, quote.strip(), entry_key(quote), at.isoformat(), at.isoformat()),
        )
        row = conn.execute(
            "SELECT * FROM sightings WHERE source = ? AND quote_key = ?",
            (source, entry_key(quote)),
        ).fetchone()
        return Sighting.from_row(row)


def open_sightings() -> list[Sighting]:
    """Commitments raised and not yet captured or dismissed, oldest first."""
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM sightings WHERE status = 'open' ORDER BY first_raised, id"
        ).fetchall()
        return [Sighting.from_row(row) for row in rows]


def resolve_sighting(
    sighting_id: int,
    outcome: str,
    *,
    note_path: str | None = None,
    description: str | None = None,
    at: datetime | None = None,
) -> Sighting:
    """Close a Sighting: captured as an Entry (named), or dismissed."""
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {', '.join(OUTCOMES)}, got {outcome!r}")
    at = at or datetime.now()
    with closing(_connect()) as conn, conn:
        updated = conn.execute(
            """
            UPDATE sightings
            SET status = ?, entry_note_path = ?, entry_description = ?, resolved_at = ?
            WHERE id = ?
            """,
            (outcome, note_path, description, at.isoformat(), sighting_id),
        )
        if updated.rowcount == 0:
            raise LookupError(f"no sighting with id {sighting_id}")
        row = conn.execute("SELECT * FROM sightings WHERE id = ?", (sighting_id,)).fetchone()
        return Sighting.from_row(row)


# --- Approvals --------------------------------------------------------------


@dataclass
class ApprovalRecord:
    at: datetime
    tool: str
    args: dict
    description: str
    approved: bool
    reason: str | None


def record_approval(
    tool: str,
    args: dict,
    description: str,
    *,
    approved: bool,
    reason: str | None,
    thread_id: str | None,
    tool_call_id: str | None = None,
    at: datetime | None = None,
) -> None:
    """Record one answered Approval. Never raises, for the same reason as record_change.

    Recording the same `(thread_id, tool_call_id)` again is a no-op.
    """
    at = at or datetime.now()
    try:
        with closing(_connect()) as conn, conn:
            conn.execute(
                """
                INSERT INTO approvals
                    (at, tool, args, description, approved, reason, thread_id, tool_call_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO NOTHING
                """,
                (
                    at.isoformat(),
                    tool,
                    json.dumps(args, default=str),
                    description,
                    int(approved),
                    reason,
                    thread_id,
                    tool_call_id,
                ),
            )
    except (sqlite3.Error, OSError) as exc:
        print(f"history: could not record approval of {tool}: {exc}", file=sys.stderr)


def past_rejections(tool: str | None = None, limit: int = 20) -> list[ApprovalRecord]:
    """Approvals answered no, newest first."""
    query = "SELECT * FROM approvals WHERE approved = 0"
    params: list = []
    if tool:
        query += " AND tool = ?"
        params.append(tool)
    query += " ORDER BY at DESC, id DESC LIMIT ?"
    params.append(limit)
    with closing(_connect()) as conn:
        return [
            ApprovalRecord(
                at=datetime.fromisoformat(row["at"]),
                tool=row["tool"],
                args=json.loads(row["args"]),
                description=row["description"],
                approved=bool(row["approved"]),
                reason=row["reason"],
            )
            for row in conn.execute(query, params).fetchall()
        ]
