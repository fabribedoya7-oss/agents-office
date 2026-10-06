"""Generic tools: documents, drafts, reports, task creation, approvals, completion."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .. import store
from . import tool


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-") or "item"


@tool("Read a document from the workspace (.txt, .md, .pdf, .docx): its text plus basic facts "
      "(characters, words, lines, first line).",
      {"path": {"type": "string", "description": "path relative to workspace/, e.g. inbox/notes/hello.txt"}})
def read_document(path: str) -> dict:
    from .recruiting import extract_text
    p = store.ws_path(path)
    if not p.is_file():
        raise FileNotFoundError(f"document not found: {path}")
    text = extract_text(p)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return {"path": store.rel(p), "chars": len(text), "words": len(text.split()), "lines": len(lines),
            "first_line": lines[0].strip()[:200] if lines else "", "text": text[:12000]}


@tool("Save a markdown draft (e.g. a job ad) to outputs/drafts/. Returns the file path.",
      {"name": {"type": "string", "description": "short file name, e.g. the job order id"},
       "content": {"type": "string", "description": "markdown content"}},
      produces_artifacts=True)
def save_draft(name: str, content: str) -> dict:
    p = store.OUTPUTS / "drafts" / f"{_slug(name)}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return {"path": store.rel(p), "chars": len(content)}


@tool("Save a structured report (JSON + readable markdown) to outputs/reports/<kind>/.",
      {"kind": {"type": "string"}, "name": {"type": "string"},
       "data": {"type": "object", "description": "report payload"},
       "context": {"type": "object", "description": "optional source record (e.g. the job order)"}},
      required=["kind", "name", "data"], produces_artifacts=True)
def save_report(kind: str, name: str, data: dict, context: dict | None = None) -> dict:
    d = store.OUTPUTS / "reports" / _slug(kind)
    d.mkdir(parents=True, exist_ok=True)
    jp, mp = d / f"{_slug(name)}.json", d / f"{_slug(name)}.md"
    payload = {"generated": store.now(), "data": data, "context": context}
    jp.write_text(json.dumps(payload, indent=2, default=str))
    mp.write_text(_render_md(kind, name, data, context))
    return {"path": store.rel(mp), "json": store.rel(jp)}


def _render_md(kind: str, name: str, data: dict, context: dict | None) -> str:
    lines = [f"# {kind.title()} report: {name}", f"_Generated {store.now()}_", ""]
    if kind == "salary" and context:
        offer = context.get("pay_offered", {})
        lines += [f"**Role:** {context.get('title')} · **Client:** {context.get('client')} · "
                  f"**Offered:** ${offer.get('min')}–${offer.get('max')}/{offer.get('unit', 'hour')}", ""]
        if data.get("verdict"):
            lines += [f"**Verdict:** {data['verdict']}", ""]
    for k, v in data.items():
        if isinstance(v, (dict, list)):
            lines.append(f"- **{k}:**")
            items = v.items() if isinstance(v, dict) else enumerate(v, 1)
            for kk, vv in items:
                lines.append(f"  - {kk}: {vv}")
        else:
            lines.append(f"- **{k}:** {v}")
    return "\n".join(lines) + "\n"


@tool("Create a task for another department's agent (hand-off).",
      {"department": {"type": "string"}, "agent": {"type": "string"},
       "title": {"type": "string"}, "input": {"type": "object"}})
def create_task(department: str, agent: str, title: str, input: dict, ctx: dict) -> dict:
    by = f"{ctx.get('agent', 'agent')}:{ctx.get('task_id', '')}"
    t = store.create_task(department, agent, title, input, created_by=by)
    return {"task_id": t["id"], "department": department, "agent": agent}


@tool("Send the current task's output to a human for approval. Stops the task until a decision.",
      {"summary": {"type": "string", "description": "what the human is approving, in one or two sentences"},
       "artifacts": {"type": "array", "items": {"type": "string"}, "description": "file paths to review"}})
def request_approval(summary: str, artifacts: list[str], ctx: dict) -> dict:
    task = store.get_task(ctx["task_id"])
    appr = store.request_approval(task, summary, artifacts)
    ctx["stop"] = "awaiting_approval"
    return {"status": "awaiting_approval", "approval_file": f"approvals/{task['id']}.json",
            "artifacts": appr["artifacts"]}


@tool("Mark the current task done with a one-line summary of what was produced.",
      {"summary": {"type": "string"}})
def complete_task(summary: str, ctx: dict) -> dict:
    ctx["stop"] = "done"
    ctx["summary"] = summary
    return {"status": "done"}


# ---------- post-approval actions (run by the orchestrator, not by agents) ----------

def publish_job_ad(approval: dict) -> list[str]:
    """Approved ads move to outputs/published/. Posting to a live job board needs that board's API
    credentials; add the call here when you have one."""
    out = []
    for a in approval["artifacts"]:
        src = store.ws_path(a)
        if src.exists() and src.suffix == ".md":
            dst = store.OUTPUTS / "published" / src.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(src.read_text())
            out.append(store.rel(dst))
    return out


def release_invoices(approval: dict) -> list[str]:
    """Approved invoices/payroll move to outputs/released/. Hook an accounting API (QuickBooks,
    Xero) or email sender here when credentials exist."""
    out = []
    for a in approval["artifacts"]:
        src = store.ws_path(a)
        if src.exists():
            dst = store.OUTPUTS / "released" / Path(a).parent.name / src.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src.read_bytes())
            out.append(store.rel(dst))
    return out


POST_APPROVAL = {"publish_job_ad": publish_job_ad, "release_invoices": release_invoices}
