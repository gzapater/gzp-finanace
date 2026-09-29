from __future__ import annotations

import math
from pathlib import Path

import joblib
from sklearn.feature_extraction import DictVectorizer
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import FeatureUnion, Pipeline
from sklearn.preprocessing import FunctionTransformer
from sqlalchemy import select, update
from threadpoolctl import threadpool_limits

from .db import ModelVersion, TrainingExample, uid
from .domain import TARGETS


def text_features(rows):
    # Construct from bank inputs only; never consume model_text or manual target columns.
    return [" ".join(str(row.get(k) or "") for k in (
        "concept_raw", "description_raw", "bank_category", "bank_type", "mcc")) or "desconocido"
        for row in rows]


def bank_features(rows):
    out = []
    for row in rows:
        amount = float(row.get("amount_eur") or 0)
        features = {k: str(row.get(k) or "") for k in (
            "source_bank", "bank_type", "bank_category", "direction", "mcc")}
        features["amount_bucket"] = str(int(math.log1p(abs(amount))))
        features["log_amount"] = math.log1p(abs(amount))
        out.append(features)
    return out


def estimator():
    return Pipeline([
        ("features", FeatureUnion([
            ("word", Pipeline([("text", FunctionTransformer(text_features)),
                               ("tfidf", TfidfVectorizer(ngram_range=(1, 2), max_features=12000))])),
            ("char", Pipeline([("text", FunctionTransformer(text_features)),
                               ("tfidf", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), max_features=20000))])),
            ("bank", Pipeline([("fields", FunctionTransformer(bank_features)),
                               ("vector", DictVectorizer())])),
        ])),
        ("classifier", LogisticRegression(max_iter=1000, C=4, random_state=42)),
    ])


class ModelBundle:
    def __init__(self, version: str, models: dict, thresholds: dict):
        self.version, self.models, self.thresholds = version, models, thresholds

    @threadpool_limits.wrap(limits=1)
    def predict(self, tx: dict, resolved: set[str]) -> dict:
        result = {}
        for field, model in self.models.items():
            if field in resolved:
                continue
            probabilities = model.predict_proba([tx])[0]
            index = int(probabilities.argmax())
            confidence = float(probabilities[index])
            result[field] = {"value": str(model.classes_[index]), "confidence": confidence,
                             "accepted": confidence >= self.thresholds[field]}
        return result


@threadpool_limits.wrap(limits=1)
def fit_baseline(examples: list, version: str) -> tuple[ModelBundle, dict]:
    # Freeze May-Sep 2026 as forward holdout, even if marked train.
    train, validation, forward = [], [], []
    for e in examples:
        day = str(e.input_snapshot.get("bank_date") or "")[:10]
        if not day:
            continue
        if "2026-05-01" <= day < "2026-10-01":
            forward.append(e)
        elif e.dataset_split == "validation":
            validation.append(e)
        else:
            train.append(e)
    if len(train) < 10:
        raise ValueError("Se necesitan al menos diez ejemplos históricos habilitados anteriores a mayo de 2026")
    models, thresholds, metrics = {}, {}, {}
    for field in TARGETS:
        t = [e for e in train if e.final_values.get(field)]
        v = [e for e in validation if e.final_values.get(field)]
        if len(t) < 6 or len({e.final_values[field] for e in t}) < 2:
            metrics[field] = {"status": "insufficient_labels", "train_count": len(t)}
            continue
        model = estimator()
        model.fit([e.input_snapshot for e in t], [e.final_values[field] for e in t],
                  classifier__sample_weight=[float(e.sample_weight) for e in t])
        models[field] = model
        # No trusted validation evidence => suggestions only, never automatic filling.
        thresholds[field] = 1.01
        stats = {"train_count": len(t), "validation_count": len(v), "threshold": None,
                 "accepted_count": 0, "accepted_accuracy": None, "accuracy": None}
        if v:
            probs = model.predict_proba([e.input_snapshot for e in v])
            indexes, confs = probs.argmax(axis=1), probs.max(axis=1)
            predicted = model.classes_[indexes]
            truth = [e.final_values[field] for e in v]
            correct = [p == expected for p, expected in zip(predicted, truth)]
            stats["accuracy"] = sum(correct) / len(v)
            for threshold in (0.85, 0.90, 0.95, 0.98):
                accepted = [ok for ok, c in zip(correct, confs) if c >= threshold]
                if len(accepted) >= 5 and sum(accepted) / len(accepted) >= 0.95:
                    thresholds[field] = threshold
                    stats.update(threshold=threshold, accepted_count=len(accepted),
                                 accepted_accuracy=sum(accepted) / len(accepted))
                    break
        metrics[field] = stats
    if not models:
        raise ValueError("No hay campos con al menos dos clases y seis ejemplos")
    return ModelBundle(version, models, thresholds), {
        "train_count": len(train), "validation_count": len(validation), "forward_holdout_count": len(forward),
        "evaluation": "ML only; historical validation is used for threshold selection, not an independent test",
        "fields": metrics,
    }


def train_batch(session, directory: Path) -> ModelVersion:
    examples = list(session.scalars(select(TrainingExample).where(TrainingExample.enabled.is_(True))))
    version = uid()
    bundle, metrics = fit_baseline(examples, version)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = (directory / f"{version}.joblib").resolve()
    joblib.dump(bundle, path)
    path.chmod(0o600)
    record = ModelVersion(id=version, training_example_count=metrics["train_count"],
                          metrics=metrics, artifact_path=str(path), active=False)
    session.add(record)
    session.flush()
    return record


def activate_model(session, version: str) -> None:
    record = session.get(ModelVersion, version)
    if not record or not Path(record.artifact_path).is_file():
        raise ValueError("Modelo o artefacto no encontrado")
    session.execute(update(ModelVersion).values(active=False))
    record.active = True


def active_bundle(session) -> ModelBundle | None:
    record = session.scalar(select(ModelVersion).where(ModelVersion.active.is_(True)))
    if not record:
        return None
    # Artifacts come only from this app's explicit offline training, never from uploads.
    return joblib.load(record.artifact_path)
