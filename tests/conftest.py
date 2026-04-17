from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from m8flow_bpmn_core.db import build_engine, build_session_factory, create_schema


@pytest.fixture()
def engine(tmp_path: Path):
    db_path = tmp_path / "test.db"
    engine = build_engine(f"sqlite+pysqlite:///{db_path}")
    create_schema(engine)
    yield engine
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
