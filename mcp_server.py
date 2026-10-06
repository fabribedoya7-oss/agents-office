#!/usr/bin/env python3
"""MCP server: lets Claude Code or Claude Desktop act as the orchestrator.

Register it with Claude Code:
    claude mcp add agents-office -- python /path/to/agents-office/mcp_server.py

Claude then sees office_status, office_run_cycle, office_add_task, office_call_tool, office_submit_work,
office_approvals, office_decide and office_list_tools, and can run the whole office from a conversation.
Works with the `mcp` Python SDK v1 (FastMCP) or v2 (MCPServer).
"""
from __future__ import annotations

import json
from pathlib import Path

try:  # mcp v2
    from mcp.server.mcpserver import MCPServer as Server
except ImportError:  # mcp v1
    from mcp.server.fastmcp import FastMCP as Server

from officekit import config, store, tools
from officekit import dashboard as dash
from officekit import orchestrator

mcp = Server("agents-office")


@mcp.tool()
def office_status() -> str:
    """Live dashboard: departments, agent states, task queues (real work vs drafts), pending approvals."""
    snap = dash.snapshot()
    dash.write_files(snap)
    return dash.render(snap, color=False, max_tasks=10)


@mcp.tool()
def office_run_cycle(cycles: int = 1, use_claude_for_pipelines: bool = False) -> str:
    """Run the orchestrator: pick up new inbox files, apply human decisions, run one queued task per department."""
    results = [orchestrator.cycle(prefer_llm=use_claude_for_pipelines) for _ in range(max(1, min(cycles, 20)))]
    return json.dumps(results)


@mcp.tool()
def office_add_task(department: str, agent: str, title: str, input_json: str = "{}") -> str:
    """Queue a task for an agent. input_json is a JSON object, e.g. {"job_order": "inbox/job_orders/JO-1042.yaml"}."""
    config.agent_spec(department, agent)
    t = store.create_task(department, agent, title, json.loads(input_json), created_by="mcp")
    return t["id"]


@mcp.tool()
def office_list_tools() -> str:
    """List every office tool with its description and input schema."""
    return json.dumps(tools.schemas(list(tools.REGISTRY)), indent=2)


@mcp.tool()
def office_call_tool(name: str, args_json: str = "{}") -> str:
    """Call one office tool directly (e.g. screen_resume, check_compliance, process_timesheet)."""
    if name in ("request_approval", "complete_task", "create_task"):
        return "That tool only works inside a task run. Use office_add_task + office_run_cycle."
    return json.dumps(tools.call(name, json.loads(args_json)), indent=2, default=str)


@mcp.tool()
def office_submit_work(task_id: str, content: str, summary: str) -> str:
    """Complete a Claude-only task (e.g. the Job Ad Writer) with text written in this session instead of
    the API: saves content as a markdown draft, marks the task a Claude draft and sends it for approval.
    Read the task's input first (office_call_tool read_job_order / compare_offer) and follow the agent's
    system prompt in departments/<dept>.yaml."""
    task = store.get_task(task_id)
    if not task:
        return f"no task {task_id}"
    spec = config.agent_spec(task["department"], task["agent"])
    if not (spec.get("needs_llm") or not spec.get("pipeline")):
        return f"{task_id} is a pipeline task; run it with office_run_cycle instead"
    if task["status"] in ("running", "awaiting_approval", "done"):
        return f"{task_id} is {task['status']}; nothing to submit"
    if not content.strip():
        return "content is empty"

    job_order = task["input"].get("job_order")
    name = Path(job_order).stem if job_order else task_id
    draft = tools.call("save_draft", {"name": name, "content": content})
    store.update_task(task_id, status="running", work_type="llm", artifacts=[draft["path"]], note="")
    store.log(task["department"], task["agent"], "draft submitted from Claude session via mcp", task_id=task_id)
    appr = store.request_approval(store.get_task(task_id), summary, [draft["path"]])
    return json.dumps({"task_id": task_id, "status": "awaiting_approval", "draft": draft["path"],
                       "approval_file": f"approvals/{task_id}.json", "artifacts": appr["artifacts"]}, indent=2)


@mcp.tool()
def office_approvals() -> str:
    """Items waiting for a human decision, with the files to review."""
    return json.dumps(store.pending_approvals(), indent=2)


@mcp.tool()
def office_decide(task_id: str, approve: bool, note: str = "") -> str:
    """Record a human's approve/reject decision. Only call this when the human has explicitly decided."""
    store.decide(task_id, approved=approve, note=note, by="human via mcp")
    orchestrator.apply_decisions()
    return f"{task_id} {'approved' if approve else 'rejected'}"


if __name__ == "__main__":
    mcp.run()
