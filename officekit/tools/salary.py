"""Salary benchmarks from the U.S. Bureau of Labor Statistics (OEWS national wage estimates).

Uses the public BLS API (https://api.bls.gov/publicAPI/v2/). Set BLS_API_KEY for higher limits
(free key: https://data.bls.gov/registrationEngine/). Results are cached in workspace/data/salary_cache.json.
Job boards like Seek or Indeed prohibit scraping in their terms, so they are not used here; if you get
licensed API access to one, add it as another source in this file.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

import yaml

from .. import store
from . import tool


def _cache_file():
    return store.WS / "data" / "salary_cache.json"


def _soc_map_file():
    return store.WS / "data" / "soc_map.yaml"


API = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
# OEWS datatype codes (national, all industries)
DATATYPES = {"03": "hourly_mean", "07": "hourly_p25", "08": "hourly_median", "09": "hourly_p75",
             "04": "annual_mean", "12": "annual_p25", "13": "annual_median", "14": "annual_p75"}


def _soc_for(title: str) -> tuple[str, str] | None:
    table = yaml.safe_load(_soc_map_file().read_text())
    t = title.lower()
    for row in table:
        if any(k in t for k in row["keywords"]):
            return row["soc"], row["title"]
    return None


def _fetch(soc: str) -> dict:
    series = {f"OEUN0000000000000{soc}{code}": name for code, name in DATATYPES.items()}
    body = {"seriesid": list(series), "latest": True}
    if os.environ.get("BLS_API_KEY"):
        body["registrationkey"] = os.environ["BLS_API_KEY"]
    req = urllib.request.Request(API, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        resp = json.load(r)
    if resp.get("status") != "REQUEST_SUCCEEDED":
        raise RuntimeError(f"BLS API: {resp.get('status')} {resp.get('message')}")
    out, year = {}, None
    for s in resp["Results"]["series"]:
        if s["data"]:
            v = s["data"][0]["value"]
            out[series[s["seriesID"]]] = float(v) if re.fullmatch(r"[\d.]+", v) else None
            year = s["data"][0]["year"]
    if not any(out.values()):
        raise RuntimeError(f"BLS returned no wage data for SOC {soc}")
    return {"values": out, "year": year}


@tool("Get a national pay benchmark for a job title from BLS wage data (mean, 25th, median, 75th "
      "percentile; hourly and annual). Returns source and data year. Fails clearly if offline.",
      {"job_title": {"type": "string"}})
def get_salary_benchmark(job_title: str) -> dict:
    hit = _soc_for(job_title)
    if not hit:
        raise LookupError(f"no SOC code mapped for '{job_title}'. Add keywords to workspace/data/soc_map.yaml")
    soc, soc_title = hit
    cache = json.loads(_cache_file().read_text()) if _cache_file().exists() else {}
    source = "BLS OEWS (cached)"
    if soc not in cache:
        try:
            fetched = _fetch(soc)
        except Exception as e:  # network blocked, API limit, etc.
            raise RuntimeError(f"could not reach BLS for SOC {soc}: {e}. No benchmark produced.") from e
        cache[soc] = {**fetched, "fetched": store.now(), "soc_title": soc_title}
        _cache_file().parent.mkdir(parents=True, exist_ok=True)
        _cache_file().write_text(json.dumps(cache, indent=2))
        source = "BLS OEWS (live)"
    c = cache[soc]
    return {"job_title": job_title, "soc": soc, "soc_title": c["soc_title"], "data_year": c["year"],
            "source": source, "scope": "United States, all industries", **c["values"]}


@tool("Benchmark a job order's offered pay against BLS data and return the comparison with a verdict.",
      {"job_order_path": {"type": "string"}})
def compare_offer(job_order_path: str) -> dict:
    from .recruiting import read_job_order
    order = read_job_order(job_order_path)
    bench = get_salary_benchmark(order["title"])
    offer = order.get("pay_offered", {})
    return {**bench, "offered_min": offer.get("min"), "offered_max": offer.get("max"),
            "offered_unit": offer.get("unit", "hour"), "verdict": verdict(offer, bench)}


def verdict(offer: dict, bench: dict) -> str:
    """Compare an hourly offer with the benchmark."""
    hi = offer.get("max")
    p25, med = bench.get("hourly_p25"), bench.get("hourly_median")
    if not (hi and p25 and med):
        return "not enough data to compare"
    if hi < p25:
        return f"top of range ${hi}/hr is below the national 25th percentile (${p25}/hr): hard to fill"
    if hi < med:
        return f"top of range ${hi}/hr is below the national median (${med}/hr): competitive only locally"
    return f"top of range ${hi}/hr is at or above the national median (${med}/hr)"
