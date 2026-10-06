#!/usr/bin/env python3
"""Agents Office CLI.

  python office.py run [--watch] [--llm]   intake new files, run agents until idle (or forever with --watch)
  python office.py dashboard [--live]      show departments, agents, task queues, approvals
  python office.py city [--port N] [--no-browser]   live isometric office floor in your browser (alias: view)
  python office.py approvals               list items waiting for a human
  python office.py approve T-XXXXXX [--note ...]
  python office.py reject  T-XXXXXX --note "why"
  python office.py requeue T-XXXXXX [--note ...]   withdraw a pending approval and queue the task to be redone
  python office.py task add <department> <agent> "<title>" key=value ...
  python office.py tasks [department]      list tasks as JSON
  python office.py check                   validate department YAML against the tool registry
  python office.py reset                   clear tasks, approvals, logs and outputs (keeps inbox and data)
"""
from __future__ import annotations

import argparse
import getpass
import json
import shutil
import sys
import time

from officekit import config, store
from officekit import dashboard as dash
from officekit import orchestrator


def _whoami() -> str:
    """The OS username of whoever ran the command, recorded on approvals and rejections."""
    try:
        return getpass.getuser()
    except Exception:
        return "unknown user"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="office", description="Headless multi-agent office")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run")
    r.add_argument("--watch", action="store_true", help="keep polling the inbox")
    r.add_argument("--llm", action="store_true", help="let Claude drive pipeline agents too (needs API key)")
    r.add_argument("--interval", type=float, default=5.0)
    r.add_argument("--quiet", action="store_true")

    d = sub.add_parser("dashboard")
    d.add_argument("--live", action="store_true")
    d.add_argument("--interval", type=float, default=2.0)

    c = sub.add_parser("city", aliases=["view"])
    c.add_argument("--port", type=int, default=8765)
    c.add_argument("--no-browser", action="store_true", help="don't open the browser automatically")
    c.add_argument("--interval", type=float, default=2.0, help="seconds between snapshot refreshes")

    sub.add_parser("approvals")
    for name in ("approve", "reject"):
        a = sub.add_parser(name)
        a.add_argument("task_id")
        a.add_argument("--note", default="")

    rq = sub.add_parser("requeue")
    rq.add_argument("task_id")
    rq.add_argument("--note", default="")

    t = sub.add_parser("task")
    t.add_argument("action", choices=["add"])
    t.add_argument("department")
    t.add_argument("agent")
    t.add_argument("title")
    t.add_argument("kv", nargs="*", help="input fields as key=value")

    ls = sub.add_parser("tasks")
    ls.add_argument("department", nargs="?")
    sub.add_parser("check")
    sub.add_parser("reset")

    args = p.parse_args(argv)

    problems = config.validate()
    if problems and args.cmd != "check":
        print("Config problems:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 2

    if args.cmd == "run":
        def report(stats):
            if not args.quiet and any(stats.values()):
                print(f"[{store.now()[11:]}] intake {stats['created']} · decisions applied {stats['applied']}"
                      f" · agents ran {stats['ran']}")
        orchestrator.run(watch=args.watch, interval=args.interval, prefer_llm=args.llm, on_cycle=report)
        if not args.watch:
            dash.show()
    elif args.cmd == "dashboard":
        while True:
            dash.show(clear=args.live)
            if not args.live:
                break
            time.sleep(args.interval)
    elif args.cmd in ("city", "view"):
        from officekit import view
        view.serve(port=args.port, open_browser=not args.no_browser, interval=args.interval)
    elif args.cmd == "approvals":
        items = store.pending_approvals()
        print(json.dumps(items, indent=2) if items else "Nothing waiting for approval.")
    elif args.cmd in ("approve", "reject"):
        store.decide(args.task_id, approved=args.cmd == "approve", by=_whoami(), note=args.note)
        applied = orchestrator.apply_decisions()
        print(f"{args.task_id} {args.cmd}d" + (" and applied." if args.task_id in applied else "."))
    elif args.cmd == "requeue":
        store.requeue_task(args.task_id, note=args.note, by="cli")
        print(f"{args.task_id} requeued.")
    elif args.cmd == "task":
        inp = dict(kv.split("=", 1) for kv in args.kv)
        config.agent_spec(args.department, args.agent)  # fail fast on typos
        task = store.create_task(args.department, args.agent, args.title, inp, created_by="cli")
        print(f"created {task['id']}")
    elif args.cmd == "tasks":
        print(json.dumps(store.list_tasks(args.department), indent=2))
    elif args.cmd == "check":
        print("OK: all agents reference known tools." if not problems else "\n".join(problems))
        return 1 if problems else 0
    elif args.cmd == "reset":
        for d in (store.TASKS, store.APPROVALS, store.LOGS, store.OUTPUTS):
            shutil.rmtree(d, ignore_errors=True)
            d.mkdir(parents=True)
        for f in ("state.json", "dashboard.json", "dashboard.md", "data/candidates.json"):
            (store.WS / f).unlink(missing_ok=True)
        print("Reset done. Inbox, documents and data files kept.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
