"""Memory MCP server — History, Sightings and past rejections.

Reads the history store, and writes to it — never to the vault, so nothing here
is gated. The vault stays the authority on whether an Entry is open: every
answer is checked against it as it is now, and History only supplies *when*.

Run standalone for inspection:
    npx @modelcontextprotocol/inspector .venv/bin/python servers/memory/server.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import warnings

# See servers/actions/server.py: harmless, and noisy on every server start.
warnings.filterwarnings("ignore", message=".*incomplete definition.*")

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

import history
from history.answers import describe, stamp
from vaultlib.paths import VaultPaths

load_dotenv()

mcp = FastMCP("memory", log_level="WARNING")

NO_HISTORY = (
    "No history recorded for that. This is inconclusive, not a no: history only "
    "covers changes made through ops or the agent, so it may have been done by "
    "hand. Check the vault before telling the user it never happened."
)


def vault() -> VaultPaths:
    return VaultPaths.from_env()


@mcp.tool()
def entry_history(description: str) -> str:
    """When a task or event was created, completed, renamed, moved or deleted.

    Use this for "when did I…" and "how long has … been open". Status comes from
    the vault as it is now; the dates come from what ops and the agent recorded.
    An empty result is inconclusive — say so, do not conclude it never happened.

    Args:
        description: Some of the entry's wording, e.g. "send sarah".
    """
    paths = vault()
    entries = history.history_for(description)
    result: dict = {"entries": [describe(paths, entry) for entry in entries]}
    if not entries:
        result["note"] = NO_HISTORY
    return json.dumps(result, indent=2)


def _sighting(sighting: history.Sighting) -> dict:
    return {
        "id": sighting.id,
        "source": sighting.source,
        "quote": sighting.quote,
        "status": sighting.status,
        "first_raised": stamp(sighting.first_raised),
        "days_since_first_raised": (datetime.now() - sighting.first_raised).days,
        **(
            {"entry": {"note_path": sighting.entry.note_path, "description": sighting.entry.description}}
            if sighting.entry
            else {}
        ),
    }


@mcp.tool()
def record_sighting(source: str, quote: str) -> str:
    """Record that you are showing the user a commitment. Call it for every one.

    Call this for each commitment you raise — a promise found in prose, not an
    existing task. It returns when it was first raised, so you can tell the user
    "first raised 6 days ago" instead of presenting it as new. If its status is
    not "open", it was already captured or dismissed: do not raise it as new.

    Args:
        source: Where you found it — the vault-relative note path, or
            "gmail:<message id>" for an email.
        quote: The promise, quoted as it appears.
    """
    if not source.strip() or not quote.strip():
        return json.dumps({"error": "source and quote must both be given"})
    return json.dumps(_sighting(history.record_sighting(source, quote)), indent=2)


@mcp.tool()
def open_sightings() -> str:
    """Commitments already raised with the user and not yet captured or dismissed.

    Oldest first. Use it for "what do I still owe people?" alongside searching.
    """
    return json.dumps(
        {"sightings": [_sighting(s) for s in history.open_sightings()]}, indent=2
    )


@mcp.tool()
def resolve_sighting(
    sighting_id: int,
    outcome: str,
    note_path: str | None = None,
    description: str | None = None,
) -> str:
    """Close a commitment: it was captured as a task or event, or the user dismissed it.

    Call with "captured" after a write for it succeeds, naming the entry; call
    with "dismissed" when the user says it is not a real commitment.

    Args:
        sighting_id: The id from record_sighting or open_sightings.
        outcome: "captured" or "dismissed".
        note_path: For "captured", the note the entry was written to.
        description: For "captured", the entry's wording.
    """
    try:
        sighting = history.resolve_sighting(
            sighting_id, outcome, note_path=note_path, description=description
        )
    except (ValueError, LookupError) as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps(_sighting(sighting), indent=2)


@mcp.tool()
def past_rejections(tool: str | None = None) -> str:
    """Writes the user has said no to before, newest first.

    Check this before proposing a write that looks like one already refused.

    Args:
        tool: Only rejections of this tool, e.g. "delete_event".
    """
    return json.dumps(
        {
            "rejections": [
                {
                    "at": stamp(r.at),
                    "tool": r.tool,
                    "description": r.description,
                    "reason": r.reason,
                    "args": r.args,
                }
                for r in history.past_rejections(tool)
            ]
        },
        indent=2,
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
