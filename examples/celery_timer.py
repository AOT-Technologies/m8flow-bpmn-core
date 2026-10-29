from __future__ import annotations

import os

from conditional_approval import _reject_sqlite_flag
from scheduled_timer import main as scheduled_timer_main


def main() -> None:
    _reject_sqlite_flag(
        "the Celery worker runs in another process and needs a shared "
        "PostgreSQL database."
    )
    os.environ.setdefault("M8FLOW_SCHEDULER_EXECUTION_MODE", "external")
    scheduled_timer_main()


if __name__ == "__main__":
    main()
