"""ops — one-shot capture for the day planner.

    ops add gym at 6pm            capture a block
    ops                           today's plan, overdue first
    ops todo                      every open task in the vault
    ops today / ops tomorrow      a specific day
    ops week                      the week at a glance
    ops done standup              tick something off
    ops free 90                   gaps big enough for 90 minutes
    ops rm gym                    remove a block (asks first)
    ops ask <anything>            full agent, for whatever the above cannot do
    ops chat                      the interactive REPL

Most commands never touch a language model: they parse locally and edit the
vault directly, which costs about 0.1s and no API call. `ask`, and `add` when
the phrasing is not one it is sure about, fall back to the agent.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
from rich.console import Console
from rich.markup import escape

from vaultlib.agenda import (
    Agenda,
    collapse_overdue,
    collect_open_tasks,
    locate_open_task,
    split_agenda,
)
from vaultlib.dayplanner import PlannerConfig, parse_planner_note
from vaultlib.paths import VaultPathError, VaultPaths
from vaultlib.planner_write import (
    _delete_planner_line,
    _locate_planner_entry,
    _locate_task_line,
    _write_planner_event,
)
from vaultlib.quickparse import parse_capture
from vaultlib.tasks import set_checkbox

console = Console()
load_dotenv()

RELATIVE_DAYS = {"today": 0, "tomorrow": 1, "tmr": 1, "yesterday": -1}


def die(message: str) -> int:
    console.print(f"[red]{message}[/red]")
    return 1


def vault() -> VaultPaths:
    return VaultPaths.from_env()


def resolve_day(word: str | None) -> date:
    if not word:
        return date.today()
    if word.lower() in RELATIVE_DAYS:
        return date.today() + timedelta(days=RELATIVE_DAYS[word.lower()])
    try:
        return date.fromisoformat(word)
    except ValueError:
        return date.today()


def entries_for(paths: VaultPaths, day: date) -> list:
    note = paths.daily_dir / f"{day.isoformat()}.md"
    if not note.exists():
        return []
    config = PlannerConfig.from_vault(paths.root)
    return parse_planner_note(note, paths.relative(note), config, day)


def agenda_for(paths: VaultPaths, day: date) -> Agenda:
    """The untimed tasks attached to a day.

    Overdue is debt as of *today*, so only today's view carries it: asking what
    is late on a day that has not arrived is a category error, and showing
    today's misses under tomorrow's heading would file them as tomorrow's plan.
    """
    agenda = split_agenda(collect_open_tasks(paths), day)
    if day != date.today():
        return Agenda(overdue=[], due=agenda.due)
    return agenda


def show_overdue(agenda: Agenda) -> None:
    """Debt, above the plan, with the date it was missed."""
    if not agenda.overdue:
        return
    shown, hidden = collapse_overdue(agenda.overdue)
    console.print("\n[bold red]overdue[/bold red]")
    for task in shown:
        console.print(
            f"  [red]![/red] {escape(task.description)}"
            f"  [dim]{task.due.strftime('%a %-d %b')}[/dim]"
        )
    if hidden:
        console.print(
            f"  [dim]… and {hidden} more overdue ([/dim]ops todo[dim])[/dim]"
        )


def show_day(
    paths: VaultPaths,
    day: date,
    highlight: str | None = None,
    with_tasks: bool = True,
) -> None:
    agenda = agenda_for(paths, day) if with_tasks else Agenda(overdue=[], due=[])
    show_overdue(agenda)

    entries = entries_for(paths, day)
    label = day.strftime("%A %-d %B")
    if day == date.today():
        label += "  [dim]today[/dim]"

    console.print(f"\n[bold]{label}[/bold]")
    if not entries and agenda.is_empty:
        console.print("  [dim]nothing planned[/dim]")
        return
    if not entries:
        console.print("  [dim]nothing scheduled[/dim]")

    booked = 0
    for entry in sorted(entries, key=lambda e: e.start_time):
        done = entry.status == "done"
        mark = "[green]✓[/green]" if done else "[dim]·[/dim]"
        safe = escape(entry.description)
        title = f"[dim strike]{safe}[/dim strike]" if done else safe
        if highlight and entry.description == highlight:
            title = f"[bold yellow]{safe}[/bold yellow]"
        console.print(f"  {mark} [cyan]{entry.start_time}–{entry.end_time}[/cyan]  {title}")
        if not done:
            booked += _minutes(entry.end_time) - _minutes(entry.start_time)

    if booked:
        console.print(f"  [dim]{booked // 60}h {booked % 60:02d}m still to do[/dim]")

    if agenda.due:
        console.print("\n  [dim]due, no time set[/dim]")
        for task in agenda.due:
            console.print(f"  [dim]·[/dim] {escape(task.description)}")


def _minutes(clock: str) -> int:
    hours, _, mins = clock.partition(":")
    return int(hours) * 60 + int(mins)


# --------------------------------------------------------------------------- add


def cmd_add(text: str) -> int:
    paths = vault()
    config = PlannerConfig.from_vault(paths.root)
    capture = parse_capture(text, default_duration_minutes=config.default_duration_minutes)

    if capture is None:
        console.print("[dim]not a shape I can parse — asking the agent…[/dim]")
        return run_agent(f"Add this to my planner: {text}")

    result = _write_planner_event(
        paths, capture.title, capture.date_iso, capture.start_time, capture.end_time
    )
    if "error" in result:
        return die(result["error"])

    console.print(
        f"[green]added[/green]  [cyan]{capture.start_time}–{capture.end_time}[/cyan]  "
        f"{escape(capture.title)}  [dim]{capture.date.strftime('%a %-d %b')}[/dim]"
    )
    console.print(f"[dim]{result['note_path']}[/dim]")
    return 0


# ------------------------------------------------------------------------- done


def cmd_done(text: str, day_word: str | None = None, done: bool = True) -> int:
    paths = vault()

    if done:
        # The day view now shows tasks from notes other than today's, so ticking
        # one off has to search where they live. Restricting that search to open
        # tasks is what keeps it usable: a "standup" finished three months ago
        # cannot collide with today's.
        found = locate_open_task(paths, text)
    else:
        # Reopening still searches only the day's own note. Vault-wide over
        # *closed* tasks would match every standup ever ticked off, and refuse.
        day = resolve_day(day_word)
        found = _locate_task_line(
            paths, f"{paths.daily_folder}/{day.isoformat()}.md", text, None
        )

    if isinstance(found, dict):
        return die(found["error"])
    target, index, task = found

    lines = target.read_text(encoding="utf-8").splitlines()
    before = lines[index]
    after = set_checkbox(before, "x" if done else " ")
    if before == after:
        console.print(f"[dim]already {'done' if done else 'open'}:[/dim] {task.description}")
        return 0

    lines[index] = after
    target.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    verb = "done" if done else "reopened"
    console.print(f"[green]{verb}[/green]  {escape(after.strip())}")
    console.print(f"[dim]{paths.relative(target)}[/dim]")
    return 0


# -------------------------------------------------------------------------- todo


def cmd_todo() -> int:
    """Every open task in the vault — the deliberate list, not the daily glance."""
    paths = vault()
    tasks = collect_open_tasks(paths)

    console.print("\n[bold]open tasks[/bold]")
    if not tasks:
        console.print("  [dim]nothing open[/dim]")
        return 0

    today = date.today()
    for task in tasks:
        if task.due is None:
            when = "[dim]someday[/dim]"
        elif task.due < today:
            when = f"[red]{task.due.strftime('%a %-d %b')}[/red]"
        elif task.due == today:
            when = "[yellow]today[/yellow]"
        else:
            when = f"[dim]{task.due.strftime('%a %-d %b')}[/dim]"
        console.print(
            f"  [dim]·[/dim] {escape(task.description)}  {when}"
            f"  [dim]{task.note_path}[/dim]"
        )

    console.print(f"  [dim]{len(tasks)} open[/dim]")
    return 0


# --------------------------------------------------------------------------- rm


def cmd_rm(text: str, day_word: str | None = None) -> int:
    paths = vault()
    day = resolve_day(day_word)
    note_path = f"{paths.daily_folder}/{day.isoformat()}.md"

    found = _locate_planner_entry(paths, note_path, text, None)
    if isinstance(found, dict):
        return die(found["error"])
    target, entry = found

    # Deleting is the one thing here that loses data, so it asks.
    console.print(
        f"\n[yellow]remove[/yellow]  [cyan]{entry.start_time}–{entry.end_time}[/cyan]  "
        f"{escape(entry.description)}  [dim]{day.strftime('%a %-d %b')}[/dim]"
    )
    if console.input("  [bold]delete it?[/bold] [dim](y/N)[/dim] ").strip().lower() != "y":
        console.print("[dim]left alone[/dim]")
        return 0

    removed = _delete_planner_line(target, entry)
    console.print(f"[green]removed[/green]  {escape(removed.strip())}")
    return 0


# ------------------------------------------------------------------------- free


def cmd_free(minutes: int, day_word: str | None = None) -> int:
    paths = vault()
    day = resolve_day(day_word)
    busy = sorted(
        (_minutes(e.start_time), _minutes(e.end_time))
        for e in entries_for(paths, day)
        if e.status != "done"
    )

    window_start, window_end = _minutes("09:00"), _minutes("18:00")
    free, cursor = [], window_start
    for start, end in busy:
        if end <= window_start or start >= window_end:
            continue
        if start - cursor >= minutes:
            free.append((cursor, start))
        cursor = max(cursor, end)
    if window_end - cursor >= minutes:
        free.append((cursor, window_end))

    console.print(f"\n[bold]{day.strftime('%A %-d %B')}[/bold]  [dim]gaps ≥ {minutes}m, 09:00–18:00[/dim]")
    if not free:
        console.print("  [dim]nothing that long is open[/dim]")
    for start, end in free:
        span = end - start
        console.print(
            f"  [cyan]{start // 60:02d}:{start % 60:02d}–{end // 60:02d}:{end % 60:02d}[/cyan]"
            f"  [dim]{span // 60}h {span % 60:02d}m[/dim]"
        )
    return 0


# ------------------------------------------------------------------------- week


def cmd_week() -> int:
    paths = vault()
    monday = date.today() - timedelta(days=date.today().weekday())
    for offset in range(7):
        # A week is a glance at where time is booked; seven task lists is
        # not a glance. `ops todo` is the list.
        show_day(paths, monday + timedelta(days=offset), with_tasks=False)
    return 0


# ------------------------------------------------------------------- agent path


def run_agent(question: str) -> int:
    """Fall back to the full agent. Imports langchain lazily — it costs ~0.8s."""
    import asyncio

    from cli import run_turn  # noqa: PLC0415 - deliberate lazy import
    from langgraph.checkpoint.memory import MemorySaver

    from agent import models
    from agent.graph import build_graph
    from agent.mcp_client import build_client, load_tools

    async def main() -> int:
        try:
            model = models.get_model()
        except (RuntimeError, ValueError) as exc:
            return die(str(exc))
        client = build_client(include_gmail=False, include_actions=True)
        try:
            tools = await load_tools(client)
        except RuntimeError as exc:
            return die(str(exc))
        graph = build_graph(tools, model, MemorySaver())
        await run_turn(graph, {"configurable": {"thread_id": "ops-oneshot"}}, question)
        return 0

    return asyncio.run(main())


# -------------------------------------------------------------------------- main

USAGE = """[bold]ops[/bold] — day planner capture

  [cyan]ops[/cyan]                        today's plan, overdue first
  [cyan]ops todo[/cyan]                   every open task in the vault
  [cyan]ops add[/cyan] gym at 6pm         capture a block
  [cyan]ops add[/cyan] standup 9-9:15 tomorrow
  [cyan]ops today[/cyan] / [cyan]tomorrow[/cyan]     a specific day
  [cyan]ops week[/cyan]                   the week
  [cyan]ops done[/cyan] standup           tick it off
  [cyan]ops undone[/cyan] standup         put it back
  [cyan]ops free[/cyan] 90                gaps of at least 90 minutes
  [cyan]ops rm[/cyan] gym                 remove a block (asks first)
  [cyan]ops ask[/cyan] what do I owe Sarah?   full agent
  [cyan]ops chat[/cyan]                   interactive session
"""


def main(argv: list[str]) -> int:
    if not argv:
        return show_day(vault(), date.today()) or 0

    command, rest = argv[0].lower(), argv[1:]
    text = " ".join(rest).strip()

    if command in ("-h", "--help", "help"):
        console.print(USAGE)
        return 0
    if command == "add":
        return cmd_add(text) if text else die("what should I add?")
    if command in RELATIVE_DAYS:
        return show_day(vault(), resolve_day(command)) or 0
    if command == "week":
        return cmd_week()
    if command in ("todo", "todos", "tasks"):
        return cmd_todo()
    if command == "done":
        return cmd_done(text) if text else die("mark what as done?")
    if command == "undone":
        return cmd_done(text, done=False) if text else die("reopen what?")
    if command == "rm":
        return cmd_rm(text) if text else die("remove what?")
    if command == "free":
        minutes = int(rest[0]) if rest and rest[0].isdigit() else 30
        return cmd_free(minutes, rest[1] if len(rest) > 1 else None)
    if command == "ask":
        return run_agent(text) if text else die("ask what?")
    if command == "chat":
        import runpy

        sys.argv = ["cli.py", "--no-gmail"]
        runpy.run_path(str(Path(__file__).parent / "cli.py"), run_name="__main__")
        return 0

    # Bare `ops gym at 6pm` is a capture: the common case should not need a verb.
    return cmd_add(" ".join(argv))


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except VaultPathError as exc:
        sys.exit(die(str(exc)))
    except KeyboardInterrupt:
        sys.exit(130)
