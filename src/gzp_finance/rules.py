from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable

UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
IBAN_RE = re.compile(r"\b[a-z]{2}\d{2}[a-z0-9]{10,30}\b", re.I)
DATE_RE = re.compile(r"\b\d{1,4}[/-]\d{1,2}[/-]\d{1,4}\b")
NUMBER_RE = re.compile(r"\b\d+(?:[.,]\d+)?\b")
NON_ALPHA_RE = re.compile(r"[^a-z]+")
NOISE_WORDS = {
    "amount", "card", "customer", "eur", "fee", "gross", "net",
    "payment", "ref", "reference", "tax", "transaction",
}


def normalize_concept(value: str | None) -> str:
    """Create a stable merchant/concept key without amounts, UUIDs or IBANs."""
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = UUID_RE.sub(" ", text)
    text = IBAN_RE.sub(" ", text)
    text = DATE_RE.sub(" ", text)
    text = NUMBER_RE.sub(" ", text)
    text = NON_ALPHA_RE.sub(" ", text)
    tokens = [t for t in text.split() if t not in NOISE_WORDS]
    return " ".join(tokens)


def transaction_context(transaction: dict[str, Any]) -> dict[str, str]:
    raw = transaction.get("concept_raw") or transaction.get("description_raw") or ""
    return {
        "source_bank": str(transaction.get("source_bank") or ""),
        "concept_key": normalize_concept(str(raw)),
        "bank_type": str(transaction.get("bank_type") or ""),
        "bank_category": str(transaction.get("bank_category") or ""),
        "direction": str(transaction.get("direction") or ""),
    }


def _matches(rule: dict[str, Any], context: dict[str, str]) -> bool:
    return all(context.get(key, "") == str(value) for key, value in rule["match"].items())


def apply_rules(transaction: dict[str, Any], rules: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Apply deterministic rules and fill only fields supported by a rule.

    Higher-priority rules win. Lower-priority rules can fill fields that remain empty,
    but never silently overwrite an earlier prediction.
    """
    context = transaction_context(transaction)
    ordered = sorted(
        rules,
        key=lambda r: (-int(r.get("priority", 0)), -int(r.get("support", 0)), str(r.get("id", ""))),
    )

    values: dict[str, str] = {}
    evidence: dict[str, dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []

    for rule in ordered:
        if not _matches(rule, context):
            continue
        for field, value in rule.get("set", {}).items():
            if field not in values:
                values[field] = value
                evidence[field] = {
                    "rule_id": rule.get("id"),
                    "support": rule.get("support"),
                    "priority": rule.get("priority"),
                }
            elif values[field] != value:
                conflicts.append({
                    "field": field,
                    "kept": values[field],
                    "discarded": value,
                    "rule_id": rule.get("id"),
                })

    return {
        "values": values,
        "evidence": evidence,
        "conflicts": conflicts,
        "context": context,
    }


def load_rules(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return list(payload.get("rules", payload))
