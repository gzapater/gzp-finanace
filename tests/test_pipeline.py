import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from gzp_finance.db import Classification, Import, Prediction, Rule, TrainingExample, Transaction
from gzp_finance.service import (classify, confirm, import_rules, import_statement,
                                 rule_candidates, seed_history)
from test_importers import MI, TR, TR_HEADER


def imported(session, data=MI):
    import_statement(session, "MyInvestor", "synthetic.csv", data.encode())
    return session.scalar(select(Transaction))


def classification(session, tx):
    return session.scalar(select(Classification).where(Classification.transaction_id == tx.id))


def test_repeated_and_overlapping_statements_are_idempotent(session):
    data = MI + MI.splitlines()[1] + "\n"
    assert import_statement(session, "MyInvestor", "a.csv", data.encode())["inserted"] == 2
    assert import_statement(session, "MyInvestor", "renamed.csv", data.encode())["same_file"]
    assert import_statement(session, "MyInvestor", "b.csv", (data + "\n").encode())["duplicates"] == 2
    assert session.scalar(select(func.count()).select_from(Transaction)) == 2


def test_partial_fill_overlap_is_rejected(session):
    import_statement(session, "Trade Republic", "full.csv", TR.encode())
    with pytest.raises(ValueError, match="incompleta"):
        import_statement(session, "Trade Republic", "partial.csv", (TR_HEADER + TR.splitlines()[1] + "\n").encode())


def test_rules_protect_even_blank_values_and_ml_is_not_training(session):
    session.add(Rule(id="locked", kind="confirmed", source="user_confirmed", priority=1000,
                     match={"source_bank": "MyInvestor"},
                     set_values={"target_categoria_general": "Investment", "target_detalle": ""}))
    session.flush()
    class Models:
        version = "test-model"
        def predict(self, tx, resolved):
            assert {"target_categoria_general", "target_detalle"} <= resolved
            return {"target_categoria_general": {"value": "Wrong", "confidence": 1, "accepted": True},
                    "target_detalle": {"value": "Wrong", "confidence": 1, "accepted": True},
                    "target_subtipo": {"value": "Fund", "confidence": .95, "accepted": True},
                    "target_activo": {"value": "Guess", "confidence": .4, "accepted": False}}
    import_statement(session, "MyInvestor", "a.csv", MI.encode(), models=Models())
    tx = session.scalar(select(Transaction)); c = classification(session, tx)
    assert c.values == {"target_categoria_general": "Investment", "target_detalle": "", "target_subtipo": "Fund"}
    assert c.provenance["target_subtipo"]["engine"] == "ml"
    assert session.scalar(select(func.count()).select_from(TrainingExample)) == 0
    assert session.scalar(select(Prediction).where(Prediction.field == "target_activo")).accepted is False


def test_confirmation_audit_revision_and_opt_out(session):
    tx = imported(session); c = classification(session, tx)
    revision = c.revision
    result = confirm(session, tx.id, {"target_categoria_general": "A"}, training=True, revision=revision)
    with pytest.raises(ValueError, match="cambiado"):
        confirm(session, tx.id, {}, training=True, revision=revision)
    confirm(session, tx.id, {"target_categoria_general": "B"}, training=False, revision=result["revision"])
    examples = list(session.scalars(select(TrainingExample).order_by(TrainingExample.added_at)))
    assert [e.final_values["target_categoria_general"] for e in examples] == ["A", "B"]
    assert not any(e.enabled for e in examples)
    classify(session, tx)
    assert c.values["target_categoria_general"] == "B"


def test_one_confirmation_creates_explicit_rule(session):
    tx = imported(session); c = classification(session, tx)
    result = confirm(session, tx.id, {"target_activo": "Example security"}, training=False,
                     revision=c.revision, rule_mode="concept_amount")
    rule = session.get(Rule, result["rule_id"])
    assert rule.source == "user_confirmed" and rule.support == 1
    assert rule.match["amount_cents"] == "-129987"
    assert rule_candidates(session) == []


def test_custom_rule_must_match_current_input(session):
    tx = imported(session); c = classification(session, tx)
    with pytest.raises(ValueError, match="no coincide"):
        confirm(session, tx.id, {"target_activo": "Example"}, training=False, revision=c.revision,
                rule_mode="custom", custom_match={"source_bank": "CaixaBank", "concept_key": "other"})


def test_candidates_use_only_confirmed_enabled_consistent_fields(session):
    for i in range(3):
        session.add(TrainingExample(source="user_confirmed", enabled=True,
            input_snapshot={"source_bank": "MyInvestor", "concept_raw": "Example fund", "bank_date": f"2026-04-0{i+1}", "amount_eur": "-100"},
            final_values={"target_categoria_general": "Investment", "target_detalle": str(i)}))
    session.flush()
    candidate = rule_candidates(session)[0]
    assert candidate["support"] == 3
    assert candidate["set"] == {"target_categoria_general": "Investment"}


def test_private_rule_loading_preserves_priority_and_list_format(session, tmp_path):
    path = tmp_path / "rules.json"
    path.write_text(json.dumps([{"id": "r", "kind": "user_confirmed", "priority": 1100,
                                "match": {"source_bank": "MyInvestor", "concept_key": "example"},
                                "set": {"target_activo": "Example"}}]))
    import_rules(session, path, "user_confirmed")
    assert session.get(Rule, "r").priority == 1100


def test_history_seed_is_private_idempotent_and_high_confidence_only(session, tmp_path):
    path = tmp_path / "history.csv"
    path.write_text("record_id,source_bank,bank_date,match_confidence,manual_date,model_text,target_subtipo\n"
                    "1,MyInvestor,2026-04-01,Alta,not_a_feature,label_leak,Fund\n"
                    "2,MyInvestor,2026-04-02,Media,not_a_feature,label_leak,Fund\n")
    assert seed_history(session, path) == {"inserted": 1, "skipped": 1}
    assert seed_history(session, path)["inserted"] == 0
    e = session.scalar(select(TrainingExample))
    assert "manual_date" not in e.input_snapshot and "model_text" not in e.input_snapshot
