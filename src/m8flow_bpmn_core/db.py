from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from m8flow_bpmn_core.models import Base
from m8flow_bpmn_core.settings import get_settings


def build_engine(database_url: str | None = None, echo: bool | None = None) -> Engine:
    settings = get_settings()
    return create_engine(
        database_url or settings.database_url,
        echo=settings.database_echo if echo is None else echo,
    )


def build_session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    engine = engine or build_engine()
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def create_schema(engine: Engine | None = None) -> None:
    engine = engine or build_engine()
    Base.metadata.create_all(bind=engine)


@contextmanager
def session_scope(engine: Engine | None = None) -> Iterator[Session]:
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
