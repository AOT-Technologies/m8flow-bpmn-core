from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from m8flow_sample_app.settings import get_settings


def build_engine(database_url: str | None = None, echo: bool | None = None) -> Engine:
    settings = get_settings()
    return create_engine(
        database_url or settings.database_url,
        echo=settings.database_echo if echo is None else echo,
    )


def build_session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    engine = engine or build_engine()
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


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


def run_migrations(database_url: str | None = None) -> None:
    command.upgrade(build_alembic_config(database_url), "head")


def build_alembic_config(database_url: str | None = None) -> Config:
    project_root = sample_app_root()
    settings = get_settings()
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "alembic"))
    config.set_main_option(
        "sqlalchemy.url",
        database_url or settings.database_url,
    )
    return config


def sample_app_root() -> Path:
    return Path(__file__).resolve().parents[2]
