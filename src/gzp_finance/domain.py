from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from .rules import amount_to_cents, normalize_concept

BANKS = ("Trade Republic", "CaixaBank", "MyInvestor")
TARGETS = tuple("target_" + f for f in (
    "tipo_transaccion", "tipo_gasto", "fiscalidad", "categoria_general",
    "subtipo", "activo", "detalle", "detalle2",
))
MAIN_TARGETS = ("target_tipo_transaccion", "target_categoria_general", "target_subtipo")
SELECT_TARGETS = TARGETS[:5]
TARGET_LABELS = dict(zip(TARGETS, ("Tipo de transacción", "Tipo de gasto", "Fiscalidad",
    "Categoría general", "Subtipo", "Activo", "Detalle", "Detalle 2")))
MATCH_KEYS = {"source_bank", "concept_key", "bank_type", "bank_category", "direction",
              "amount_cents", "amount_cents_min", "amount_cents_max", "counterparty_iban"}


def month_bounds(month: str) -> tuple[date, date]:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise ValueError("Selecciona un mes válido (AAAA-MM)")
    start = date.fromisoformat(f"{month}-01")
    end = date(start.year + 1, 1, 1) if start.month == 12 else date(start.year, start.month + 1, 1)
    return start, end


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    default=str, separators=(",", ":")).encode()).hexdigest()


def snapshot(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str, ensure_ascii=False))


def normalized(*, bank: str, bank_date: date, amount: Decimal, concept: str,
               raw: Any, **extra: Any) -> dict:
    amount = amount.quantize(Decimal("0.01"))
    return {
        "source_bank": bank, "bank_date": bank_date, "amount_eur": amount,
        "direction": "gasto" if amount < 0 else "ingreso", "concept_raw": concept,
        "concept_key": normalize_concept(concept), "currency": "EUR",
        "raw_payload": raw, **extra,
    }


def fingerprint(tx: dict) -> str:
    # Include account: bank transaction IDs are not necessarily globally unique.
    if tx.get("transaction_id"):
        return digest([tx["source_bank"], tx.get("account_ref", ""), tx["transaction_id"]])
    return digest([tx["source_bank"], tx.get("account_ref", ""), tx["bank_date"],
                   tx.get("value_date"), amount_to_cents(tx["amount_eur"]), tx["concept_key"],
                   tx.get("bank_type", ""), tx.get("bank_category", ""),
                   tx.get("counterparty_iban", ""), tx.get("payment_reference", ""),
                   tx.get("description_raw", ""), tx.get("mcc", "")])


def validate_values(values: dict) -> dict[str, str]:
    if not isinstance(values, dict) or set(values) - set(TARGETS):
        raise ValueError("Campos de clasificación desconocidos")
    if any(not isinstance(v, str) or len(v) > 500 for v in values.values()):
        raise ValueError("Las clasificaciones deben ser textos de hasta 500 caracteres")
    return dict(values)


def validate_match(match: dict) -> dict[str, str]:
    if not isinstance(match, dict) or not match or set(match) - MATCH_KEYS:
        raise ValueError("Condiciones de regla desconocidas o vacías")
    if not match.get("source_bank") in BANKS:
        raise ValueError("La regla debe indicar un banco válido")
    if not any(match.get(k) for k in MATCH_KEYS - {"source_bank", "direction"}):
        raise ValueError("La regla necesita un identificador o condición concreta")
    if any(not isinstance(v, (str, int)) or len(str(v)) > 500 for v in match.values()):
        raise ValueError("Condiciones inválidas")
    out = {k: str(v) for k, v in match.items()}
    for k in ("amount_cents", "amount_cents_min", "amount_cents_max"):
        if k in out:
            out[k] = str(int(out[k]))
    if ("amount_cents_min" in out and "amount_cents_max" in out
            and int(out["amount_cents_min"]) > int(out["amount_cents_max"])):
        raise ValueError("Rango de importes invertido")
    if "concept_key" in out and not out["concept_key"].strip():
        raise ValueError("El concepto no puede estar vacío")
    return out
