#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#

"""Protect the service dependency boundary, including deferred imports."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _imports(path: Path) -> set[str]:
    result: set[str] = set()

    class Imports(ast.NodeVisitor):
        def visit_If(self, node: ast.If) -> None:
            # Type-only re-exports do not execute at import time.
            test = node.test
            type_only = (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
                isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
            )
            for child in node.orelse if type_only else [*node.body, *node.orelse]:
                self.visit(child)

        def visit_Import(self, node: ast.Import) -> None:
            result.update(alias.name for alias in node.names)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            if node.module:
                result.add(node.module)
                result.update(f"{node.module}.{alias.name}" for alias in node.names)

    Imports().visit(ast.parse(path.read_text(encoding="utf-8")))
    return result


def test_core_import_dependencies_are_acyclic() -> None:
    package = ROOT / "src"
    paths = {}
    for path in (package / "m8flow_core").rglob("*.py"):
        parts = path.relative_to(package).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        paths[".".join(parts)] = path
    graph = {name: _imports(path) & paths.keys() for name, path in paths.items()}
    visited: set[str] = set()

    def visit(name: str, chain: tuple[str, ...]) -> None:
        assert name not in chain, "Import cycle: " + " -> ".join((*chain, name))
        if name in visited:
            return
        for target in graph[name]:
            visit(target, (*chain, name))
        visited.add(name)

    for name in graph:
        visit(name, ())


@pytest.mark.parametrize("reverse", [False, True])
def test_service_modules_import_in_fresh_processes(reverse: bool) -> None:
    names = [
        "signal_runtime",
        "workflow_engine",
        "workflow_state",
        "signal_registry",
        "process_instances",
        "workflow_runtime",
        "signals",
        "process_instance_reactivation",
    ]
    if reverse:
        names.reverse()
    modules = [f"m8flow_core.services.{name}" for name in names]
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import importlib; "
            f"[importlib.import_module(name) for name in {modules!r}]",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
