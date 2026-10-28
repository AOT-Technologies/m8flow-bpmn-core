from __future__ import annotations

import ast
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_version_fallback_matches_pyproject_version() -> None:
    # The fallback is reported when the package runs from a source checkout
    # without installed distribution metadata.
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    declared = pyproject["project"]["version"]
    module = ast.parse((ROOT / "src" / "m8flow_core" / "__init__.py").read_text())
    fallbacks = [
        node.value.value
        for node in ast.walk(module)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets)
        and isinstance(node.value, ast.Constant)
    ]

    assert fallbacks == [declared]
