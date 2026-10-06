"""Job orders, resume parsing and screening, candidate tracker."""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from .. import store
from . import tool

CANDIDATES = store.WS / "data" / "candidates.json"


@tool("Read a job order YAML (client, title, pay, must-have and nice-to-have requirements).",
      {"path": {"type": "string", "description": "path relative to workspace/, e.g. inbox/job_orders/JO-1042.yaml"}})
def read_job_order(path: str) -> dict:
    p = store.ws_path(path)
    if not p.exists():
        raise FileNotFoundError(f"job order not found: {path}")
    order = yaml.safe_load(p.read_text())
    order.setdefault("id", p.stem)
    return order


def extract_text(p: Path) -> str:
    suf = p.suffix.lower()
    if suf in (".txt", ".md"):
        return p.read_text(errors="ignore")
    if suf == ".pdf":
        from pypdf import PdfReader
        return "\n".join((pg.extract_text() or "") for pg in PdfReader(str(p)).pages)
    if suf == ".docx":
        import docx
        return "\n".join(par.text for par in docx.Document(str(p)).paragraphs)
    raise ValueError(f"unsupported resume format: {suf} (use .txt, .md, .pdf or .docx)")


@tool("Extract the plain text of a resume (.txt, .md, .pdf, .docx).",
      {"path": {"type": "string"}})
def read_resume(path: str) -> dict:
    p = store.ws_path(path)
    text = extract_text(p)
    return {"path": store.rel(p), "chars": len(text), "text": text[:12000]}


def _matches(req: str, text: str) -> bool:
    # "forklift|reach truck" = any synonym counts
    return any(re.search(r"\b" + re.escape(alt.strip().lower()) + r"\b", text) for alt in req.split("|"))


def _years(text: str) -> int:
    nums = [int(n) for n in re.findall(r"(\d{1,2})\+?\s*(?:years|yrs)", text)]
    return max(nums) if nums else 0


@tool("Score a resume against a job order. Deterministic: requirement matches, years of experience, "
      "decision shortlist / review / reject with reasons. Uses job-relevant criteria only.",
      {"resume_path": {"type": "string"}, "job_order_path": {"type": "string"}})
def screen_resume(resume_path: str, job_order_path: str) -> dict:
    order = read_job_order(job_order_path)
    p = store.ws_path(resume_path)
    raw = extract_text(p)
    text = raw.lower()
    name = next((ln.strip() for ln in raw.splitlines() if ln.strip()), p.stem)

    must = order.get("must_have", [])
    nice = order.get("nice_to_have", [])
    must_hit = [m for m in must if _matches(m, text)]
    nice_hit = [n for n in nice if _matches(n, text)]
    yrs = _years(text)
    min_yrs = order.get("min_years", 0)

    must_cov = len(must_hit) / len(must) if must else 1.0
    score = round(70 * must_cov + 20 * (len(nice_hit) / len(nice) if nice else 0)
                  + (10 if yrs >= min_yrs else 10 * yrs / max(min_yrs, 1)))
    if must_cov == 1.0 and yrs >= min_yrs:
        decision = "shortlist"
    elif must_cov >= 0.5:
        decision = "review"
    else:
        decision = "reject"

    return {
        "candidate_id": re.sub(r"[^a-z0-9]+", "_", p.stem.lower()),
        "name": name,
        "job_order": order["id"],
        "role": order.get("title"),
        "score": score,
        "decision": decision,
        "years_experience": yrs,
        "must_have_met": [m.split("|")[0] for m in must_hit],
        "must_have_missing": [m.split("|")[0] for m in must if m not in must_hit],
        "nice_to_have_met": [n.split("|")[0] for n in nice_hit],
        "resume": store.rel(p),
        "status": {"shortlist": "shortlisted", "review": "needs_review", "reject": "rejected"}[decision],
    }


def _load() -> list[dict]:
    return json.loads(CANDIDATES.read_text()) if CANDIDATES.exists() else []


def _save(rows: list[dict]) -> None:
    CANDIDATES.parent.mkdir(parents=True, exist_ok=True)
    CANDIDATES.write_text(json.dumps(rows, indent=2))


@tool("Add or update a candidate in the tracker (workspace/data/candidates.json).",
      {"record": {"type": "object", "description": "must include candidate_id"}},
      produces_artifacts=True)
def upsert_candidate(record: dict) -> dict:
    rows = [r for r in _load() if not (r["candidate_id"] == record["candidate_id"]
                                       and r.get("job_order") == record.get("job_order"))]
    record = {**record, "updated": store.now()}
    rows.append(record)
    _save(rows)
    return {"path": store.rel(CANDIDATES), "candidate_id": record["candidate_id"], "status": record.get("status")}


@tool("Set a candidate's pipeline status (e.g. cleared, docs_missing, placed).",
      {"candidate_id": {"type": "string"}, "status": {"type": "string"}},
      produces_artifacts=True)
def update_candidate_status(candidate_id: str, status: str) -> dict:
    rows = _load()
    hit = False
    for r in rows:
        if r["candidate_id"] == candidate_id:
            r["status"], r["updated"], hit = status, store.now(), True
    if not hit:
        raise KeyError(f"candidate {candidate_id} not in tracker")
    _save(rows)
    return {"path": store.rel(CANDIDATES), "candidate_id": candidate_id, "status": status}
