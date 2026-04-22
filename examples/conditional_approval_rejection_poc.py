from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from examples import conditional_approval_poc as approval_poc  # noqa: E402

approval_poc.SCENARIO_AMOUNT = 1_500
approval_poc.MANAGER_DECISION = "Rejected"


def main() -> None:
    print("m8flow-bpmn-core conditional-approval rejection example")
    print(
        "This variant follows the manager rejection path, so the workflow "
        "should stop before the Finance lane."
    )
    approval_poc.main()


if __name__ == "__main__":
    main()
