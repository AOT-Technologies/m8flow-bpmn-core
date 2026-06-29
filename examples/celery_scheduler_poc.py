from __future__ import annotations

import os
import socket
from dataclasses import asdict, dataclass
from pprint import pformat

from celery import Celery
from sqlalchemy.engine import Engine

from m8flow_bpmn_core import api
from m8flow_bpmn_core.db import build_engine

DEFAULT_BROKER_URL = "redis://localhost:6848/0"
DEFAULT_QUEUE_NAME = "m8flow-bpmn-core-poc"
DEFAULT_POLL_SECONDS = 1.0
DEFAULT_LOGICAL_WORKER_ID_PREFIX = "m8flow-bpmn-core-celery"
POLL_TASK_NAME = "m8flow_bpmn_core.poc.poll_due_scheduler_jobs"
DEFAULT_SHARED_DATABASE_URL = (
    "postgresql+psycopg://postgres:postgres@localhost:6843/postgres"
    "?connect_timeout=1"
)


@dataclass(frozen=True, slots=True)
class CelerySchedulerPocConfig:
    broker_url: str
    result_backend: str
    database_url: str
    queue_name: str
    poll_seconds: float
    tenant_id: str | None
    worker_id: str


def _env_first(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return None


def _resolve_poll_seconds() -> float:
    raw_value = os.getenv("M8FLOW_BPMN_CORE_CELERY_POLL_SECONDS", "").strip()
    if not raw_value:
        return DEFAULT_POLL_SECONDS
    try:
        poll_seconds = float(raw_value)
    except ValueError as exc:
        raise RuntimeError(
            "M8FLOW_BPMN_CORE_CELERY_POLL_SECONDS must be a number"
        ) from exc
    if poll_seconds <= 0:
        raise RuntimeError(
            "M8FLOW_BPMN_CORE_CELERY_POLL_SECONDS must be greater than zero"
        )
    return poll_seconds


def resolve_config() -> CelerySchedulerPocConfig:
    broker_url = _env_first(
        "M8FLOW_BPMN_CORE_CELERY_BROKER_URL",
        "M8FLOW_BACKEND_CELERY_BROKER_URL",
    ) or DEFAULT_BROKER_URL
    result_backend = _env_first(
        "M8FLOW_BPMN_CORE_CELERY_RESULT_BACKEND",
        "M8FLOW_BACKEND_CELERY_RESULT_BACKEND",
    ) or broker_url
    database_url = _env_first(
        "M8FLOW_BPMN_CORE_CELERY_DATABASE_URL",
        "M8FLOW_EXAMPLE_DATABASE_URL",
        "M8FLOW_DATABASE_URL",
    ) or DEFAULT_SHARED_DATABASE_URL
    queue_name = (
        os.getenv("M8FLOW_BPMN_CORE_CELERY_QUEUE", "").strip()
        or DEFAULT_QUEUE_NAME
    )
    tenant_id = os.getenv("M8FLOW_BPMN_CORE_CELERY_TENANT_ID", "").strip() or None
    worker_id_prefix = (
        os.getenv("M8FLOW_BPMN_CORE_CELERY_WORKER_ID", "").strip()
        or DEFAULT_LOGICAL_WORKER_ID_PREFIX
    )
    hostname = socket.gethostname().strip() or "unknown-host"
    return CelerySchedulerPocConfig(
        broker_url=broker_url,
        result_backend=result_backend,
        database_url=database_url,
        queue_name=queue_name,
        poll_seconds=_resolve_poll_seconds(),
        tenant_id=tenant_id,
        worker_id=f"{worker_id_prefix}@{hostname}",
    )


CONFIG = resolve_config()
_ENGINE: Engine | None = None

celery_app = Celery(
    "m8flow_bpmn_core_poc",
    broker=CONFIG.broker_url,
    backend=CONFIG.result_backend,
)
celery_app.conf.update(
    broker_connection_retry_on_startup=True,
    enable_utc=True,
    timezone="UTC",
    task_default_queue=CONFIG.queue_name,
    task_default_exchange=CONFIG.queue_name,
    task_default_routing_key=CONFIG.queue_name,
    task_routes={
        POLL_TASK_NAME: {
            "queue": CONFIG.queue_name,
            "routing_key": CONFIG.queue_name,
        }
    },
    beat_schedule={
        "poll-due-scheduler-jobs": {
            "task": POLL_TASK_NAME,
            "schedule": CONFIG.poll_seconds,
            "options": {
                "queue": CONFIG.queue_name,
                "routing_key": CONFIG.queue_name,
            },
        }
    },
)


def _engine_for_worker() -> Engine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = build_engine(CONFIG.database_url)
    return _ENGINE


@celery_app.task(name=POLL_TASK_NAME)
def poll_due_scheduler_jobs() -> dict[str, object]:
    with _engine_for_worker().begin() as connection:
        processed = api.run_due_scheduler_jobs(
            connection,
            worker_id=CONFIG.worker_id,
            tenant_id=CONFIG.tenant_id,
        )
    return {
        "processed_count": processed,
        "tenant_id": CONFIG.tenant_id,
        "worker_id": CONFIG.worker_id,
        "queue_name": CONFIG.queue_name,
    }


def main() -> None:
    print("m8flow-bpmn-core Celery scheduler POC")
    print(
        "This module defines a dedicated Celery app that periodically calls "
        "`api.run_due_scheduler_jobs(...)` against the configured database."
    )
    print("Resolved config:")
    print(pformat(asdict(CONFIG), sort_dicts=False, width=100))
    print()
    print("Recommended beat command:")
    print(
        "celery -A examples.celery_scheduler_poc:celery_app beat "
        "--loglevel info"
    )
    print()
    print("Recommended worker command:")
    print(
        "celery -A examples.celery_scheduler_poc:celery_app worker "
        f"--pool solo --loglevel info -Q {CONFIG.queue_name}"
    )


if __name__ == "__main__":
    main()
