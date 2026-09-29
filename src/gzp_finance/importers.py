from __future__ import annotations

import csv
import io
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from defusedxml import ElementTree as ET

from .domain import BANKS, digest, fingerprint, normalized


class ImportError(ValueError):
    """Invalid statement, with a safe row number rather than private row contents."""


def text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise ImportError("Codificación de archivo no soportada")


def money(value: Any, european: bool = False) -> Decimal:
    s = str(value or "0").replace("€", "").replace("\u00a0", "").replace(" ", "").strip()
    if european:
        s = s.replace(".", "").replace(",", ".") if "," in s else s
    try:
        result = Decimal(s)
        if not result.is_finite():
            raise InvalidOperation
        return result
    except InvalidOperation:
        raise ImportError("Importe no válido") from None


def bank_day(value: str) -> date:
    s = value.strip()
    try:
        if "/" in s:
            return datetime.strptime(s, "%d/%m/%Y").date()
        return date.fromisoformat(s[:10])
    except ValueError:
        raise ImportError("Fecha no válida") from None


def read_csv(data: bytes, delimiter: str, required: set[str]) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text(data)), delimiter=delimiter)
    if not reader.fieldnames or not required <= set(reader.fieldnames):
        raise ImportError("El archivo no tiene las columnas requeridas para este banco")
    rows = []
    for n, row in enumerate(reader, 2):
        if None in row or any(row.get(k) is None for k in required):
            raise ImportError(f"Fila {n}: número de columnas inválido")
        if all(not v for v in row.values()):
            continue
        row["_row"] = n
        rows.append(row)
    if not rows:
        raise ImportError("El extracto no contiene movimientos")
    return rows


def trade_republic(data: bytes) -> tuple[list[dict], dict]:
    rows = read_csv(data, ",", {"date", "datetime", "type", "name", "amount", "currency"})
    entries, skipped = [], 0
    for row in rows:
        try:
            # Security migrations move units, not EUR cash. Preserve their count explicitly.
            if row["type"] == "MIGRATION":
                skipped += 1
                continue
            if row["currency"] != "EUR":
                raise ImportError("La V0 solo admite importes en EUR")
            if not row["amount"].strip():
                raise ImportError("Importe ausente")
            stamp = datetime.fromisoformat(row["datetime"].replace("Z", "+00:00"))
            amount = money(row["amount"]) + money(row.get("fee")) + money(row.get("tax"))
            day = bank_day(row["date"])
            entries.append((stamp, normalized(
                bank="Trade Republic", bank_date=day, amount=amount,
                concept=row["name"] or row.get("description", ""), raw={"rows": [row]},
                value_date=day, bank_type=row["type"], bank_category=row.get("category", ""),
                account_ref=row.get("account_type", ""), description_raw=row.get("description", ""),
                counterparty_name=row.get("counterparty_name", ""),
                counterparty_iban=row.get("counterparty_iban", ""),
                payment_reference=row.get("payment_reference", ""), mcc=row.get("mcc_code", ""),
                transaction_id=row.get("transaction_id", ""),
            )))
        except (ValueError, TypeError):
            raise ImportError(f"Fila {row['_row']}: fecha, importe o divisa no válidos") from None
    entries.sort(key=lambda item: item[0])
    grouped, last_by_asset, merged = [], {}, 0
    for stamp, tx in entries:
        row = tx["raw_payload"]["rows"][0]
        key = (tx["account_ref"], tx["bank_date"], tx["bank_type"],
               row.get("symbol") or row["name"], row.get("asset_class", ""))
        previous = last_by_asset.get(key)
        if tx["bank_type"] in {"BUY", "SELL"} and previous and (stamp - previous[0]).total_seconds() <= 2:
            old = previous[1]
            old["amount_eur"] += tx["amount_eur"]
            old["raw_payload"]["rows"].append(row)
            last_by_asset[key] = (stamp, old)
            merged += 1
        else:
            grouped.append(tx)
            last_by_asset[key] = (stamp, tx)
    for tx in grouped:
        components = tx["raw_payload"]["rows"]
        if len(components) > 1:
            ids = sorted(r.get("transaction_id", "") for r in components)
            tx["transaction_id"] = "fills:" + digest(ids) if all(ids) else ""
        tx["direction"] = "gasto" if tx["amount_eur"] < 0 else "ingreso"
    return grouped, {"raw_rows": len(rows), "grouped_fills": merged, "skipped_non_cash": skipped}


def myinvestor(data: bytes) -> tuple[list[dict], dict]:
    rows = read_csv(data, ";", {"Fecha de operación", "Fecha de valor", "Concepto", "Importe", "Divisa"})
    txs = []
    for row in rows:
        try:
            if row["Divisa"].strip() != "EUR" or not row["Importe"].strip():
                raise ImportError("Divisa o importe no válidos")
            txs.append(normalized(bank="MyInvestor", bank_date=bank_day(row["Fecha de operación"]),
                                  value_date=bank_day(row["Fecha de valor"]),
                                  amount=money(row["Importe"], True), concept=row["Concepto"], raw=row))
        except ValueError:
            raise ImportError(f"Fila {row['_row']}: fecha, importe o divisa no válidos") from None
    return txs, {"raw_rows": len(rows), "grouped_fills": 0, "skipped_non_cash": 0}


def caixabank(data: bytes) -> tuple[list[dict], dict]:
    ns = "{urn:schemas-microsoft-com:office:spreadsheet}"
    try:
        root = ET.fromstring(data)
    except Exception:
        raise ImportError("XML de CaixaBank inválido") from None
    required = {"Fecha", "Concepto", "Categoría", "Importe (€)", "Tipo Movimiento", "Cuenta/Tarjeta"}
    txs, found = [], False
    for sheet in root.findall(ns + "Worksheet"):
        headers = None
        for number, row in enumerate(sheet.findall(".//" + ns + "Row"), 1):
            cells, index = {}, 1
            for cell in row.findall(ns + "Cell"):
                index = int(cell.get(ns + "Index", index))
                value = cell.find(ns + "Data")
                cells[index] = value.text or "" if value is not None else ""
                index += 1
            if required <= set(cells.values()):
                headers = {i: h for i, h in cells.items() if h in required}
                found = True
                continue
            if headers is None:
                continue
            values = {h: cells.get(i, "") for i, h in headers.items()}
            if not any(values.values()):
                continue
            if values["Fecha"].startswith(("Total Ingresos", "Total Gastos")):
                continue
            try:
                txs.append(normalized(
                    bank="CaixaBank", bank_date=bank_day(values["Fecha"]),
                    amount=money(values["Importe (€)"], True), concept=values["Concepto"],
                    bank_category=values["Categoría"], bank_type=values["Tipo Movimiento"],
                    account_ref=values["Cuenta/Tarjeta"], raw={"row": number, "cells": cells},
                ))
            except ValueError:
                raise ImportError(f"Fila {number}: fecha o importe no válidos") from None
    if not found or not txs:
        raise ImportError("No se encuentra una tabla de movimientos de CaixaBank")
    return txs, {"raw_rows": len(txs), "grouped_fills": 0, "skipped_non_cash": 0}


def parse_statement(bank: str, data: bytes) -> tuple[list[dict], dict]:
    if bank not in BANKS:
        raise ImportError("Banco desconocido")
    parser = dict(zip(BANKS, (trade_republic, caixabank, myinvestor)))[bank]
    txs, counts = parser(data)
    occurrences = Counter()
    for tx in txs:
        key = fingerprint(tx)
        tx["fingerprint"] = key
        tx["occurrence"] = 0 if tx.get("transaction_id") else occurrences[key]
        occurrences[key] += 1
    return txs, counts
