#!/usr/bin/env python3
"""Agents Office CLI.

Every command works on one company (companies/<name>/). Pick it with --company <name>, before or after the
command; without it the default from office.yaml is used.

  python office.py companies               list companies and show the default
  python office.py new-company <name>      create companies/<name>/ from the starter template
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

  e.g. python office.py --company my-shop run      python office.py dashboard --company recruitment-demo
"""
from __future__ import annotations

import argparse
import getpass
import json
import shutil
import sys
import time
from pathlib import Path

from officekit import config, store
from officekit import dashboard as dash
from officekit import orchestrator


def _whoami() -> str:
    """The OS username of whoever ran the command, recorded on approvals and rejections."""
    try:
        return getpass.getuser()
    except Exception:
        return "unknown user"


TEMPLATE = Path(__file__).resolve().parent / "officekit" / "templates" / "company"


def new_company(name: str) -> Path:
    """Copy the starter template to companies/<name>/, filling in the company name."""
    if not store.COMPANY_NAME.match(name):
        raise ValueError(f"invalid company name {name!r}: use lowercase letters, digits and dashes (e.g. my-shop)")
    dest = store.COMPANIES / name
    if dest.exists():
        raise ValueError(f"{dest} already exists")
    shutil.copytree(TEMPLATE, dest)
    title = name.replace("-", " ").title()
    for f in dest.rglob("*"):
        if f.is_file() and f.suffix in (".md", ".yaml", ".txt"):
            f.write_text(f.read_text().replace("{{company}}", name).replace("{{company_title}}", title))
    return dest


def main(argv=None) -> int:
    # --company is accepted before the command (office.py --company x run) and after it (office.py run --company x)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--company", default=argparse.SUPPRESS, help="company under companies/ (default: office.yaml)")
    p = argparse.ArgumentParser(prog="office", description="Headless multi-agent office", parents=[common])
    sub = p.add_subparsers(dest="cmd", required=True)

    def command(*names, **kw):
        return sub.add_parser(names[0], aliases=list(names[1:]), parents=[common], **kw)

    command("companies")
    nc = command("new-company")
    nc.add_argument("name", help="lowercase letters, digits and dashes, e.g. my-shop")

    r = command("run")
    r.add_argument("--watch", action="store_true", help="keep polling the inbox")
    r.add_argument("--llm", action="store_true", help="let Claude drive pipeline agents too (needs API key)")
    r.add_argument("--interval", type=float, default=5.0)
    r.add_argument("--quiet", action="store_true")

    d = command("dashboard")
    d.add_argument("--live", action="store_true")
    d.add_argument("--interval", type=float, default=2.0)

    c = command("city", "view")
    c.add_argument("--port", type=int, default=8765)
    c.add_argument("--no-browser", action="store_true", help="don't open the browser automatically")
    c.add_argument("--interval", type=float, default=2.0, help="seconds between snapshot refreshes")

    command("approvals")
    for name in ("approve", "reject"):
        a = command(name)
        a.add_argument("task_id")
        a.add_argument("--note", default="")

    rq = command("requeue")
    rq.add_argument("task_id")
    rq.add_argument("--note", default="")

    t = command("task")
    t.add_argument("action", choices=["add"])
    t.add_argument("department")
    t.add_argument("agent")
    t.add_argument("title")
    t.add_argument("kv", nargs="*", help="input fields as key=value")

    ls = command("tasks")
    ls.add_argument("department", nargs="?")
    command("check")
    command("reset")

    args = p.parse_args(argv)

    if args.cmd == "companies":
        default = store.default_company()
        for name in store.companies():
            print(("* " if name == default else "  ") + name)
        if not store.companies():
            print("No companies yet. Create one with: python office.py new-company <name>")
        return 0
    if args.cmd == "new-company":
        try:
            dest = new_company(args.name)
        except ValueError as e:
            print(e, file=sys.stderr)
            return 2
        print(f"Created {dest.relative_to(store.ROOT)}/ from the starter template.\n"
              f"Next: read {dest.relative_to(store.ROOT)}/README.md, then run\n"
              f"  python office.py --company {args.name} check\n"
              f"  python office.py --company {args.name} run")
        return 0

    try:
        store.use_company(getattr(args, "company", None))
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2

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
        print(f"OK ({store.COMPANY}): all agents reference known tools." if not problems else "\n".join(problems))
        return 1 if problems else 0
    elif args.cmd == "reset":
        for d in (store.TASKS, store.APPROVALS, store.LOGS, store.OUTPUTS):
            shutil.rmtree(d, ignore_errors=True)
            d.mkdir(parents=True)
        for f in ("state.json", "dashboard.json", "dashboard.md", "data/candidates.json"):
            (store.WS / f).unlink(missing_ok=True)
        print(f"Reset done for {store.COMPANY}. Inbox, documents and data files kept.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
