"""Runs one task with its agent: either a deterministic pipeline or a Claude tool-use loop."""
from __future__ import annotations

import json
import re

from . import llm, store, tools
from .config import agent_spec

MAX_TURNS = 14
REF = re.compile(r"\{([a-zA-Z_][\w.]*)\}")


# ---------- template resolution for YAML pipelines ----------

def _lookup(path: str, ctx: dict):
    cur = ctx
    for part in path.split("."):
        cur = cur[part] if isinstance(cur, dict) else getattr(cur, part)
    return cur


def resolve(value, ctx: dict):
    if isinstance(value, str):
        m = REF.fullmatch(value)
        if m:
            return _lookup(m.group(1), ctx)          # keep objects intact
        return REF.sub(lambda mm: str(_lookup(mm.group(1), ctx)), value)
    if isinstance(value, dict):
        return {k: resolve(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, ctx) for v in value]
    return value


def _when(expr: str | None, ctx: dict) -> bool:
    if not expr:
        return True
    m = re.fullmatch(r"\s*([\w.]+)\s*(==|!=)\s*'([^']*)'\s*", expr)
    if not m:
        raise ValueError(f"unsupported condition: {expr}")
    left = str(_lookup(m.group(1), ctx))
    return (left == m.group(3)) if m.group(2) == "==" else (left != m.group(3))


def _collect_artifacts(result) -> list[str]:
    if not isinstance(result, dict):
        return []
    out = [result[k] for k in ("path", "json") if isinstance(result.get(k), str)]
    if isinstance(result.get("artifacts"), list):
        out += [a for a in result["artifacts"] if isinstance(a, str)]
    return out


# ---------- runners ----------

def run_task(task: dict, prefer_llm: bool = False) -> dict:
    spec = agent_spec(task["department"], task["agent"])
    use_llm = spec.get("needs_llm") or (prefer_llm and llm.available()) or not spec.get("pipeline")
    if use_llm and not llm.available():
        note = "needs Claude: set ANTHROPIC_API_KEY to run this agent (nothing was generated)"
        store.log(task["department"], task["agent"], note, task_id=task["id"], level="warn")
        return store.update_task(task["id"], status="blocked", note=note)

    store.update_task(task["id"], status="running", note="")
    store.log(task["department"], task["agent"], f"started ({'claude' if use_llm else 'pipeline'})", task_id=task["id"])
    ctx = {"task_id": task["id"], "agent": task["agent"], "department": task["department"]}
    try:
        artifacts, kinds = (_run_llm if use_llm else _run_pipeline)(task, spec, ctx)
    except Exception as e:  # tool or API failure: record it, don't hide it
        msg = f"{type(e).__name__}: {e}"
        store.log(task["department"], task["agent"], f"failed: {msg}", task_id=task["id"], level="error")
        return store.update_task(task["id"], status="failed", note=msg[:300])

    work_type = "real" if kinds - {"save_draft"} else ("llm" if "save_draft" in kinds else "placeholder")
    stop = ctx.get("stop")
    if stop == "awaiting_approval":
        t = store.update_task(task["id"], work_type=work_type, artifacts=artifacts)
    elif spec.get("approval") == "required":
        t = store.update_task(task["id"], status="blocked", work_type=work_type, artifacts=artifacts,
                              note="agent finished without requesting the required approval")
    else:
        t = store.update_task(task["id"], status="done", work_type=work_type, artifacts=artifacts,
                              note=ctx.get("summary", f"{len(artifacts)} artifact(s) written"))
    store.log(task["department"], task["agent"], f"{t['status']} [{work_type}] {t['note'][:100]}", task_id=task["id"])
    return t


def _run_pipeline(task: dict, spec: dict, ctx: dict):
    scope = {"input": task["input"]}
    artifacts, kinds = [], set()
    for step in spec["pipeline"]:
        if not _when(step.get("when"), scope):
            continue
        args = resolve(step.get("args", {}), scope)
        result = tools.call(step["tool"], args, ctx)
        store.log(task["department"], task["agent"], f"tool {step['tool']} ok", task_id=task["id"])
        if step.get("save_as"):
            scope[step["save_as"]] = result
        new = _collect_artifacts(result)
        if new and tools.REGISTRY[step["tool"]]["produces_artifacts"]:
            artifacts += new
            kinds.add(step["tool"])
        if ctx.get("stop"):
            break
    return artifacts, kinds


def _run_llm(task: dict, spec: dict, ctx: dict):
    tool_defs = tools.schemas(spec["tools"])
    messages = [{"role": "user", "content":
                 f"Task {task['id']}: {task['title']}\nInput (paths are relative to workspace/):\n"
                 f"{json.dumps(task['input'], indent=2)}\nDo the work with your tools."}]
    artifacts, kinds = [], set()
    for _ in range(MAX_TURNS):
        resp = llm.create(spec["system"], messages, tool_defs)
        messages.append({"role": "assistant", "content": resp["content"]})
        uses = [b for b in resp["content"] if b["type"] == "tool_use"]
        if not uses:
            text = " ".join(b.get("text", "") for b in resp["content"] if b["type"] == "text").strip()
            ctx.setdefault("summary", text[:200])
            break
        results = []
        for u in uses:
            if u["name"] not in spec["tools"]:
                out, err = {"error": f"tool {u['name']} not allowed for this agent"}, True
            else:
                try:
                    out, err = tools.call(u["name"], u["input"], ctx), False
                    store.log(task["department"], task["agent"], f"claude called {u['name']}", task_id=task["id"])
                    new = _collect_artifacts(out)
                    if new and tools.REGISTRY[u["name"]]["produces_artifacts"]:
                        artifacts += new
                        kinds.add(u["name"])
                except Exception as e:
                    out, err = {"error": f"{type(e).__name__}: {e}"}, True
            results.append({"type": "tool_result", "tool_use_id": u["id"],
                            "content": json.dumps(out, default=str)[:20000], "is_error": err})
        messages.append({"role": "user", "content": results})
        if ctx.get("stop"):
            break
    return artifacts, kinds
