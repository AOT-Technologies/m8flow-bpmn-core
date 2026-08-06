from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

from sqlalchemy import Engine

from m8flow_bpmn_core import api
from m8flow_bpmn_core.errors import BpmnCoreError
from m8flow_sample_app import service_tasks as sample_app_service_tasks
from m8flow_sample_app.db import build_engine, session_scope

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class SampleAppSchedulerPoller:
    database_url: str | None = None
    poll_seconds: float = 1.0
    batch_limit: int = 100
    worker_id: str = "sample-app-inline-scheduler"
    _stop_event: threading.Event = field(default_factory=threading.Event, init=False)
    _thread: threading.Thread | None = field(default=None, init=False)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run_loop,
            name=self.worker_id,
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=max(self.poll_seconds * 2, 1.0))

    def _run_loop(self) -> None:
        engine = build_engine(self.database_url)
        try:
            while not self._stop_event.is_set():
                try:
                    run_scheduler_cycle(
                        engine=engine,
                        worker_id=self.worker_id,
                        limit=self.batch_limit,
                    )
                except BpmnCoreError:
                    LOGGER.exception(
                        "Sample app scheduler cycle failed with a BPMN-core error."
                    )
                except Exception:
                    LOGGER.exception("Sample app scheduler cycle failed.")
                self._stop_event.wait(self.poll_seconds)
        finally:
            engine.dispose()


def run_scheduler_cycle(
    *,
    database_url: str | None = None,
    engine: Engine | None = None,
    now_in_seconds: int | None = None,
    limit: int = 100,
    worker_id: str = "sample-app-inline-scheduler",
) -> int:
    if engine is None:
        engine = build_engine(database_url)
        owns_engine = True
    else:
        owns_engine = False

    try:
        with session_scope(engine) as db_session:
            with api.service_task_registry_scope(
                sample_app_service_tasks.build_sample_app_service_task_registry
            ):
                return api.run_due_scheduler_jobs(
                    db_session,
                    now_in_seconds=now_in_seconds,
                    limit=limit,
                    worker_id=worker_id,
                )
    finally:
        if owns_engine:
            engine.dispose()
