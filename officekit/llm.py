"""Minimal Claude Messages API client (stdlib only, so no SDK install is required)."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = os.environ.get("OFFICE_MODEL", "claude-sonnet-5-5")


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def create(system: str, messages: list[dict], tools: list[dict], model: str | None = None,
           max_tokens: int = 4096) -> dict:
    body = {"model": model or DEFAULT_MODEL, "max_tokens": max_tokens, "system": system,
            "messages": messages, "tools": tools}
    req = urllib.request.Request(
        API_URL, data=json.dumps(body).encode(),
        headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01",
                 "content-type": "application/json"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 529) and attempt < 3:
                time.sleep(2 ** attempt * 2)
                continue
            raise RuntimeError(f"Claude API {e.code}: {e.read().decode()[:300]}") from e
    raise RuntimeError("Claude API: retries exhausted")
