"""Tool registry. Each tool is a plain Python function plus a JSON schema.

The same registry feeds three callers:
  * the Claude tool-use loop (office/agent.py)
  * deterministic pipelines in department YAML (no API key needed)
  * the MCP server (mcp_server.py), so Claude Code / Claude Desktop can drive the office
"""
from __future__ import annotations

import inspect
from typing import Any, Callable

REGISTRY: dict[str, dict] = {}


def tool(description: str, params: dict[str, dict], required: list[str] | None = None,
         produces_artifacts: bool = False):
    """Register a function as a tool. `params` is a JSON-schema properties dict."""
    def wrap(fn: Callable):
        REGISTRY[fn.__name__] = {
            "name": fn.__name__,
            "description": description,
            "input_schema": {"type": "object", "properties": params,
                             "required": required if required is not None else list(params)},
            "fn": fn,
            "wants_ctx": "ctx" in inspect.signature(fn).parameters,
            "produces_artifacts": produces_artifacts,
        }
        return fn
    return wrap


def call(name: str, args: dict, ctx: dict | None = None) -> Any:
    if name not in REGISTRY:
        raise KeyError(f"unknown tool {name}")
    spec = REGISTRY[name]
    kwargs = dict(args)
    if spec["wants_ctx"]:
        kwargs["ctx"] = ctx or {}
    return spec["fn"](**kwargs)


def schemas(names: list[str]) -> list[dict]:
    return [{k: REGISTRY[n][k] for k in ("name", "description", "input_schema")} for n in names]


# import modules so their @tool decorators run
from . import recruiting, compliance, paybill, salary, office_tools  # noqa: E402,F401
