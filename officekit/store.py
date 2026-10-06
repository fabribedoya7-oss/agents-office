"""File-based state: tasks, logs, approvals. Everything is plain JSON/JSONL in workspace/."""
from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(os.environ.get("OFFICE_ROOT", Path(__file__).resolve().parent.parent))
WS = ROOT / "workspace"
TASKS = WS / "tasks"
APPROVALS = WS / "approvals"
LOGS = WS / "logs"
OUTPUTS = WS / "outputs"
STATE = WS / "state.json"

STATUSES = ["queued", "running", "awaiting_approval", "blocked", "done", "failed", "rejected"]
# work_type tells the dashboard what actually happened:
#   real        - a tool ran and produced artifacts on disk (CSV, PDF, report)
#   llm         - Claude produced text (a draft) that a human must review
#   placeholder - nothing ran yet / no artifacts
WORK_TYPES = ["real", "llm", "placeholder"]


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def rel(p: str | Path) -> str:
    p = Path(p)
    try:
        return str(p.resolve().relative_to(WS.resolve()))
    except ValueError:
        return str(p)


def ws_path(p: str | Path) -> Path:
    p = Path(p)
    return p if p.is_absolute() else WS / p


@contextmanager
def _locked(path: Path):
    """Tiny lock-file so the watcher, CLI and MCP server don't clobber each other."""
    lock = path.with_suffix(path.suffix + ".lock")
    for _ in range(200):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except FileExistsError:
            if time.time() - lock.stat().st_mtime > 30:
                lock.unlink(missing_ok=True)
            time.sleep(0.05)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def _read(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text() or "null") or default


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str))
    tmp.replace(path)


# ---------------- tasks ----------------

def dept_file(dept: str) -> Path:
    return TASKS / f"{dept}.json"


def list_tasks(dept: str | None = None) -> list[dict]:
    files = [dept_file(dept)] if dept else sorted(TASKS.glob("*.json"))
    out = []
    for f in files:
        out.extend(_read(f, []))
    return out


def get_task(task_id: str) -> dict | None:
    return next((t for t in list_tasks() if t["id"] == task_id), None)


def create_task(department: str, agent: str, title: str, input: dict, created_by: str = "system") -> dict:
    task = {
        "id": "T-" + uuid.uuid4().hex[:6].upper(),
        "department": department,
        "agent": agent,
        "title": title,
        "input": input,
        "status": "queued",
        "work_type": "placeholder",
        "artifacts": [],
        "note": "",
        "created_by": created_by,
        "created": now(),
        "updated": now(),
        "history": [{"at": now(), "status": "queued", "by": created_by}],
    }
    f = dept_file(department)
    with _locked(f):
        tasks = _read(f, [])
        tasks.append(task)
        _write(f, tasks)
    log(department, agent, f"task created: {task['id']} {title}", task_id=task["id"])
    return task


def update_task(task_id: str, **fields) -> dict:
    task = get_task(task_id)
    if not task:
        raise KeyError(f"no task {task_id}")
    f = dept_file(task["department"])
    with _locked(f):
        tasks = _read(f, [])
        for t in tasks:
            if t["id"] == task_id:
                if "artifacts" in fields:
                    fields["artifacts"] = sorted(set(t["artifacts"]) | set(fields["artifacts"]))
                if "status" in fields and fields["status"] != t["status"]:
                    t["history"].append({"at": now(), "status": fields["status"], "note": fields.get("note", "")})
                t.update(fields)
                t["updated"] = now()
                task = t
        _write(f, tasks)
    return task


# ---------------- logs ----------------

def log(dept: str, agent: str, msg: str, task_id: str | None = None, level: str = "info") -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    line = {"at": now(), "dept": dept, "agent": agent, "task": task_id, "level": level, "msg": msg}
    with open(LOGS / "activity.jsonl", "a") as fh:
        fh.write(json.dumps(line) + "\n")


def recent_logs(n: int = 12) -> list[dict]:
    f = LOGS / "activity.jsonl"
    if not f.exists():
        return []
    lines = f.read_text().splitlines()[-n:]
    return [json.loads(x) for x in lines]


# ---------------- approvals ----------------

def request_approval(task: dict, summary: str, artifacts: list[str]) -> dict:
    appr = {
        "task_id": task["id"],
        "department": task["department"],
        "agent": task["agent"],
        "title": task["title"],
        "summary": summary,
        "artifacts": [rel(a) for a in artifacts],
        "requested": now(),
        "decision": None,
    }
    _write(APPROVALS / f"{task['id']}.json", appr)
    update_task(task["id"], status="awaiting_approval", artifacts=appr["artifacts"], note=summary[:200])
    log(task["department"], task["agent"], f"approval requested: {summary[:120]}", task_id=task["id"])
    return appr


def pending_approvals() -> list[dict]:
    return [a for a in (_read(p, {}) for p in sorted(APPROVALS.glob("*.json"))) if a and a.get("decision") is None]


def decide(task_id: str, approved: bool, note: str = "", by: str = "human") -> dict:
    p = APPROVALS / f"{task_id}.json"
    appr = _read(p, None)
    if not appr:
        raise KeyError(f"no pending approval for {task_id}")
    if appr.get("decision"):
        raise ValueError(f"{task_id} already {appr['decision']}")
    appr.update(decision="approved" if approved else "rejected", decided=now(), by=by, note=note)
    _write(p, appr)
    return appr


# ---------------- watcher state ----------------

def seen_files() -> set[str]:
    return set(_read(STATE, {}).get("seen", []))


def mark_seen(paths: list[str]) -> None:
    with _locked(STATE):
        st = _read(STATE, {})
        st["seen"] = sorted(set(st.get("seen", [])) | set(paths))
        _write(STATE, st)
