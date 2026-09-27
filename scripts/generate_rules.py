#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gzp_finance.rules import normalize_concept  # noqa: E402

TARGET_PREFIX = "target_"

RULE_SPECS = [
    ("concept_bank_type", 100, ("source_bank", "concept_key", "bank_type")),
    ("concept_bank_category", 95, ("source_bank", "concept_key", "bank_category")),
    ("concept_direction", 90, ("source_bank", "concept_key", "direction")),
    ("concept", 80, ("source_bank", "concept_key")),
]


def load_rows(path: Path, allowed_confidence: set[str]) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if allowed_confidence:
        rows = [r for r in rows if r.get("match_confidence", "") in allowed_confidence]
    for row in rows:
        source = row.get("concept_raw") or row.get("description_raw") or ""
        row["concept_key"] = normalize_concept(source)
    return rows


def stable_outputs(rows: list[dict[str, str]], targets: list[str]) -> dict[str, str]:
    outputs: dict[str, str] = {}
    for target in targets:
        values = [row.get(target, "") for row in rows]
        # Conservative by design: every supporting row must contain exactly the same
        # non-empty value before the field becomes deterministic.
        if values and values[0] and len(set(values)) == 1:
            outputs[target] = values[0]
    return outputs


def rule_id(kind: str, match: dict[str, str]) -> str:
    raw = json.dumps([kind, match], sort_keys=True, ensure_ascii=False).encode("utf-8")
    return f"{kind}:{hashlib.sha1(raw).hexdigest()[:12]}"


def generate(rows: list[dict[str, str]], min_support: int) -> list[dict]:
    targets = [name for name in rows[0].keys() if name.startswith(TARGET_PREFIX)] if rows else []
    rules: list[dict] = []

    for kind, priority, fields in RULE_SPECS:
        grouped: dict[tuple[str, ...], list[dict[str, str]]] = defaultdict(list)
        for row in rows:
            key = tuple(row.get(field, "") for field in fields)
            if all(key):
                grouped[key].append(row)

        for key, group in grouped.items():
            if len(group) < min_support:
                continue
            outputs = stable_outputs(group, targets)
            if not outputs:
                continue
            match = dict(zip(fields, key))
            rules.append({
                "id": rule_id(kind, match),
                "kind": kind,
                "priority": priority,
                "support": len(group),
                "historical_consistency": 1.0,
                "match": match,
                "set": outputs,
            })

    rules.sort(key=lambda r: (-r["priority"], -r["support"], r["id"]))
    return rules


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate conservative deterministic finance rules")
    parser.add_argument("dataset", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--min-support", type=int, default=3)
    parser.add_argument(
        "--confidence",
        action="append",
        default=["Alta"],
        help="Allowed match confidence. Repeat to include more values (default: Alta).",
    )
    args = parser.parse_args()

    rows = load_rows(args.dataset, set(args.confidence))
    rules = generate(rows, args.min_support)
    payload = {
        "schema_version": 1,
        "generation_policy": {
            "min_support": args.min_support,
            "required_historical_consistency": 1.0,
            "allowed_match_confidence": sorted(set(args.confidence)),
            "note": "A rule may fill only some target fields. Missing fields continue to ML/manual review.",
        },
        "stats": {"source_rows": len(rows), "rules": len(rules)},
        "rules": rules,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Generated {len(rules)} rules from {len(rows)} rows -> {args.output}")


if __name__ == "__main__":
    main()
