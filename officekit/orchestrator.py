"""The orchestrator loop: intake -> dispatch queued tasks -> apply human decisions.

Departments run independently: each cycle takes the oldest queued task per department, so a slow
Pay & Bill run never starves Candidate Hub.
"""
from __future__ import annotations

import json
import time

from . import store
from .agent import run_task
from .config import agent_spec, departments, intake_rules
from .tools.office_tools import POST_APPROVAL


def intake() -> list[dict]:
    """Turn new inbox files into tasks according to intake.yaml."""
    seen, created, newly = store.seen_files(), [], []
    for rule in intake_rules():
        for p in sorted(store.WS.glob(rule["watch"])):
            if not p.is_file() or p.name.startswith(".") or p.suffix == ".lock":
                continue
            r = store.rel(p)
            if r in seen or r in newly:
                continue
            vars_ = {"path": r, "stem": p.stem, "name": p.name, "parent": p.parent.name}
            for spec in rule["tasks"]:
                fmt = lambda v: v.format(**vars_) if isinstance(v, str) else v
                created.append(store.create_task(
                    spec["department"], spec["agent"], fmt(spec["title"]),
                    {k: fmt(v) for k, v in spec.get("input", {}).items()}, created_by="intake"))
            newly.append(r)
    if newly:
        store.mark_seen(newly)
    return created


def apply_decisions() -> list[str]:
    """Run post-approval actions for approved items; close rejected ones."""
    done = []
    for f in sorted(store.APPROVALS.glob("*.json")):
        a = json.loads(f.read_text())
        if not a.get("decision") or a.get("applied"):
            continue
        task = store.get_task(a["task_id"])
        if a["decision"] == "approved":
            action = agent_spec(task["department"], task["agent"]).get("on_approve")
            outputs = POST_APPROVAL[action](a) if action else []
            store.update_task(task["id"], status="done", artifacts=outputs,
                              note=f"approved by {a.get('by', 'unknown')}" + (f": {a['note']}" if a.get("note") else ""))
            store.log(task["department"], task["agent"],
                      f"approved by {a.get('by', 'unknown')}, {action or 'no action'} -> {len(outputs)} file(s)",
                      task_id=task["id"])
        else:
            store.update_task(task["id"], status="rejected",
                              note=f"rejected by {a.get('by', 'unknown')}" + (f": {a['note']}" if a.get("note") else ""))
            store.log(task["department"], task["agent"], f"rejected by {a.get('by', 'unknown')}: {a.get('note', '')}",
                      task_id=task["id"])
        a["applied"] = store.now()
        f.write_text(json.dumps(a, indent=2))
        done.append(task["id"])
    return done


def cycle(prefer_llm: bool = False) -> dict:
    created = intake()
    applied = apply_decisions()
    ran = []
    for dept in departments():
        queued = sorted((t for t in store.list_tasks(dept) if t["status"] == "queued"), key=lambda t: t["created"])
        if queued:
            ran.append(run_task(queued[0], prefer_llm=prefer_llm))
    return {"created": len(created), "applied": len(applied), "ran": len(ran)}


def run(until_idle: bool = True, watch: bool = False, interval: float = 5.0, prefer_llm: bool = False,
        on_cycle=None) -> None:
    while True:
        stats = cycle(prefer_llm)
        if on_cycle:
            on_cycle(stats)
        idle = not (stats["created"] or stats["applied"] or stats["ran"])
        if idle and not watch:
            return
        if idle or watch:
            time.sleep(interval if idle else 0.2)
