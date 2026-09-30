from __future__ import annotations

import csv
import hashlib
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import select, text, update

from .db import (Classification, HistoricalRecord, Import, ModelVersion, Prediction, Rule,
                 TrainingExample, Transaction, TransactionComponent, now, uid)
from .domain import (MAIN_TARGETS, TARGETS, digest, month_bounds, snapshot,
                     validate_match, validate_values)
from .importers import parse_statement
from .rules import apply_rules, load_rules, transaction_context


def lock_import(session, bank: str) -> None:
    if session.bind.dialect.name == "postgresql":
        # Serialize simultaneous uploads per bank, including overlapping exports.
        key = int.from_bytes(hashlib.sha256(bank.encode()).digest()[:8], "big", signed=True)
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


def classify(session, tx: Transaction, *, models=None) -> Classification:
    current = session.scalar(select(Classification).where(Classification.transaction_id == tx.id).with_for_update())
    if current and current.confirmed_by_user:
        return current
    rules = [r.spec() for r in session.scalars(select(Rule).where(Rule.enabled.is_(True)))]
    result = apply_rules(tx.context(), rules)
    values, provenance = dict(result["values"]), {}
    for field, value in values.items():
        evidence = result["evidence"][field]
        provenance[field] = {"engine": "rule", "confidence": 1, **evidence}
        session.add(Prediction(transaction_id=tx.id, engine="rule", field=field,
                               predicted_value=value, confidence=1, accepted=True,
                               rule_id=evidence["rule_id"], evidence={
                                   **evidence, "conflicts": [c for c in result["conflicts"] if c["field"] == field]}))
    if models:
        for field, prediction in models.predict(tx.context(), resolved=set(values)).items():
            if field in values or field not in TARGETS:
                continue
            session.add(Prediction(transaction_id=tx.id, engine="ml", field=field,
                                   predicted_value=prediction["value"], confidence=prediction["confidence"],
                                   accepted=prediction["accepted"], model_version=models.version))
            if prediction["accepted"]:
                values[field] = prediction["value"]
                provenance[field] = {"engine": "ml", "confidence": prediction["confidence"],
                                     "model_version": models.version}
    if not current:
        current = Classification(transaction_id=tx.id)
        session.add(current)
    current.values, current.provenance = values, provenance
    current.revision = (current.revision or 0) + 1
    session.flush()
    return current


def import_statement(session, bank: str, filename: str, data: bytes, *, models=None,
                     month: str = "") -> dict:
    transactions, counts = parse_statement(bank, data)
    if month:
        start, end = month_bounds(month)
        transactions = [tx for tx in transactions if start <= tx["bank_date"] < end]
        if not transactions:
            raise ValueError(f"El extracto no contiene movimientos de {month}")
        counts = {**counts, "month": month, "selected_rows": len(transactions)}
    # One full bank export can be imported month by month without a same-file collision.
    file_hash = hashlib.sha256(data + (b"\0month:" + month.encode() if month else b"")).hexdigest()
    lock_import(session, bank)
    existing = session.scalar(select(Import).where(Import.source_bank == bank, Import.file_hash == file_hash))
    if existing:
        return {"import_id": existing.id, "same_file": True, "inserted": 0,
                "duplicates": len(transactions), **counts}
    imp = Import(source_bank=bank, filename=Path(filename.replace("\\", "/")).name[:255], file_hash=file_hash)
    session.add(imp)
    session.flush()
    inserted, duplicates = 0, 0
    complete_proposals, with_rules, with_ml = 0, 0, 0
    for tx in transactions:
        # Grouped fills keep every underlying bank ID to detect partial overlaps.
        rows = tx["raw_payload"].get("rows", [])
        ids = sorted(set(r.get("transaction_id") for r in rows if r.get("transaction_id")))
        if ids:
            known = list(session.scalars(select(TransactionComponent).where(
                TransactionComponent.source_bank == bank,
                TransactionComponent.account_ref == tx.get("account_ref", ""),
                TransactionComponent.external_id.in_(ids))))
            if known:
                owners = {c.transaction_id for c in known}
                if len(known) != len(ids) or len(owners) != 1:
                    raise ValueError("Fills parcialmente solapados: importa un extracto completo de esa orden")
                all_ids = set(session.scalars(select(TransactionComponent.external_id).where(
                    TransactionComponent.transaction_id == next(iter(owners)))))
                if all_ids != set(ids):
                    raise ValueError("Orden incompleta respecto a una importación anterior")
                duplicates += 1
                continue
        exists = session.scalar(select(Transaction.id).where(
            Transaction.fingerprint == tx["fingerprint"], Transaction.occurrence == tx["occurrence"]))
        if exists:
            duplicates += 1
            continue
        record = Transaction(import_id=imp.id, **tx)
        session.add(record)
        session.flush()
        for external_id in ids:
            session.add(TransactionComponent(transaction_id=record.id, source_bank=bank,
                                              account_ref=tx.get("account_ref", ""), external_id=external_id))
        classification = classify(session, record, models=models)
        complete_proposals += all(classification.values.get(field) for field in MAIN_TARGETS)
        with_rules += any(source.get("engine") == "rule" for source in classification.provenance.values())
        with_ml += any(source.get("engine") == "ml" for source in classification.provenance.values())
        inserted += 1
    imp.counts = {**counts, "inserted": inserted, "duplicates": duplicates,
                  "complete_proposals": complete_proposals, "with_rules": with_rules,
                  "with_ml": with_ml}
    session.flush()
    from .history import link_history
    link_history(session)
    return {"import_id": imp.id, "same_file": False, **imp.counts}


def import_rules(session, path: Path, source: str) -> int:
    if source not in {"learned", "user_confirmed", "structural"}:
        raise ValueError("Origen de reglas no válido")
    specs = load_rules(path)
    # Validate the entire payload before writing any rule.
    for spec in specs:
        validate_match(spec["match"])
        validate_values(spec["set"])
        if not spec["set"] or not spec.get("id"):
            raise ValueError("Regla sin identificador o valores")
        if source == "learned" and int(spec.get("priority", 0)) >= 1000:
            raise ValueError("Las reglas aprendidas deben tener prioridad inferior a 1000")
    for spec in specs:
        rule = session.get(Rule, str(spec["id"]))
        if rule and rule.source == "user_confirmed" and source != "user_confirmed":
            continue
        if not rule:
            rule = Rule(id=str(spec["id"]))
            session.add(rule)
        rule.kind, rule.source = spec.get("kind", source), source
        rule.priority = max(1000, int(spec.get("priority", 0))) if source == "user_confirmed" else int(spec.get("priority", 0))
        rule.match, rule.set_values = spec["match"], spec["set"]
        rule.support = int(spec.get("support", 1))
        rule.historical_consistency = spec.get("historical_consistency")
        if rule.enabled is None:
            rule.enabled = True
    session.flush()
    return len(specs)


def confirm(session, transaction_id: str, values: dict, *, training: bool,
            revision: int, rule_mode: str = "none", custom_match: dict | None = None) -> dict:
    tx = session.get(Transaction, transaction_id)
    current = session.scalar(select(Classification).where(
        Classification.transaction_id == transaction_id).with_for_update())
    if not tx or not current:
        raise LookupError("Transacción no encontrada")
    if current.revision != revision:
        raise ValueError("La clasificación ha cambiado. Recarga antes de guardar")
    if rule_mode not in {"none", "concept", "concept_amount", "custom"}:
        raise ValueError("Modo de regla desconocido")
    values = validate_values(values)
    values = {k: v.strip() for k, v in values.items()}
    predictions = list(session.scalars(select(Prediction).where(Prediction.transaction_id == tx.id)))
    # Retain immutable audit snapshots; only the latest enabled labels train future models.
    session.execute(update(TrainingExample).where(TrainingExample.transaction_id == tx.id).values(enabled=False))
    current.values = values
    current.provenance = {field: {"engine": "manual", "confidence": 1} for field in values}
    current.confirmed_by_user, current.confirmed_at = True, now()
    current.revision += 1
    model_versions = [p.model_version for p in predictions if p.model_version]
    session.add(TrainingExample(
        transaction_id=tx.id, classification_id=current.id, source="user_confirmed", enabled=training,
        input_snapshot=snapshot(tx.context()), final_values=values,
        prediction_snapshot=[{"engine": p.engine, "field": p.field, "value": p.predicted_value,
                              "confidence": float(p.confidence), "rule_id": p.rule_id,
                              "model_version": p.model_version, "accepted": p.accepted,
                              "evidence": p.evidence} for p in predictions],
        model_version=model_versions[-1] if model_versions else None,
    ))
    created_rule = None
    if rule_mode != "none":
        context = transaction_context(tx.context())
        match = {"source_bank": tx.source_bank, "concept_key": tx.concept_key}
        # Prevent a BUY rule becoming a SELL rule for the same security.
        if tx.bank_type:
            match["bank_type"] = tx.bank_type
        if rule_mode == "concept_amount":
            match["amount_cents"] = context["amount_cents"]
        elif rule_mode == "custom":
            match = custom_match or {}
        match = validate_match(match)
        if not apply_rules(tx.context(), [{"match": match, "set": {"test": "yes"}}])["values"]:
            raise ValueError("La regla personalizada no coincide con esta transacción")
        # Empty fields remain manual; avoid blocking future learned detail with blanks.
        outputs = {k: v for k, v in values.items() if v}
        if not outputs:
            raise ValueError("Una regla necesita al menos un valor")
        created_rule = "confirmed:" + uid()
        priority = max([1200] + [r.priority + 1 for r in session.scalars(select(Rule)) if r.match == match])
        session.add(Rule(id=created_rule, kind=rule_mode, source="user_confirmed", priority=priority,
                         match=match, set_values=outputs, support=1))
    session.flush()
    return {"transaction_id": tx.id, "revision": current.revision, "rule_id": created_rule}


def rule_candidates(session) -> list[dict]:
    # Deduplicate by normalized input (historical seed and a later confirmation can overlap).
    groups = defaultdict(dict)
    for example in session.scalars(select(TrainingExample).where(TrainingExample.enabled.is_(True))):
        tx = example.input_snapshot
        concept = transaction_context(tx)["concept_key"]
        if not concept:
            continue
        key = (tx.get("source_bank", ""), concept, tx.get("bank_type", ""))
        identity = digest([tx.get("transaction_id") or str(tx.get("bank_date")),
                           transaction_context(tx)["amount_cents"], concept])
        groups[key][identity] = example.final_values
    out = []
    for (bank, concept, bank_type), examples in groups.items():
        labels = list(examples.values())
        if len(labels) < 3:
            continue
        values = {field: labels[0][field] for field in TARGETS if labels[0].get(field)
                  and all(row.get(field) == labels[0][field] for row in labels)}
        if not values:
            continue
        match = {"source_bank": bank, "concept_key": concept}
        if bank_type:
            match["bank_type"] = bank_type
        out.append({"match": match, "set": values, "support": len(labels), "historical_consistency": 1})
    return sorted(out, key=lambda c: -c["support"])


def accept_candidate(session, candidate: dict) -> str:
    fresh = next((c for c in rule_candidates(session) if c["match"] == candidate.get("match")
                  and c["set"] == candidate.get("set")), None)
    if not fresh:
        raise ValueError("La propuesta ya no cumple soporte y consistencia")
    rule_id = "candidate:" + digest([fresh["match"], fresh["set"]])[:24]
    if not session.get(Rule, rule_id):
        session.add(Rule(id=rule_id, source="user_confirmed", kind="confirmed_candidate", priority=1200,
                         match=fresh["match"], set_values=fresh["set"], support=fresh["support"],
                         historical_consistency=1))
    return rule_id


def seed_history(session, path: Path) -> dict:
    inserted, skipped = 0, 0
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("match_confidence") != "Alta":
                skipped += 1
                continue
            key = digest(["historical", row.get("record_id"), row.get("source_bank"),
                          row.get("bank_date"), row.get("transaction_id"), row.get("source_row")])
            if session.scalar(select(TrainingExample.id).where(TrainingExample.external_key == key)):
                skipped += 1
                continue
            # Never use manual_date/model_text (possible label leakage) as input features.
            inputs = {k: v for k, v in row.items() if not k.startswith("target_")
                      and k not in {"manual_date", "model_text"}}
            labels = {k: row.get(k, "") for k in TARGETS if row.get(k)}
            session.add(TrainingExample(external_key=key, source="historical", enabled=True,
                                        input_snapshot=inputs, final_values=labels,
                                        dataset_split=row.get("dataset_split", "train"),
                                        sample_weight=float(row.get("sample_weight") or 1)))
            inserted += 1
    session.flush()
    return {"inserted": inserted, "skipped": skipped}


def review_status(classification: Classification) -> str:
    if classification.confirmed_by_user:
        return "confirmada"
    if not any(classification.values.values()):
        return "sin clasificar"
    if all(classification.values.get(f) for f in MAIN_TARGETS):
        return "completa"
    return "revisar"


def vocabulary(session) -> dict:
    """Use existing labels verbatim; an unconfirmed ML guess is not a vocabulary source."""
    fields = {field: set() for field in TARGETS}
    subtypes = defaultdict(set)
    sources = [e.final_values for e in session.scalars(select(TrainingExample))]
    sources.extend(h.values for h in session.scalars(select(HistoricalRecord)))
    sources.extend(c.values for c in session.scalars(select(Classification).where(
        Classification.confirmed_by_user.is_(True))))
    sources.extend(r.set_values for r in session.scalars(select(Rule)))
    for values in sources:
        for field, value in values.items():
            if field in fields and isinstance(value, str) and value.strip():
                fields[field].add(value)
        category, subtype = values.get("target_categoria_general"), values.get("target_subtipo")
        if category and subtype:
            subtypes[category].add(subtype)
    return {"fields": {field: sorted(values, key=str.casefold) for field, values in fields.items()},
            "subtypes": {category: sorted(values, key=str.casefold) for category, values in subtypes.items()}}
