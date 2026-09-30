from sqlalchemy import select

from gzp_finance.db import Classification, Rule, TrainingExample
from gzp_finance.service import vocabulary


def test_vocabulary_preserves_partial_labels_and_category_relationships(session):
    session.add(TrainingExample(source="historical", enabled=True,
        input_snapshot={}, final_values={"target_categoria_general": "02. Investment",
                                        "target_subtipo": "02.01. Fund", "target_activo": "Example fund"}))
    session.add(TrainingExample(source="historical", enabled=False,
        input_snapshot={}, final_values={"target_detalle": "Existing partial detail"}))
    session.add(Rule(id="partial", kind="confirmed", source="user_confirmed", priority=1000,
        match={"source_bank": "MyInvestor"}, set_values={"target_tipo_transaccion": "Investment"}))
    session.flush()
    result = vocabulary(session)
    assert result["fields"]["target_activo"] == ["Example fund"]
    assert result["fields"]["target_detalle"] == ["Existing partial detail"]
    assert result["fields"]["target_tipo_transaccion"] == ["Investment"]
    assert result["subtypes"] == {"02. Investment": ["02.01. Fund"]}
