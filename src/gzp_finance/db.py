from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (Boolean, Date, DateTime, ForeignKey, Integer, JSON, Numeric,
                        String, UniqueConstraint, create_engine)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def uid() -> str:
    return str(uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


J = JSON().with_variant(JSONB(), "postgresql")


class Import(Base):
    __tablename__ = "imports"
    __table_args__ = (UniqueConstraint("source_bank", "file_hash"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    source_bank: Mapped[str] = mapped_column(String(40))
    filename: Mapped[str] = mapped_column(String(255))
    file_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="complete")
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    counts: Mapped[dict] = mapped_column(J, default=dict)


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (UniqueConstraint("fingerprint", "occurrence"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    import_id: Mapped[str] = mapped_column(ForeignKey("imports.id"))
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    occurrence: Mapped[int] = mapped_column(Integer, default=0)
    source_bank: Mapped[str] = mapped_column(String(40), index=True)
    bank_date: Mapped[datetime] = mapped_column(Date, index=True)
    value_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    amount_eur: Mapped[float] = mapped_column(Numeric(18, 2))
    direction: Mapped[str] = mapped_column(String(10))
    concept_raw: Mapped[str] = mapped_column(String)
    concept_key: Mapped[str] = mapped_column(String, index=True)
    description_raw: Mapped[str] = mapped_column(String, default="")
    bank_category: Mapped[str] = mapped_column(String, default="")
    bank_type: Mapped[str] = mapped_column(String, default="")
    account_ref: Mapped[str] = mapped_column(String, default="")
    counterparty_name: Mapped[str] = mapped_column(String, default="")
    counterparty_iban: Mapped[str] = mapped_column(String, default="")
    payment_reference: Mapped[str] = mapped_column(String, default="")
    mcc: Mapped[str] = mapped_column(String, default="")
    transaction_id: Mapped[str] = mapped_column(String, default="")
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    raw_payload: Mapped[dict] = mapped_column(J)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    def context(self) -> dict:
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


class Classification(Base):
    __tablename__ = "classifications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.id"), unique=True)
    values: Mapped[dict] = mapped_column(J, default=dict)
    provenance: Mapped[dict] = mapped_column(J, default=dict)
    confirmed_by_user: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(Integer, default=0)


class TransactionComponent(Base):
    __tablename__ = "transaction_components"
    __table_args__ = (UniqueConstraint("source_bank", "account_ref", "external_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.id"), index=True)
    source_bank: Mapped[str] = mapped_column(String(40))
    account_ref: Mapped[str] = mapped_column(String)
    external_id: Mapped[str] = mapped_column(String)


class Prediction(Base):
    __tablename__ = "predictions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.id"), index=True)
    engine: Mapped[str] = mapped_column(String(10))
    field: Mapped[str] = mapped_column(String(40))
    predicted_value: Mapped[str] = mapped_column(String)
    confidence: Mapped[float] = mapped_column(Numeric(8, 6))
    accepted: Mapped[bool] = mapped_column(Boolean)
    rule_id: Mapped[str | None] = mapped_column(String)
    model_version: Mapped[str | None] = mapped_column(String)
    evidence: Mapped[dict] = mapped_column(J, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Rule(Base):
    __tablename__ = "rules"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String)
    source: Mapped[str] = mapped_column(String(20), index=True)
    priority: Mapped[int] = mapped_column(Integer)
    match: Mapped[dict] = mapped_column(J)
    set_values: Mapped[dict] = mapped_column(J)
    support: Mapped[int] = mapped_column(Integer, default=1)
    historical_consistency: Mapped[float | None] = mapped_column(Numeric(8, 6))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    def spec(self) -> dict:
        return {"id": self.id, "kind": self.kind, "source": self.source,
                "priority": self.priority, "support": self.support,
                "match": self.match, "set": self.set_values}


class TrainingExample(Base):
    __tablename__ = "training_examples"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    transaction_id: Mapped[str | None] = mapped_column(ForeignKey("transactions.id"), index=True)
    classification_id: Mapped[str | None] = mapped_column(ForeignKey("classifications.id"))
    external_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    source: Mapped[str] = mapped_column(String(20))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    input_snapshot: Mapped[dict] = mapped_column(J)
    prediction_snapshot: Mapped[list] = mapped_column(J, default=list)
    final_values: Mapped[dict] = mapped_column(J)
    model_version: Mapped[str | None] = mapped_column(String)
    dataset_split: Mapped[str] = mapped_column(String(20), default="train")
    sample_weight: Mapped[float] = mapped_column(Numeric(4, 2), default=1)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ModelVersion(Base):
    __tablename__ = "model_versions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    training_example_count: Mapped[int] = mapped_column(Integer)
    metrics: Mapped[dict] = mapped_column(J)
    artifact_path: Mapped[str] = mapped_column(String)
    active: Mapped[bool] = mapped_column(Boolean, default=False)


class HistoricalRecord(Base):
    """Original Excel labels are a reference, never a second cash movement."""
    __tablename__ = "historical_records"
    __table_args__ = (UniqueConstraint("source_hash", "source_row"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    source_hash: Mapped[str] = mapped_column(String(64))
    source_file: Mapped[str] = mapped_column(String(255))
    source_sheet: Mapped[str] = mapped_column(String(100))
    source_row: Mapped[int] = mapped_column(Integer)
    source_bank: Mapped[str] = mapped_column(String(40), index=True)
    manual_date: Mapped[datetime] = mapped_column(Date)
    amount_eur: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    values: Mapped[dict] = mapped_column(J)
    bank_input: Mapped[dict] = mapped_column(J, default=dict)
    match_confidence: Mapped[str] = mapped_column(String(20), default="")
    linkage_status: Mapped[str] = mapped_column(String(20), default="not_in_dataset")
    transaction_id: Mapped[str | None] = mapped_column(ForeignKey("transactions.id"), index=True)


def make_engine(url: str | None = None):
    url = url or os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("Configura DATABASE_URL en un archivo privado o en el entorno")
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return create_engine(url, pool_pre_ping=True, hide_parameters=True)


@contextmanager
def session_scope(engine):
    with sessionmaker(engine, expire_on_commit=False)() as session:
        with session.begin():
            yield session
