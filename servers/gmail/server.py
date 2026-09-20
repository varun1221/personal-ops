"""Gmail MCP server — READ-ONLY.

Scope is gmail.readonly, so the safety boundary is enforced by Google's API rather
than by this code remembering to be careful. There is no send, modify, or delete
path here and the token could not authorize one.

Auth is lazy: the server starts and lists its tools without credentials, and only
prompts for consent on the first real call. An eager connect would make a missing
credentials.json look like "the whole agent is broken" instead of "Gmail needs setup".

First run opens a browser for consent. Run standalone to get that out of the way:
    npx @modelcontextprotocol/inspector .venv/bin/python servers/gmail/server.py
"""

from __future__ import annotations

import base64
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import warnings

# pydantic-settings emits an IncompleteFieldDefinitionWarning while the MCP
# library imports. It is internal to that library, harmless, and prints to
# stderr on every server start — which means twice on every CLI launch.
warnings.filterwarnings("ignore", message=".*incomplete definition.*")

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

mcp = FastMCP("gmail", log_level="WARNING")

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

_service = None


class GmailAuthError(RuntimeError):
    pass


def _credentials_path() -> Path:
    return Path(os.environ.get("GMAIL_CREDENTIALS_PATH", "./credentials.json")).expanduser()


def _token_path() -> Path:
    return Path(os.environ.get("GMAIL_TOKEN_PATH", "./token.json")).expanduser()


def service():
    """Build (once) an authorized Gmail client."""
    global _service
    if _service is not None:
        return _service

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    token_path = _token_path()
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            credentials_path = _credentials_path()
            if not credentials_path.exists():
                raise GmailAuthError(
                    f"No OAuth client secrets at {credentials_path}. Create a Google "
                    "Cloud project, enable the Gmail API, create an OAuth client ID of "
                    "type 'Desktop app', download the JSON, and point "
                    "GMAIL_CREDENTIALS_PATH at it."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")

    _service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    return _service


def _header(payload: dict, name: str) -> str:
    for header in payload.get("headers", []):
        if header.get("name", "").lower() == name.lower():
            return header.get("value", "")
    return ""


def _body_text(payload: dict) -> str:
    """Depth-first walk for the first text/plain part, falling back to HTML."""
    mime = payload.get("mimeType", "")
    data = payload.get("body", {}).get("data")

    if mime == "text/plain" and data:
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

    for part in payload.get("parts", []) or []:
        if text := _body_text(part):
            return text

    if mime == "text/html" and data:
        import re

        html = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        return re.sub(r"<[^>]+>", " ", html)
    return ""


def _summarize(message: dict) -> dict:
    payload = message.get("payload", {})
    return {
        "id": message.get("id"),
        "thread_id": message.get("threadId"),
        "from": _header(payload, "From"),
        "to": _header(payload, "To"),
        "subject": _header(payload, "Subject"),
        "date": _header(payload, "Date"),
        "snippet": message.get("snippet", ""),
    }


def _guard(fn):
    """Turn auth and API failures into tool-level errors the model can read."""

    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except GmailAuthError as exc:
            return json.dumps({"error": str(exc), "needs_setup": True})
        except Exception as exc:
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"})

    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


@mcp.tool()
@_guard
def search_email(query: str, max_results: int = 10) -> str:
    """Search the inbox using Gmail's own query syntax.

    Supports operators like from:, to:, subject:, after:, before:, has:attachment.
    Dates in after:/before: are YYYY/MM/DD.

    Args:
        query: A Gmail search query, e.g. 'from:sarah after:2026/08/10'.
        max_results: Maximum messages to return.
    """
    api = service()
    listing = (
        api.users()
        .messages()
        .list(userId="me", q=query, maxResults=max(1, min(max_results, 50)))
        .execute()
    )

    messages = []
    for stub in listing.get("messages", []):
        full = (
            api.users()
            .messages()
            .get(userId="me", id=stub["id"], format="metadata",
                 metadataHeaders=["From", "To", "Subject", "Date"])
            .execute()
        )
        messages.append(_summarize(full))

    return json.dumps(
        {"query": query, "result_count": len(messages), "emails": messages}, indent=2
    )


@mcp.tool()
@_guard
def read_thread(thread_id: str) -> str:
    """Read a full email thread, including message bodies.

    Args:
        thread_id: The thread_id from a search_email or list_recent result.
    """
    api = service()
    thread = api.users().threads().get(userId="me", id=thread_id, format="full").execute()

    messages = []
    for message in thread.get("messages", []):
        summary = _summarize(message)
        body = _body_text(message.get("payload", {})).strip()
        # Long threads blow the context window; the tail carries the commitment.
        summary["body"] = body[:4000] + ("\n...[truncated]" if len(body) > 4000 else "")
        messages.append(summary)

    return json.dumps(
        {"thread_id": thread_id, "message_count": len(messages), "messages": messages}, indent=2
    )


@mcp.tool()
@_guard
def list_recent(days: int = 7, max_results: int = 20) -> str:
    """List recent inbox messages from the last N days.

    Args:
        days: How many days back to look.
        max_results: Maximum messages to return.
    """
    after = (date.today() - timedelta(days=max(1, days))).strftime("%Y/%m/%d")
    return search_email(f"in:inbox after:{after}", max_results)


if __name__ == "__main__":
    mcp.run(transport="stdio")
