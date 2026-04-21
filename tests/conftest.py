from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from m8flow_bpmn_core.db import build_engine, build_session_factory, create_schema
from m8flow_bpmn_core.models import Base


@pytest.fixture()
def engine(tmp_path: Path):
    database_url = os.getenv("M8FLOW_TEST_DATABASE_URL")
    if database_url is None:
        db_path = tmp_path / "test.db"
        database_url = f"sqlite+pysqlite:///{db_path}"

    engine = build_engine(database_url)
    create_schema(engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture()
def session(engine) -> Session:
    session_factory = build_session_factory(engine)
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
