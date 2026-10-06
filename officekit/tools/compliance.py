"""Document compliance: required docs per job profile vs. the candidate's document manifest."""
from __future__ import annotations

import datetime as dt

import yaml

from .. import store
from . import tool
from .recruiting import read_job_order

PROFILES = store.WS / "data" / "compliance_profiles.yaml"
EXPIRY_WARNING_DAYS = 30


@tool("Check a candidate's documents against the job's compliance profile. Reports missing, expired "
      "and soon-to-expire documents, and whether the files referenced actually exist.",
      {"candidate_id": {"type": "string"}, "job_order_path": {"type": "string"}})
def check_compliance(candidate_id: str, job_order_path: str, today: str | None = None) -> dict:
    order = read_job_order(job_order_path)
    profile_name = order.get("compliance_profile", "default")
    profiles = yaml.safe_load(PROFILES.read_text())
    required = profiles.get(profile_name) or profiles["default"]

    folder = store.WS / "documents" / candidate_id
    manifest = folder / "manifest.yaml"
    docs = yaml.safe_load(manifest.read_text()).get("documents", []) if manifest.exists() else []
    by_type = {d["type"]: d for d in docs}
    ref = dt.date.fromisoformat(today) if today else dt.date.today()

    missing, expired, expiring, ok = [], [], [], []
    for req in required:
        d = by_type.get(req)
        if not d:
            missing.append(req)
            continue
        if d.get("file") and not (folder / d["file"]).exists():
            missing.append(f"{req} (listed but file {d['file']} not found)")
            continue
        exp = d.get("expires")
        if exp:
            exp_d = exp if isinstance(exp, dt.date) else dt.date.fromisoformat(str(exp))
            if exp_d < ref:
                expired.append(f"{req} (expired {exp_d})")
                continue
            if (exp_d - ref).days <= EXPIRY_WARNING_DAYS:
                expiring.append(f"{req} (expires {exp_d})")
        ok.append(req)

    cleared = not missing and not expired
    return {
        "candidate_id": candidate_id,
        "job_order": order["id"],
        "profile": profile_name,
        "required": required,
        "ok": ok,
        "missing": missing,
        "expired": expired,
        "expiring_soon": expiring,
        "cleared": cleared,
        "candidate_status": "cleared" if cleared else "docs_missing",
        "checked_on": str(ref),
        "manifest_found": manifest.exists(),
    }
