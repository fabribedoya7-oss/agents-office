"""Loads the selected company's departments/*.yaml and intake.yaml. Adding a department = adding a YAML file."""
from __future__ import annotations

import yaml

from . import store


def _company_dir():
    if store.COMPANY_DIR is None:
        store.use_company()   # raises a clear error if no company is selected
    return store.COMPANY_DIR


def departments() -> dict[str, dict]:
    out = {}
    for f in sorted((_company_dir() / "departments").glob("*.yaml")):
        d = yaml.safe_load(f.read_text())
        out[d["id"]] = d
    return out


def agent_spec(dept: str, agent: str) -> dict:
    d = departments().get(dept)
    if not d:
        raise KeyError(f"unknown department {dept}")
    for a in d["agents"]:
        if a["id"] == agent:
            return a
    raise KeyError(f"unknown agent {agent} in {dept}")


def intake_rules() -> list[dict]:
    f = _company_dir() / "intake.yaml"
    return (yaml.safe_load(f.read_text()) or {}).get("rules", []) if f.exists() else []


def validate() -> list[str]:
    """Check every agent's tools exist, so a typo in YAML fails loudly at startup."""
    from . import tools
    problems = []
    for d in departments().values():
        for a in d["agents"]:
            for t in a.get("tools", []):
                if t not in tools.REGISTRY:
                    problems.append(f"{d['id']}/{a['id']}: unknown tool {t}")
            for s in a.get("pipeline", []):
                if s["tool"] not in tools.REGISTRY:
                    problems.append(f"{d['id']}/{a['id']}: pipeline uses unknown tool {s['tool']}")
            if a.get("on_approve"):
                from .tools.office_tools import POST_APPROVAL
                if a["on_approve"] not in POST_APPROVAL:
                    problems.append(f"{d['id']}/{a['id']}: unknown on_approve action {a['on_approve']}")
    return problems
