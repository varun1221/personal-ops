"""Interactive REPL for the personal ops agent.

    python cli.py                  # all three servers
    python cli.py --no-gmail       # skip Gmail (no OAuth needed)
    python cli.py --read-only      # skip the write server entirely
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid

# Importing readline transparently upgrades input() with line editing, history
# and sane paste handling. Without it the prompt has no arrow keys, no word
# delete, and a pasted newline submits mid-sentence. macOS ships libedit rather
# than GNU readline; both work for this, and neither is worth failing over.
try:
    import readline
except ImportError:  # pragma: no cover - Windows without pyreadline
    readline = None

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from agent import models
from agent.graph import build_graph
from agent.mcp_client import build_client, load_tools

console = Console()

CHECKPOINT_DB = "checkpoints.sqlite"


def ask_approval(request: dict) -> dict:
    """Render a pending write and collect the user's decision."""
    console.print()
    console.print(
        Panel(
            f"[bold]{request.get('description', request.get('tool'))}[/bold]\n\n"
            f"[dim]tool:[/dim] {request.get('tool')}\n"
            f"[dim]args:[/dim] {request.get('args')}",
            title="[yellow]Approval required[/yellow]",
            border_style="yellow",
        )
    )
    answer = Prompt.ask(
        "  [bold]Approve?[/bold]", choices=["y", "n"], default="n", show_default=True
    )
    if answer == "y":
        return {"approved": True}
    reason = Prompt.ask("  Why not? [dim](optional, helps it adapt)[/dim]", default="")
    return {"approved": False, "reason": reason or "the user declined"}


async def run_turn(graph, config: dict, user_input: str) -> None:
    payload: object = {"messages": [HumanMessage(content=user_input)]}

    while True:
        with console.status("[dim]thinking…[/dim]", spinner="dots"):
            result = await graph.ainvoke(payload, config)

        pending = result.get("__interrupt__")
        if not pending:
            break

        request = pending[0].value
        if request.get("type") != "approval_request":
            console.print(f"[red]Unexpected interrupt:[/red] {request}")
            break
        payload = Command(resume=ask_approval(request))

    for message in reversed(result["messages"]):
        if isinstance(message, AIMessage) and not message.tool_calls:
            console.print()
            console.print(getattr(message, "text", None) or message.content)
            break


def show_tools(tools) -> None:
    by_server: dict[str, list[str]] = {}
    for tool in tools:
        # MCP tool descriptions start with the docstring; group by known names.
        by_server.setdefault(_server_of(tool.name), []).append(tool.name)
    for server, names in by_server.items():
        console.print(f"  [cyan]{server}[/cyan]: {', '.join(sorted(names))}")


def _server_of(tool_name: str) -> str:
    from agent.mcp_client import GATED_TOOLS

    if tool_name in GATED_TOOLS:
        return "actions (write, gated)"
    if tool_name in {"search_email", "read_thread", "list_recent"}:
        return "gmail (read-only)"
    return "obsidian (read-only)"


async def main() -> int:
    parser = argparse.ArgumentParser(description="Personal ops agent")
    parser.add_argument("--no-gmail", action="store_true", help="skip the Gmail server")
    parser.add_argument("--read-only", action="store_true", help="skip the write server")
    parser.add_argument("--thread", default=None, help="resume a previous conversation id")
    args = parser.parse_args()

    load_dotenv()

    try:
        model = models.get_model()
    except (RuntimeError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        return 1

    client = build_client(
        include_gmail=not args.no_gmail, include_actions=not args.read_only
    )

    console.print("[dim]starting MCP servers…[/dim]")
    try:
        tools = await load_tools(client)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        return 1

    thread_id = args.thread or str(uuid.uuid4())

    console.print(
        Panel(
            f"[bold]Personal ops agent[/bold]\n"
            f"[dim]model:[/dim] {models.describe()}\n"
            f"[dim]thread:[/dim] {thread_id}\n"
            f"[dim]tools:[/dim] {len(tools)}",
            border_style="cyan",
        )
    )
    show_tools(tools)
    console.print("\n[dim]Ask a question, or 'exit' to quit.[/dim]")

    async with AsyncSqliteSaver.from_conn_string(CHECKPOINT_DB) as checkpointer:
        graph = build_graph(tools, model, checkpointer)
        config = {"configurable": {"thread_id": thread_id}}

        while True:
            try:
                # A pasted block arrives as several lines; join them so one
                # paste is one message rather than a half-sent sentence.
                user_input = console.input("\n[bold green]› [/bold green]").strip()
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]bye[/dim]")
                return 0

            if user_input.lower() in {"exit", "quit", "q"}:
                console.print("[dim]bye[/dim]")
                return 0
            if not user_input:
                continue

            try:
                await run_turn(graph, config, user_input)
            except KeyboardInterrupt:
                console.print("\n[yellow]interrupted[/yellow]")
            except Exception as exc:
                console.print(f"[red]{type(exc).__name__}:[/red] {exc}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
