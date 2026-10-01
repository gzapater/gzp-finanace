from __future__ import annotations

import os
import secrets
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from starlette.concurrency import run_in_threadpool

from .db import Classification, HistoricalRecord, Prediction, Rule, Transaction, make_engine, session_scope
from .history import load_workbook, totals, validation_summary
from .domain import BANKS, SELECT_TARGETS, TARGET_LABELS, TARGETS, snapshot
from .ml import active_bundle
from .service import (accept_candidate, classify, confirm, import_statement,
                      review_status, rule_candidates, vocabulary)


class Confirmation(BaseModel):
    values: dict[str, str]
    training: bool = False
    revision: int
    rule_mode: str = "none"
    custom_match: dict | None = None


class Toggle(BaseModel):
    enabled: bool


def create_app(engine=None, *, username=None, password=None):
    username = username or os.getenv("APP_USER")
    password = password or os.getenv("APP_PASSWORD")
    if not username or not password or len(password) < 16:
        raise RuntimeError("Configura APP_USER y APP_PASSWORD (mínimo 16 caracteres) antes de iniciar")
    engine = engine or make_engine()
    security = HTTPBasic()
    csrf = secrets.token_urlsafe(32)

    def authenticate(credentials: HTTPBasicCredentials = Depends(security)):
        ok_user = secrets.compare_digest(credentials.username.encode(), username.encode())
        ok_password = secrets.compare_digest(credentials.password.encode(), password.encode())
        if not (ok_user and ok_password):
            raise HTTPException(401, "Credenciales incorrectas", headers={"WWW-Authenticate": "Basic"})

    app = FastAPI(title="Finanzas V0", docs_url=None, redoc_url=None, openapi_url=None)
    here = Path(__file__).parent
    templates = Jinja2Templates(directory=here / "templates")
    app.mount("/static", StaticFiles(directory=here / "static"), name="static")

    @app.middleware("http")
    async def protect(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and urlparse(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "Origen no permitido"}, status_code=403)
            if not secrets.compare_digest(request.headers.get("x-csrf-token", ""), csrf):
                return JSONResponse({"detail": "Recarga la página para renovar la sesión"}, status_code=403)
            try:
                length = int(request.headers.get("content-length", "0"))
            except ValueError:
                return JSONResponse({"detail": "Tamaño inválido"}, status_code=400)
            if length > 10 * 1024 * 1024:
                return JSONResponse({"detail": "Máximo 10 MB por extracto"}, status_code=413)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'; form-action 'self'"
        return response

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(LookupError)
    async def missing(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.get("/health")
    def health():
        try:
            with engine.connect() as c:
                c.execute(text("SELECT 1 FROM alembic_version"))
        except Exception:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return {"status": "ok"}

    @app.get("/", dependencies=[Depends(authenticate)])
    def index(request: Request):
        with session_scope(engine) as s:
            history_banks = sorted(set(s.scalars(select(HistoricalRecord.source_bank))))
        display_names = {"CaixaBank": "01. La Caixa", "MyInvestor": "03. MyInvestor",
                         "Trade Republic": "04. Trade Republic"}
        history_banks = sorted(((bank, display_names.get(bank, bank)) for bank in history_banks),
                               key=lambda item: item[1])
        return templates.TemplateResponse(request=request, name="index.html", context={
            "csrf": csrf, "banks": BANKS, "targets": TARGETS,
            "select_targets": SELECT_TARGETS, "target_labels": TARGET_LABELS,
            "history_banks": history_banks})

    @app.get("/api/vocabulary", dependencies=[Depends(authenticate)])
    def existing_labels():
        with session_scope(engine) as s:
            return vocabulary(s)

    @app.post("/api/imports", dependencies=[Depends(authenticate)])
    async def upload(bank: str = Form(...), file: UploadFile = File(...)):
        data = await file.read(10 * 1024 * 1024 + 1)
        await file.close()
        if len(data) > 10 * 1024 * 1024:
            raise HTTPException(413, "Máximo 10 MB por extracto")
        def process():
            with session_scope(engine) as s:
                return import_statement(s, bank, file.filename or "extracto", data, models=active_bundle(s))
        return await run_in_threadpool(process)

    @app.get("/api/transactions", dependencies=[Depends(authenticate)])
    def transactions(bank: str = "", status: str = "", origin: str = "", offset: int = 0, limit: int = 50):
        if offset < 0 or not 1 <= limit <= 100:
            raise HTTPException(422, "Paginación inválida")
        if origin not in {"", "historical", "new"}:
            raise HTTPException(422, "Origen inválido")
        with session_scope(engine) as s:
            has_history = select(HistoricalRecord.id).where(HistoricalRecord.transaction_id == Transaction.id).exists()
            query = select(Transaction, Classification).join(Classification, Classification.transaction_id == Transaction.id)
            if origin:
                query = query.where(has_history if origin == "historical" else ~has_history)
            if bank:
                query = query.where(Transaction.source_bank == bank)
            if status == "pending":
                query = query.where(Classification.confirmed_by_user.is_(False))
            elif status == "confirmed":
                query = query.where(Classification.confirmed_by_user.is_(True))
            total = s.scalar(select(func.count()).select_from(query.subquery()))
            rows = s.execute(query.order_by(Transaction.bank_date.desc(), Transaction.id).offset(offset).limit(limit)).all()
            historical_ids = set(s.scalars(select(HistoricalRecord.transaction_id).where(
                HistoricalRecord.transaction_id.in_([tx.id for tx, c in rows]))))
            return {"total": total, "items": [{
                "id": tx.id, "source_bank": tx.source_bank, "bank_date": str(tx.bank_date),
                "concept_raw": tx.concept_raw, "amount_eur": str(tx.amount_eur),
                "values": c.values, "provenance": c.provenance, "revision": c.revision,
                "status": review_status(c), "confirmed": c.confirmed_by_user,
                "origin": "historical" if tx.id in historical_ids else "new",
            } for tx, c in rows]}

    @app.get("/api/history", dependencies=[Depends(authenticate)])
    def history(bank: str = "", offset: int = 0, limit: int = 50):
        if offset < 0 or not 1 <= limit <= 100:
            raise HTTPException(422, "Paginación inválida")
        with session_scope(engine) as s:
            query = select(HistoricalRecord)
            if bank:
                query = query.where(HistoricalRecord.source_bank == bank)
            total = s.scalar(select(func.count()).select_from(query.subquery()))
            all_refs = list(s.scalars(query))
            amounts = totals(r.amount_eur if r.excel_amount_is_numeric else None for r in all_refs)
            refs = s.scalars(query.order_by(HistoricalRecord.manual_date.desc(), HistoricalRecord.source_row)
                .offset(offset).limit(limit))
            return {"total": total, "totals": amounts, "items": [{"id": r.id, "source_bank": r.source_bank,
                "date": str(r.manual_date), "amount_eur": str(r.amount_eur) if r.amount_eur is not None else None,
                "values": r.values, "source_file": r.source_file, "source_sheet": r.source_sheet,
                "source_row": r.source_row, "match_confidence": r.match_confidence,
                "linkage_status": r.linkage_status, "transaction_id": r.transaction_id} for r in refs]}

    @app.post("/api/history/import", dependencies=[Depends(authenticate)])
    async def upload_history(file: UploadFile = File(...)):
        if not file.filename or not file.filename.lower().endswith(".xlsx"):
            raise HTTPException(422, "Selecciona el Excel histórico .xlsx")
        data = await file.read(10 * 1024 * 1024 + 1)
        await file.close()
        if len(data) > 10 * 1024 * 1024:
            raise HTTPException(413, "Máximo 10 MB por Excel")
        def process():
            with session_scope(engine) as s:
                return load_workbook(s, BytesIO(data), source_name=Path(file.filename).name)
        return await run_in_threadpool(process)

    @app.get("/api/validation", dependencies=[Depends(authenticate)])
    def validation():
        with session_scope(engine) as s:
            return validation_summary(s)

    @app.get("/api/transactions/{transaction_id}", dependencies=[Depends(authenticate)])
    def detail(transaction_id: str):
        with session_scope(engine) as s:
            tx = s.get(Transaction, transaction_id)
            if not tx:
                raise LookupError("Transacción no encontrada")
            c = s.scalar(select(Classification).where(Classification.transaction_id == tx.id))
            ps = list(s.scalars(select(Prediction).where(Prediction.transaction_id == tx.id).order_by(Prediction.created_at)))
            historical = list(s.scalars(select(HistoricalRecord).where(HistoricalRecord.transaction_id == tx.id)))
            return {"transaction": snapshot(tx.context()), "values": c.values,
                    "historical_labels": [{"values": r.values, "source_row": r.source_row,
                                           "match_confidence": r.match_confidence} for r in historical],
                    "provenance": c.provenance, "revision": c.revision, "confirmed": c.confirmed_by_user,
                    "predictions": [{"engine": p.engine, "field": p.field, "value": p.predicted_value,
                                     "confidence": float(p.confidence), "accepted": p.accepted,
                                     "rule_id": p.rule_id, "model_version": p.model_version,
                                     "evidence": p.evidence} for p in ps]}

    @app.post("/api/transactions/{transaction_id}/confirm", dependencies=[Depends(authenticate)])
    def confirmation(transaction_id: str, body: Confirmation):
        with session_scope(engine) as s:
            result = confirm(s, transaction_id, **body.model_dump())
            if result["rule_id"]:
                models = active_bundle(s)
                for tx in s.scalars(select(Transaction)):
                    classify(s, tx, models=models)
            return result

    @app.get("/api/rules", dependencies=[Depends(authenticate)])
    def rules():
        with session_scope(engine) as s:
            return [{**r.spec(), "enabled": r.enabled} for r in s.scalars(select(Rule).order_by(Rule.priority.desc(), Rule.id))]

    @app.post("/api/rules/{rule_id}/enabled", dependencies=[Depends(authenticate)])
    def toggle(rule_id: str, body: Toggle):
        with session_scope(engine) as s:
            rule = s.get(Rule, rule_id)
            if not rule:
                raise LookupError("Regla no encontrada")
            rule.enabled = body.enabled
            s.flush()
            models = active_bundle(s)
            for tx in s.scalars(select(Transaction)):
                classify(s, tx, models=models)
            return {"enabled": rule.enabled}

    @app.get("/api/candidates", dependencies=[Depends(authenticate)])
    def candidates():
        with session_scope(engine) as s:
            return rule_candidates(s)

    @app.post("/api/candidates/accept", dependencies=[Depends(authenticate)])
    def accept(body: dict):
        with session_scope(engine) as s:
            rule_id = accept_candidate(s, body)
            s.flush()
            models = active_bundle(s)
            for tx in s.scalars(select(Transaction)):
                classify(s, tx, models=models)
            return {"rule_id": rule_id}

    @app.post("/api/reclassify", dependencies=[Depends(authenticate)])
    def reclassify(bank: str = ""):
        if bank and bank not in BANKS:
            raise HTTPException(422, "Banco inválido")
        with session_scope(engine) as s:
            models, count, changed = active_bundle(s), 0, 0
            query = select(Transaction, Classification).join(
                Classification, Classification.transaction_id == Transaction.id
            ).where(Classification.confirmed_by_user.is_(False))
            if bank:
                query = query.where(Transaction.source_bank == bank)
            for tx, current in s.execute(query).all():
                revision = current.revision
                updated = classify(s, tx, models=models)
                count += 1
                changed += updated.revision != revision
            return {"reclassified": count, "changed": changed}

    return app
