from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from .db import Classification, HistoricalRecord, Transaction, TransactionComponent
from .domain import BANKS, digest, validate_values
from .rules import normalize_concept


def load_reference(session, path: Path, dataset: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    # Extraction retains every original row, including banks outside the three importers.
    # Cell typing is supplementary metadata: adding it must not change the identity
    # of an already loaded workbook or duplicate its historical rows.
    identity = {**payload, "records": [
        {k: v for k, v in row.items() if k != "excel_amount_is_numeric"}
        for row in payload["records"]]}
    source_hash = digest(identity)
    with dataset.open(encoding="utf-8-sig", newline="") as f:
        bank_rows = {(r["source_bank"], int(r["source_row"])): r for r in csv.DictReader(f)}
    existing = {r.source_row: r for r in session.scalars(select(HistoricalRecord).where(
        HistoricalRecord.source_hash == source_hash))}
    other_source = session.scalar(select(HistoricalRecord.id).where(HistoricalRecord.source_hash != source_hash))
    if other_source:
        raise ValueError("Ya existe otro Excel de referencia; no mezcles versiones del mismo histórico")
    inserted = 0
    for row in payload["records"]:
        numeric = row.get("excel_amount_is_numeric", True)
        if not isinstance(numeric, bool):
            raise ValueError("excel_amount_is_numeric debe ser booleano")
        if row["source_row"] in existing:
            if "excel_amount_is_numeric" in row:
                existing[row["source_row"]].excel_amount_is_numeric = numeric
            continue
        original = validate_values(row["values"])
        matched = bank_rows.get((row["source_bank"], row["source_row"]), {})
        inputs = {k: v for k, v in matched.items() if not k.startswith("target_")
                  and k not in {"manual_date", "model_text"}}
        session.add(HistoricalRecord(source_hash=source_hash, source_file=payload["filename"],
            source_sheet=payload["sheet"], source_row=row["source_row"], source_bank=row["source_bank"],
            manual_date=date.fromisoformat(row["manual_date"]),
            amount_eur=Decimal(row["amount_eur"]) if row["amount_eur"] is not None else None,
            excel_amount_is_numeric=numeric,
            values=original, bank_input=inputs, match_confidence=matched.get("match_confidence", "")))
        inserted += 1
    session.flush()
    return {"inserted": inserted, **link_history(session)}


def link_history(session) -> dict:
    transactions = list(session.scalars(select(Transaction)))
    by_id, by_key = defaultdict(list), defaultdict(list)
    for tx in transactions:
        by_key[(tx.source_bank, str(tx.bank_date), tx.amount_eur, tx.concept_key)].append(tx)
        if tx.transaction_id:
            by_id[(tx.source_bank, tx.transaction_id)].append(tx)
    owners = {t.id: t for t in transactions}
    for component in session.scalars(select(TransactionComponent)):
        key = (component.source_bank, component.external_id)
        owner = owners[component.transaction_id]
        if owner not in by_id[key]:
            by_id[key].append(owner)
    matched, unresolved = 0, 0
    for ref in session.scalars(select(HistoricalRecord).order_by(HistoricalRecord.source_row)):
        if ref.transaction_id:
            matched += 1
            continue
        row = ref.bank_input
        if not row:
            unresolved += 1
            continue
        candidates = by_id.get((ref.source_bank, row.get("transaction_id")), []) if row.get("transaction_id") else []
        amount = Decimal(row["amount_eur"]).quantize(Decimal(".01"))
        if not candidates:
            candidates = by_key.get((ref.source_bank, row["bank_date"], amount,
                                     normalize_concept(row.get("concept_raw", ""))), [])
            if row.get("account_ref"):
                candidates = [t for t in candidates if t.account_ref == row["account_ref"]]
        if len(candidates) == 1:
            ref.transaction_id = candidates[0].id
            ref.linkage_status = "matched"
            matched += 1
        else:
            ref.linkage_status = "ambiguous" if candidates else "unmatched"
            unresolved += 1
    session.flush()
    return {"matched": matched, "unresolved": unresolved}


def totals(amounts) -> dict:
    amounts = list(amounts)
    known = [Decimal(a) for a in amounts if a is not None]
    incoming = sum((a for a in known if a > 0), Decimal(0))
    outgoing = -sum((a for a in known if a < 0), Decimal(0))
    return {"count": len(amounts), "missing_amount": len(amounts) - len(known),
            "income": str(incoming.quantize(Decimal('.01'))),
            "expenses": str(outgoing.quantize(Decimal('.01'))),
            "net": str((incoming - outgoing).quantize(Decimal('.01')))}


def validation_summary(session) -> dict:
    txs = list(session.scalars(select(Transaction)))
    refs = list(session.scalars(select(HistoricalRecord)))
    confirmed = set(session.scalars(select(Classification.transaction_id).where(
        Classification.confirmed_by_user.is_(True))))
    by_id = {t.id: t for t in txs}
    linked_ids = {r.transaction_id for r in refs if r.transaction_id}
    banks = []
    excel_banks = []
    source_labels = {"CaixaBank": "01. La Caixa", "MyInvestor": "03. MyInvestor",
                     "Trade Republic": "04. Trade Republic"}
    for bank in sorted({r.source_bank for r in refs}, key=lambda name: source_labels.get(name, name)):
        original = [r for r in refs if r.source_bank == bank]
        excel_banks.append({"bank": source_labels.get(bank, bank), "totals": totals(
            r.amount_eur if r.excel_amount_is_numeric else None for r in original)})
    for bank in BANKS:
        imported = [t for t in txs if t.source_bank == bank]
        original = [r for r in refs if r.source_bank == bank]
        # One original row per bank movement; grouped/ambiguous relations are reported separately.
        links = defaultdict(list)
        for r in original:
            if r.transaction_id:
                links[r.transaction_id].append(r)
        comparable = [rr[0] for tid, rr in links.items() if len(rr) == 1 and
                      rr[0].amount_eur is not None and rr[0].excel_amount_is_numeric and tid in by_id]
        comparable_ids = {r.id for r in comparable}
        excel = totals(r.amount_eur for r in comparable)
        actual = totals(by_id[r.transaction_id].amount_eur for r in comparable)
        deltas = {k: str((Decimal(actual[k]) - Decimal(excel[k])).quantize(Decimal('.01')))
                  for k in ("income", "expenses", "net")}
        mismatched = sum(by_id[r.transaction_id].amount_eur != r.amount_eur for r in comparable)
        banks.append({"bank": bank, "imported": totals(t.amount_eur for t in imported),
            "new": totals(t.amount_eur for t in imported if t.id not in linked_ids),
            "historical": totals(t.amount_eur for t in imported if t.id in linked_ids),
            "excel": totals(r.amount_eur if r.excel_amount_is_numeric else None for r in original),
            "outside_comparison": totals(r.amount_eur if r.excel_amount_is_numeric else None
                                         for r in original if r.id not in comparable_ids),
            "text_amount_rows": sum(r.amount_eur is not None and not r.excel_amount_is_numeric
                                    for r in original),
            "linked_rows": sum(len(v) for v in links.values()),
            "unlinked_rows": sum(not r.transaction_id for r in original),
            "ambiguous_rows": sum(r.linkage_status == "ambiguous" for r in original),
            "grouped_rows": sum(len(rr) for rr in links.values() if len(rr) > 1),
            "confirmed": sum(t.id in confirmed for t in imported),
            "comparison": {"excel": excel, "bank": actual, "delta": deltas,
                           "mismatched_rows": mismatched, "matches": bool(comparable) and mismatched == 0}})
    return {"banks": banks, "excel_banks": excel_banks,
            "excel_total": totals(r.amount_eur if r.excel_amount_is_numeric else None for r in refs),
            "excel_records": len(refs), "imported_records": len(txs),
            "historical_records": len(linked_ids), "new_records": len(txs) - len(linked_ids),
            "confirmed_records": len(confirmed)}
