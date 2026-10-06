"""Terminal dashboard + machine-readable snapshot (workspace/dashboard.json and dashboard.md)."""
from __future__ import annotations

import json
import os
import shutil
from collections import Counter

from . import llm, store
from .config import departments

C = {"reset": "\033[0m", "dim": "\033[2m", "bold": "\033[1m", "green": "\033[32m", "yellow": "\033[33m",
     "red": "\033[31m", "cyan": "\033[36m", "magenta": "\033[35m", "grey": "\033[90m", "blue": "\033[34m"}
STATUS_COLOR = {"queued": "grey", "running": "cyan", "awaiting_approval": "yellow", "blocked": "magenta",
                "done": "green", "failed": "red", "rejected": "red"}
WORK_TAG = {"real": ("REAL", "green"), "llm": ("CLAUDE DRAFT", "blue"), "placeholder": ("NO OUTPUT", "grey")}


def snapshot() -> dict:
    depts = []
    for d in departments().values():
        tasks = store.list_tasks(d["id"])
        agents = []
        for a in d["agents"]:
            mine = [t for t in tasks if t["agent"] == a["id"]]
            latest = max(mine, key=lambda t: t["updated"], default=None)
            state = ("working" if any(t["status"] == "running" for t in mine) else
                     "waiting on human" if any(t["status"] == "awaiting_approval" for t in mine) else
                     "blocked" if any(t["status"] == "blocked" for t in mine) else
                     "failed" if latest and latest["status"] == "failed" else
                     "queued" if any(t["status"] == "queued" for t in mine) else "idle")
            agents.append({"id": a["id"], "name": a["name"], "state": state,
                           "mode": "claude" if a.get("needs_llm") else "pipeline",
                           "done": sum(t["status"] == "done" for t in mine)})
        depts.append({"id": d["id"], "name": d["name"], "agents": agents,
                      "counts": dict(Counter(t["status"] for t in tasks)),
                      "tasks": sorted(tasks, key=lambda t: t["updated"], reverse=True)})
    all_tasks = [t for d in depts for t in d["tasks"]]
    return {"company": store.COMPANY, "generated": store.now(), "claude_connected": llm.available(),
            "model": llm.DEFAULT_MODEL,
            "departments": depts, "approvals": store.pending_approvals(), "activity": store.recent_logs(10),
            "handoffs": _handoffs(all_tasks),
            "totals": {"tasks": len(all_tasks),
                       "real_work": sum(t["work_type"] == "real" for t in all_tasks),
                       "claude_drafts": sum(t["work_type"] == "llm" for t in all_tasks),
                       "no_output": sum(t["work_type"] == "placeholder" for t in all_tasks),
                       "artifacts": sum(len(t["artifacts"]) for t in all_tasks)}}


def _handoffs(all_tasks: list[dict], limit: int = 50) -> list[dict]:
    """Tasks one agent created for another department (created_by is "<agent>:<source task id>")."""
    dept_of = {t["id"]: t["department"] for t in all_tasks}
    out = []
    for t in all_tasks:
        agent, _, src = t.get("created_by", "").partition(":")
        if src in dept_of and dept_of[src] != t["department"]:
            out.append({"task_id": t["id"], "from_department": dept_of[src], "from_agent": agent, "from_task": src,
                        "to_department": t["department"], "to_agent": t["agent"], "at": t["created"]})
    return sorted(out, key=lambda h: h["at"])[-limit:]


def _c(text: str, color: str, on: bool) -> str:
    return f"{C[color]}{text}{C['reset']}" if on else text


def render(snap: dict, color: bool = True, max_tasks: int = 6) -> str:
    w = min(shutil.get_terminal_size((100, 40)).columns, 110)
    out = []
    tot = snap["totals"]
    mode = (_c(f"Claude connected ({snap['model']})", "green", color) if snap["claude_connected"]
            else _c("Claude not connected: pipeline agents run, Claude-only agents are blocked", "yellow", color))
    out.append(_c("AGENTS OFFICE", "bold", color) + _c(f"  ·  {snap.get('company')}  ·  {snap['generated']}", "dim", color))
    out.append(mode)
    out.append(f"{tot['tasks']} tasks · " + _c(f"{tot['real_work']} real work", "green", color) + " · "
               + _c(f"{tot['claude_drafts']} Claude drafts", "blue", color) + " · "
               + _c(f"{tot['no_output']} no output yet", "grey", color) + f" · {tot['artifacts']} files produced")
    out.append("─" * w)

    for d in snap["departments"]:
        counts = "  ".join(_c(f"{k} {v}", STATUS_COLOR[k], color) for k, v in sorted(d["counts"].items()))
        out.append(_c(d["name"].upper(), "bold", color) + "   " + (counts or _c("no tasks", "dim", color)))
        for a in d["agents"]:
            sc = {"working": "cyan", "waiting on human": "yellow", "blocked": "magenta", "failed": "red",
                  "queued": "grey", "idle": "dim"}[a["state"]]
            out.append(f"  ● {a['name']:<22}" + _c(f"{a['state']:<17}", sc, color)
                       + _c(f"{a['mode']:<9}", "dim", color) + f"{a['done']} done")
        for t in d["tasks"][:max_tasks]:
            tag, tc = WORK_TAG[t["work_type"]]
            st = _c(f"{t['status']:<18}", STATUS_COLOR[t["status"]], color)
            line = f"    {t['id']}  {st}" + _c(f"{tag:<13}", tc, color) + t["title"]
            out.append(line[: w + 40])
            if t.get("note") and t["status"] in ("blocked", "failed", "awaiting_approval", "rejected"):
                out.append(_c(f"             ↳ {t['note'][:w - 16]}", "dim", color))
        out.append("")

    out.append("─" * w)
    out.append(_c(f"NEEDS YOUR APPROVAL ({len(snap['approvals'])})", "yellow", color))
    for a in snap["approvals"]:
        out.append(f"  {a['task_id']}  {a['title']}")
        out.append(_c(f"           {a['summary'][:w - 12]}", "dim", color))
        for f in a["artifacts"][:4]:
            out.append(_c(f"           · workspace/{f}", "dim", color))
        out.append(_c(f"           python office.py approve {a['task_id']}   |   python office.py reject {a['task_id']} --note \"...\"",
                      "cyan", color))
    if not snap["approvals"]:
        out.append(_c("  nothing waiting", "dim", color))

    out.append(_c("RECENT ACTIVITY", "bold", color))
    for l in snap["activity"]:
        lc = {"error": "red", "warn": "yellow"}.get(l["level"], "dim")
        out.append(_c(f"  {l['at'][11:]}  {l['agent']:<18} {l['msg'][:w - 32]}", lc, color))
    return "\n".join(out)


def _write_atomic(name: str, text: str) -> None:
    # readers (the city page polls dashboard.json) must never see a half-written file
    p = store.WS / name
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(p)


def write_files(snap: dict) -> None:
    _write_atomic("dashboard.json", json.dumps(snap, indent=2, default=str))
    _write_atomic("dashboard.md", "```\n" + render(snap, color=False, max_tasks=20) + "\n```\n")


def show(clear: bool = False) -> None:
    snap = snapshot()
    write_files(snap)
    if clear:
        os.system("clear" if os.name != "nt" else "cls")
    print(render(snap, color=os.environ.get("NO_COLOR") is None))
