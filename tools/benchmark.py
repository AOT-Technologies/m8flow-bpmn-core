#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#

"""Measure BPMN import and workflow execution time through the public API.

    uv run python tools/benchmark.py

Each iteration uses a fresh SQLite database file in a temporary directory, so
commits are included. Schema creation and seeding are not timed. Three
operations are timed:

- ``import_definition``: parse, validate, and store the BPMN definition.
- ``start_instance``: create a process instance and run it to the first task.
- ``full_workflow``: start an instance and claim and complete every human task
  of the parallel-review workflow until it reaches ``complete``.

The output is a Markdown table that can be pasted into
``docs/reference/performance-baseline.md``.
"""

from __future__ import annotations

import argparse
import platform
import statistics
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from smoke_test import PROCESS_ID, import_definition, run_workflow, seed

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BPMN = REPO_ROOT / "tests" / "fixtures" / "parallel_review_poc.bpmn"
TENANT_ID = "bench"


def _fresh_session(directory: str):
    from sqlalchemy.orm import Session

    from m8flow_core.db import build_engine, create_schema

    engine = build_engine(f"sqlite+pysqlite:///{Path(directory) / 'bench.db'}")
    create_schema(engine)
    session = Session(bind=engine, autoflush=False, expire_on_commit=False)
    users = seed(session, TENANT_ID)
    session.commit()
    return engine, session, users


def _time_import(directory: str, bpmn_xml: str) -> float:
    engine, session, users = _fresh_session(directory)
    try:
        start = time.perf_counter()
        import_definition(session, TENANT_ID, users["admin"].id, bpmn_xml)
        session.commit()
        return time.perf_counter() - start
    finally:
        session.close()
        engine.dispose()


def _time_start(directory: str, bpmn_xml: str) -> float:
    from m8flow_core import api

    engine, session, users = _fresh_session(directory)
    try:
        definition = import_definition(session, TENANT_ID, users["admin"].id, bpmn_xml)
        session.commit()
        start = time.perf_counter()
        api.execute_command(
            session,
            api.InitializeProcessInstanceFromDefinitionCommand(
                tenant_id=TENANT_ID,
                bpmn_process_definition_id=definition.id,
                process_initiator_id=users["requester"].id,
                summary="benchmark",
                process_version=1,
                started_at=datetime.now(UTC),
                bpmn_process_id=PROCESS_ID,
            ),
        )
        session.commit()
        return time.perf_counter() - start
    finally:
        session.close()
        engine.dispose()


def _time_full_workflow(directory: str, bpmn_xml: str) -> float:
    engine, session, users = _fresh_session(directory)
    try:
        definition = import_definition(session, TENANT_ID, users["admin"].id, bpmn_xml)
        session.commit()
        start = time.perf_counter()
        result = run_workflow(session, TENANT_ID, users, definition.id)
        session.commit()
        elapsed = time.perf_counter() - start
        if result.status != "complete":
            raise RuntimeError(f"workflow ended in status {result.status!r}")
        return elapsed
    finally:
        session.close()
        engine.dispose()


def _measure(
    operation: Callable[[str, str], float], bpmn_xml: str, iterations: int, warmup: int
) -> list[float]:
    def run_once() -> float:
        with tempfile.TemporaryDirectory() as directory:
            return operation(directory, bpmn_xml)

    for _ in range(warmup):
        run_once()
    return [run_once() for _ in range(iterations)]


def _row(name: str, samples: list[float]) -> str:
    ms = sorted(s * 1000 for s in samples)
    p95 = ms[max(0, round(0.95 * len(ms)) - 1)]
    return (
        f"| `{name}` | {len(ms)} | {statistics.median(ms):.1f} | "
        f"{statistics.mean(ms):.1f} | {p95:.1f} | {ms[0]:.1f} | {ms[-1]:.1f} |"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bpmn", type=Path, default=DEFAULT_BPMN)
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=3)
    args = parser.parse_args(argv)

    import m8flow_core

    bpmn_xml = args.bpmn.read_text("utf-8")
    operations = {
        "import_definition": _time_import,
        "start_instance": _time_start,
        "full_workflow": _time_full_workflow,
    }

    print(f"m8flow-core {m8flow_core.__version__}")
    print(f"Python {platform.python_version()} on {platform.platform()}")
    print(f"Processor: {platform.machine()}")
    print(f"Fixture: {args.bpmn.name}, warmup {args.warmup}")
    print()
    columns = ["Operation", "Runs", "Median", "Mean", "p95", "Min", "Max"]
    print(
        "| "
        + " | ".join(c if i < 2 else f"{c} (ms)" for i, c in enumerate(columns))
        + " |"
    )
    print("|---|---|---|---|---|---|---|")
    for name, operation in operations.items():
        print(_row(name, _measure(operation, bpmn_xml, args.iterations, args.warmup)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
