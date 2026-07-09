from __future__ import annotations

import os

from scheduled_timer_poc import main as scheduled_timer_main


def main() -> None:
    os.environ.setdefault("M8FLOW_SCHEDULER_EXECUTION_MODE", "external")
    scheduled_timer_main()


if __name__ == "__main__":
    main()
