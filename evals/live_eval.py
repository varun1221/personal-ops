"""Live eval harness: scripted scenarios with real assertions, against a temp vault.

Usage:  python harness.py [read|write|memory|all]
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent; sys.path.insert(0, str(PROJECT)); os.chdir(PROJECT)
SP = Path(__file__).parent
from dotenv import load_dotenv; load_dotenv(PROJECT / ".env")

STAGE = (sys.argv[1] if len(sys.argv) > 1 else "all").lower()

TMP = Path(tempfile.mkdtemp(prefix="eval-")); VAULT = TMP / "vault"; SNAP = TMP / "snap"
SOURCE_VAULT = Path(os.environ.get("OBSIDIAN_VAULT_PATH") or Path.home() / "vault").expanduser()
shutil.copytree(SOURCE_VAULT, VAULT)
for p in (VAULT / "Schedules").glob("*.md"): p.unlink()
shutil.copytree(SP / "seed", VAULT, dirs_exist_ok=True)
os.environ["OBSIDIAN_VAULT_PATH"] = str(VAULT)
os.environ.pop("OBSIDIAN_DAILY_FOLDER", None)

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agent import models
from agent.graph import build_graph
from agent.mcp_client import build_client, load_tools

RESULTS = []
def note(p): return (VAULT / p).read_text() if (VAULT / p).exists() else ""
def planner_lines(p): return [l for l in note(p).splitlines() if l.strip().startswith("- [")]

def snapshot():
    if SNAP.exists(): shutil.rmtree(SNAP)
    shutil.copytree(VAULT, SNAP)

def restore():
    if SNAP.exists():
        shutil.rmtree(VAULT); shutil.copytree(SNAP, VAULT)

def is_rate_limit(e) -> bool:
    m = str(e).lower()
    return "429" in m or "rate limit" in m or "rate_limited" in m

async def ask(graph, cfg, text, decisions):
    prompts, tools_used = [], []
    payload = {"messages": [HumanMessage(content=text)]}
    while True:
        result = await graph.ainvoke(payload, cfg)
        pending = result.get("__interrupt__")
        if not pending: break
        req = pending[0].value
        prompts.append(req["description"])
        # Keep answering the way a real user would, instead of silently rejecting
        # a retry — otherwise a wasted prompt looks like a tool failure.
        d = decisions.pop(0) if len(decisions) > 1 else (decisions[0] if decisions
             else {"approved": False, "reason": "unscripted"})
        payload = Command(resume=d)
    for m in result["messages"]:
        for tc in (getattr(m, "tool_calls", None) or []): tools_used.append(tc["name"])
    reply = ""
    for m in reversed(result["messages"]):
        if isinstance(m, AIMessage) and not m.tool_calls:
            reply = (m.text or m.content) or ""; break
    return reply, prompts, tools_used

def record(name, ok, detail):
    RESULTS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}\n        {detail}", flush=True)

async def scenario(graph, name, text, decisions, check, thread=None, attempts=4):
    print(f"\n> {text}", flush=True)
    for attempt in range(1, attempts + 1):
        snapshot()                     # a retry must not double-apply a write
        cfg = {"configurable": {"thread_id": thread or str(uuid.uuid4())}}
        try:
            reply, prompts, tools = await ask(graph, cfg, text, list(decisions))
        except Exception as e:
            if is_rate_limit(e) and attempt < attempts:
                wait = 45 * attempt
                print(f"  [429 — restoring vault, waiting {wait}s ({attempt}/{attempts})]", flush=True)
                restore(); await asyncio.sleep(wait); continue
            record(name, False, f"EXCEPTION {type(e).__name__}: {str(e)[:140]}")
            return None
        print(f"  [prompts] {prompts}\n  [tools]   {tools}\n  [reply]   {reply.strip()[:260]}", flush=True)
        ok, detail = check(reply, prompts, tools)
        record(name, ok, detail)
        await asyncio.sleep(PACE)
        return cfg
    record(name, False, "rate limited after all retries")
    return None

PACE = float(os.environ.get("PACE", "6"))

async def main():
    print(f"model : {models.describe()}\nvault : {VAULT}\nstage : {STAGE}\npace  : {PACE}s\n", flush=True)
    client = build_client(include_gmail=False, include_actions=True)
    tools = await load_tools(client)
    print(f"tools : {len(tools)}", flush=True)
    graph = build_graph(tools, models.get_model(), MemorySaver())

    if STAGE in ("read", "all"):
        await scenario(graph, "R1 list events today", "What's on my calendar today?", [],
            lambda r,p,t: ("list_events" in t and "Standup" in r,
                           f"used {sorted(set(t))}"))
        await scenario(graph, "R2 find free slots", "When am I free today for a 90 minute block?", [],
            lambda r,p,t: ("find_free_slots" in t, f"used {sorted(set(t))}"))
        await scenario(graph, "R3 week overview", "How does my week look?", [],
            lambda r,p,t: ("week_overview" in t, f"used {sorted(set(t))}"))
        await scenario(graph, "R4 open tasks", "What are my open tasks?", [],
            lambda r,p,t: ("list_tasks" in t, f"used {sorted(set(t))}"))
        await scenario(graph, "R5 unscheduled commitments",
            "What did I commit to this week that isn't on my calendar?", [],
            lambda r,p,t: ("search_notes" in t and "Sarah" in r,
                           f"searched:{'search_notes' in t} foundSarah:{'Sarah' in r}"))

    if STAGE in ("write", "all"):
        await scenario(graph, "W1 create event (approve)",
            "Add a gym session tomorrow at 7am for an hour.", [{"approved": True}],
            lambda r,p,t: ("07:00 - 08:00" in note("Schedules/2026-08-19.md"),
                           f"prompts:{len(p)} 19th: {planner_lines('Schedules/2026-08-19.md')}"))
        await scenario(graph, "W2 create event (reject)",
            "Add a dentist appointment today at 4pm.", [{"approved": False, "reason": "wrong day"}],
            lambda r,p,t: (len(p) >= 1 and "entist" not in note("Schedules/2026-08-18.md"),
                           f"prompts:{len(p)} vaultClean:{'entist' not in note('Schedules/2026-08-18.md')}"))
        await scenario(graph, "W3 complete task", "Mark the standup today as done.", [{"approved": True}],
            lambda r,p,t: ("- [x] 09:00 - 09:15 Standup" in note("Schedules/2026-08-18.md"),
                           f"prompts:{len(p)} lines:{planner_lines('Schedules/2026-08-18.md')}"))
        await scenario(graph, "W4 rename entry",
            "Rename 'Deep work on checkout spec' to 'Checkout spec drafting'.", [{"approved": True}],
            lambda r,p,t: ("- [ ] 10:00 - 12:00 Checkout spec drafting"
                           in planner_lines("Schedules/2026-08-18.md"),
                           f"prompts:{len(p)} lines:{planner_lines('Schedules/2026-08-18.md')}"))
        await scenario(graph, "W5 move event", "Move my 1-1 with Priya today to 4pm.", [{"approved": True}],
            lambda r,p,t: ("16:00" in note("Schedules/2026-08-18.md")
                           and note("Schedules/2026-08-18.md").count("Priya") == 1,
                           f"priyaCount:{note('Schedules/2026-08-18.md').count('Priya')} lines:{planner_lines('Schedules/2026-08-18.md')}"))
        await scenario(graph, "W6 add task", "Add a task to buy milk due Friday.", [{"approved": True}],
            lambda r,p,t: ("uy milk" in note("Inbox.md"), f"inbox tail:{note('Inbox.md').strip().splitlines()[-1:]}"))
        await scenario(graph, "W7 delete (reject keeps data)",
            "Delete the design review on Thursday.", [{"approved": False, "reason": "keep it"}],
            lambda r,p,t: ("Design review" in note("Schedules/2026-08-20.md"),
                           f"20th: {planner_lines('Schedules/2026-08-20.md')}"))
        await scenario(graph, "W8 delete (approve removes)",
            "Delete the design review on Thursday.", [{"approved": True}],
            lambda r,p,t: ("Design review" not in note("Schedules/2026-08-20.md")
                           and "Standup" in note("Schedules/2026-08-20.md"),
                           f"20th: {planner_lines('Schedules/2026-08-20.md')}"))

    if STAGE in ("memory", "all"):
        cfg = {"configurable": {"thread_id": "mem"}}
        try:
            print("\n> [mem] What's on my calendar Thursday?", flush=True)
            r1, _, _ = await ask(graph, cfg, "What's on my calendar Thursday?", [])
            print(f"  [reply] {r1.strip()[:200]}", flush=True); await asyncio.sleep(PACE)
            print("\n> [mem] What time does the first one start?", flush=True)
            r2, _, _ = await ask(graph, cfg, "What time does the first one start?", [])
            print(f"  [reply] {r2.strip()[:200]}", flush=True)
            record("M1 coreference", "09:00" in r2 or "9:00" in r2, f"resolved to 09:00: {'09:00' in r2 or '9:00' in r2}")
        except Exception as e:
            record("M1 coreference", False, f"EXCEPTION {type(e).__name__}: {str(e)[:120]}")

    print("\n" + "=" * 72, flush=True)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"RESULT: {passed}/{len(RESULTS)} passed")
    for n, ok, d in RESULTS:
        if not ok: print(f"  FAILED: {n} — {d}")
    print("\n=== 2026-08-18 ===\n" + note("Schedules/2026-08-18.md"))

asyncio.run(main())
