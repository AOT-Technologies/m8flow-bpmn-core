#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]

# Top-level sdist entries the build backend adds on its own.
BACKEND_ENTRIES = {"pyproject.toml", "PKG-INFO", ".gitignore"}


def _publish_sdist_allowlist() -> set[str]:
    workflow = (ROOT / ".github" / "workflows" / "publish.yml").read_text()
    match = re.search(r"^\s*allowed = (\{.*?\})", workflow, re.MULTILINE | re.DOTALL)
    assert match, "publish.yml no longer declares the sdist allowlist"
    allowed = ast.literal_eval(match.group(1))
    assert isinstance(allowed, set)
    return allowed


def test_publish_sdist_allowlist_matches_pyproject_only_include() -> None:
    # The publish workflow rejects any sdist entry outside its allowlist, so a
    # file added to only-include without updating the workflow blocks release.
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    only_include = set(
        pyproject["tool"]["hatch"]["build"]["targets"]["sdist"]["only-include"]
    )

    assert _publish_sdist_allowlist() == only_include | BACKEND_ENTRIES
