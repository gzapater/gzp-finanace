from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select

from .db import ModelVersion, Transaction, make_engine, session_scope
from .ml import activate_model, active_bundle, train_batch
from .history import load_reference, load_workbook
from .service import classify, import_rules, import_statement, seed_history


def main():
    parser = argparse.ArgumentParser(description="Operaciones privadas de la V0")
    sub = parser.add_subparsers(dest="command", required=True)
    rules = sub.add_parser("load-rules")
    rules.add_argument("path", type=Path)
    rules.add_argument("--source", choices=["learned", "user_confirmed", "structural"], required=True)
    history = sub.add_parser("seed-history")
    history.add_argument("path", type=Path)
    reference = sub.add_parser("load-history-reference")
    reference.add_argument("path", type=Path)
    reference.add_argument("--dataset", type=Path, required=True)
    workbook = sub.add_parser("load-history-workbook")
    workbook.add_argument("path", type=Path)
    imp = sub.add_parser("import")
    imp.add_argument("bank")
    imp.add_argument("path", type=Path)
    imp.add_argument("--month", default="", help="Importa únicamente AAAA-MM del extracto completo")
    train = sub.add_parser("train")
    train.add_argument("--output", type=Path, default=Path("data/private/models"))
    activate = sub.add_parser("activate-model")
    activate.add_argument("version")
    sub.add_parser("models")
    sub.add_parser("reclassify")
    args = parser.parse_args()
    with session_scope(make_engine()) as session:
        if args.command == "load-history-reference":
            result = load_reference(session, args.path, args.dataset)
        elif args.command == "load-history-workbook":
            result = load_workbook(session, args.path)
        elif args.command == "load-rules":
            result = {"loaded": import_rules(session, args.path, args.source)}
        elif args.command == "seed-history":
            result = seed_history(session, args.path)
        elif args.command == "import":
            result = import_statement(session, args.bank, args.path.name, args.path.read_bytes(),
                                      models=active_bundle(session), month=args.month)
        elif args.command == "train":
            model = train_batch(session, args.output)
            result = {"version": model.id, "metrics": model.metrics, "active": False}
        elif args.command == "activate-model":
            activate_model(session, args.version)
            result = {"active": args.version}
        elif args.command == "reclassify":
            models, count = active_bundle(session), 0
            for tx in session.scalars(select(Transaction)):
                current = classify(session, tx, models=models)
                if not current.confirmed_by_user:
                    count += 1
            result = {"reclassified": count}
        else:
            result = [{"version": m.id, "active": m.active, "metrics": m.metrics}
                      for m in session.scalars(select(ModelVersion).order_by(ModelVersion.created_at.desc()))]
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
