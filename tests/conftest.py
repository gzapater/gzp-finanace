import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from gzp_finance.db import Base, make_engine


@pytest.fixture
def engine():
    url = os.getenv("TEST_DATABASE_URL")
    if url:
        root = make_engine(url)
        schema = "qa_" + uuid4().hex
        with root.begin() as c:
            c.execute(text(f'CREATE SCHEMA "{schema}"'))
        db = root.execution_options(schema_translate_map={None: schema})
    else:
        db = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=__import__("sqlalchemy").pool.StaticPool)
    Base.metadata.create_all(db)
    yield db
    if url:
        with root.begin() as c:
            c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        root.dispose()
    else:
        db.dispose()


@pytest.fixture
def session(engine):
    with Session(engine, expire_on_commit=False) as s:
        yield s
        s.rollback()
