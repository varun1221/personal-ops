"""Wiring the MCP servers into LangGraph.

Each server runs as its own stdio subprocess. The client launches them, speaks
MCP over stdin/stdout, and hands back LangChain tools.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVERS_DIR = PROJECT_ROOT / "servers"

# Tools that mutate the vault. The graph pauses for approval before dispatching
# any of these; see agent/graph.py.
GATED_TOOLS = {
    "create_calendar_event",
    "add_task",
    "move_event",
    "delete_event",
    "complete_task",
    "rename_entry",
}


def _python() -> str:
    """The interpreter to launch servers with — the current venv's, not the system's."""
    return sys.executable


def server_config(include_gmail: bool = True, include_actions: bool = True) -> dict:
    """Build the connection map. Servers are opt-out so early phases can run lean."""
    config = {
        "obsidian": {
            "command": _python(),
            "args": [str(SERVERS_DIR / "obsidian" / "server.py")],
            "transport": "stdio",
            "env": dict(os.environ),
        }
    }
    if include_gmail:
        config["gmail"] = {
            "command": _python(),
            "args": [str(SERVERS_DIR / "gmail" / "server.py")],
            "transport": "stdio",
            "env": dict(os.environ),
        }
    if include_actions:
        config["actions"] = {
            "command": _python(),
            "args": [str(SERVERS_DIR / "actions" / "server.py")],
            "transport": "stdio",
            "env": dict(os.environ),
        }
    return config


def build_client(**kwargs) -> MultiServerMCPClient:
    return MultiServerMCPClient(server_config(**kwargs))


async def load_tools(client: MultiServerMCPClient) -> list[BaseTool]:
    """Load tools from every configured server.

    get_tools() gathers across servers without return_exceptions, so one server
    failing to start takes down the whole load — you get an opaque error rather
    than a partial toolset. Loading per-server turns that into a named failure.
    """
    tools: list[BaseTool] = []
    failures: list[str] = []

    for name in client.connections:
        try:
            tools.extend(await client.get_tools(server_name=name))
        except Exception as exc:  # noqa: BLE001 - surfacing which server broke is the point
            failures.append(f"  {name}: {type(exc).__name__}: {exc}")

    if failures:
        print(
            "Some MCP servers failed to start:\n" + "\n".join(failures),
            file=sys.stderr,
        )
    if not tools:
        raise RuntimeError(
            "No MCP tools loaded. Test a server standalone with:\n"
            f"  npx @modelcontextprotocol/inspector {_python()} servers/obsidian/server.py"
        )
    return tools
