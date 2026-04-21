from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from m8flow_bpmn_core.db import build_engine, create_schema
from m8flow_bpmn_core.models import Base

POSTGRES_IMAGE = os.getenv("M8FLOW_TEST_POSTGRES_IMAGE", "postgres:16")
POSTGRES_USER = os.getenv("M8FLOW_TEST_POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.getenv("M8FLOW_TEST_POSTGRES_PASSWORD", "postgres")
POSTGRES_DB = os.getenv("M8FLOW_TEST_POSTGRES_DB", "m8flow_bpmn_core_test")


@pytest.fixture(scope="session")
def postgres_engine() -> Iterator[Engine]:
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is required for the Postgres integration test")
    pytest.importorskip("psycopg")
    if not _docker_daemon_is_available():
        pytest.skip("Docker daemon is not available")

    container_name = f"m8flow-bpmn-core-postgres-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            container_name,
            "-e",
            f"POSTGRES_USER={POSTGRES_USER}",
            "-e",
            f"POSTGRES_PASSWORD={POSTGRES_PASSWORD}",
            "-e",
            f"POSTGRES_DB={POSTGRES_DB}",
            "-P",
            POSTGRES_IMAGE,
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    engine = None
    try:
        host_port = _read_host_port(container_name, "5432")
        database_url = (
            "postgresql+psycopg://"
            f"{POSTGRES_USER}:{POSTGRES_PASSWORD}@127.0.0.1:{host_port}/{POSTGRES_DB}"
        )
        engine = build_engine(database_url)
        _wait_for_database(engine)
        create_schema(engine)
        yield engine
    finally:
        if engine is not None:
            Base.metadata.drop_all(bind=engine)
            engine.dispose()
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            check=False,
            capture_output=True,
            text=True,
        )


def _docker_daemon_is_available() -> bool:
    try:
        subprocess.run(
            ["docker", "info"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False
    return True


def _read_host_port(container_name: str, container_port: str) -> int:
    result = subprocess.run(
        ["docker", "port", container_name, container_port],
        check=True,
        capture_output=True,
        text=True,
    )
    for line in result.stdout.splitlines():
        match = re.search(r":(\d+)$", line.strip())
        if match is not None:
            return int(match.group(1))
    raise RuntimeError(
        f"Could not determine the mapped host port for container {container_name}"
    )


def _wait_for_database(engine: Engine, timeout_seconds: int = 30) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with engine.connect() as connection:
                connection.execute(text("select 1"))
            return
        except Exception as exc:  # pragma: no cover - transient startup path
            last_error = exc
            time.sleep(1)
    raise RuntimeError(
        "Postgres container did not become ready in time"
    ) from last_error
