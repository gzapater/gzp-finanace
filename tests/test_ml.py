from types import SimpleNamespace

from gzp_finance.ml import fit_baseline, text_features


def test_baseline_holdout_protection_and_low_confidence_review():
    examples = []
    for i in range(30):
        label = "Fund" if i % 2 else "Shop"
        examples.append(SimpleNamespace(input_snapshot={"source_bank": "Example", "concept_raw": label,
            "bank_date": "2026-04-01", "amount_eur": str(i)}, final_values={"target_subtipo": label},
            dataset_split="train", sample_weight=1))
    examples.append(SimpleNamespace(input_snapshot={"bank_date": "2026-06-01"}, final_values={"target_subtipo": "Future"},
                                    dataset_split="train", sample_weight=1))
    bundle, metrics = fit_baseline(examples, "synthetic-model")
    assert metrics["train_count"] == 30 and metrics["forward_holdout_count"] == 1
    assert bundle.predict({"concept_raw": "Fund"}, resolved={"target_subtipo"}) == {}
    assert not bundle.predict({"concept_raw": "Fund"}, resolved=set())["target_subtipo"]["accepted"]
    assert text_features([{"concept_raw": "Bank input", "manual_date": "secret", "model_text": "leaked", "target_subtipo": "leaked"}]) == ["Bank input    "]
