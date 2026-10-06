"""Timesheets -> payroll export + per-client invoices, with anomaly checks."""
from __future__ import annotations

import csv
from collections import defaultdict

from .. import store
from . import tool

RATES = store.WS / "data" / "rates.csv"
MAX_WEEKLY_HOURS = 60


def _rates() -> dict[tuple[str, str], dict]:
    with open(RATES, newline="") as fh:
        return {(r["candidate_id"], r["client"]): r for r in csv.DictReader(fh)}


def _f(x) -> float:
    try:
        return float(x or 0)
    except ValueError:
        return float("nan")


@tool("Process a timesheet CSV: compute pay and bill amounts from rates.csv, write a payroll export "
      "and one invoice per client (PDF when reportlab is installed, else markdown), and flag anomalies "
      "(missing rates, >60 hours, negative or non-numeric hours, margin below 15%).",
      {"path": {"type": "string", "description": "timesheet CSV path relative to workspace/"}},
      produces_artifacts=True)
def process_timesheet(path: str) -> dict:
    src = store.ws_path(path)
    rates = _rates()
    out_dir = store.OUTPUTS / "paybill" / src.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    payroll, anomalies = [], []
    invoices: dict[str, list[dict]] = defaultdict(list)
    with open(src, newline="") as fh:
        for i, row in enumerate(csv.DictReader(fh), start=2):
            cid, client = row["candidate_id"], row["client"]
            reg, ot = _f(row.get("hours_regular")), _f(row.get("hours_overtime"))
            who = f"line {i} {row.get('candidate_name', cid)}"
            if reg != reg or ot != ot or reg < 0 or ot < 0:
                anomalies.append(f"{who}: bad hours value, skipped")
                continue
            if reg + ot > MAX_WEEKLY_HOURS:
                anomalies.append(f"{who}: {reg + ot:g} hours in one week, confirm with client")
            r = rates.get((cid, client))
            if not r:
                anomalies.append(f"{who}: no rate on file for client {client}, skipped")
                continue
            pay, bill, mult = float(r["pay_rate"]), float(r["bill_rate"]), float(r.get("ot_multiplier") or 1.5)
            gross = round(reg * pay + ot * pay * mult, 2)
            billed = round(reg * bill + ot * bill * mult, 2)
            if billed and (billed - gross) / billed < 0.15:
                anomalies.append(f"{who}: margin {100 * (billed - gross) / billed:.1f}% is below 15%")
            payroll.append({"week_ending": row["week_ending"], "candidate_id": cid,
                            "candidate_name": row.get("candidate_name", ""), "client": client,
                            "hours_regular": reg, "hours_overtime": ot, "pay_rate": pay,
                            "gross_pay": gross})
            invoices[client].append({"candidate": row.get("candidate_name", cid), "week_ending": row["week_ending"],
                                     "reg": reg, "ot": ot, "rate": bill, "mult": mult, "amount": billed})

    artifacts = []
    pp = out_dir / "payroll_export.csv"
    with open(pp, "w", newline="") as fh:
        if payroll:
            w = csv.DictWriter(fh, fieldnames=list(payroll[0]))
            w.writeheader()
            w.writerows(payroll)
    artifacts.append(store.rel(pp))

    totals = {}
    for n, (client, lines) in enumerate(sorted(invoices.items()), start=1):
        inv_no = f"INV-{src.stem}-{n:02d}"
        totals[client] = round(sum(l["amount"] for l in lines), 2)
        artifacts.append(store.rel(_write_invoice(out_dir, inv_no, client, lines, totals[client])))

    if anomalies:
        ap = out_dir / "anomalies.txt"
        ap.write_text("\n".join(anomalies) + "\n")
        artifacts.append(store.rel(ap))

    gross_total = round(sum(p["gross_pay"] for p in payroll), 2)
    billed_total = round(sum(totals.values()), 2)
    summary = (f"{len(payroll)} timesheet lines: payroll ${gross_total:,.2f}, invoices ${billed_total:,.2f} "
               f"across {len(totals)} client(s), margin ${billed_total - gross_total:,.2f}. "
               f"{len(anomalies)} anomaly(ies) to review.")
    return {"summary": summary, "artifacts": artifacts, "payroll_total": gross_total,
            "invoice_totals": totals, "anomalies": anomalies}


def _write_invoice(out_dir, inv_no, client, lines, total):
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError:
        p = out_dir / f"{inv_no}.md"
        rows = "\n".join(f"| {l['candidate']} | {l['week_ending']} | {l['reg']:g} | {l['ot']:g} | "
                         f"${l['rate']:.2f} | ${l['amount']:,.2f} |" for l in lines)
        p.write_text(f"# Invoice {inv_no}\n\nBill to: {client}\n\n| Worker | Week ending | Reg hrs | OT hrs | Rate | Amount |\n"
                     f"|---|---|---|---|---|---|\n{rows}\n\n**Total due: ${total:,.2f}** (net 30)\n")
        return p

    p = out_dir / f"{inv_no}.pdf"
    ss = getSampleStyleSheet()
    data = [["Worker", "Week ending", "Reg hrs", "OT hrs", "Bill rate", "Amount"]]
    data += [[l["candidate"], l["week_ending"], f"{l['reg']:g}", f"{l['ot']:g}", f"${l['rate']:.2f}",
              f"${l['amount']:,.2f}"] for l in lines]
    data.append(["", "", "", "", "Total due", f"${total:,.2f}"])
    t = Table(data, hAlign="LEFT")
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2a44")),
                           ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                           ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
                           ("LINEABOVE", (0, -1), (-1, -1), 1, colors.black),
                           ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    doc = SimpleDocTemplate(str(p), pagesize=letter, title=inv_no)
    doc.build([Paragraph(f"Invoice {inv_no}", ss["Title"]),
               Paragraph(f"Bill to: <b>{client}</b><br/>Issued: {store.now()[:10]} · Terms: net 30", ss["Normal"]),
               Spacer(1, 16), t])
    return p
