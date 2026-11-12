#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "tools" / "add_license_headers.py"
TEMPLATE = ["Copyright-Tag: 2026 Example", "License-Tag: Example-1.0"]


@pytest.fixture(scope="module")
def tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("add_license_headers", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_hash_header_is_framed_and_separated_from_code(tool: ModuleType) -> None:
    result = tool.with_header("import os\n", "hash", TEMPLATE)

    assert result == (
        "#\n# Copyright-Tag: 2026 Example\n# License-Tag: Example-1.0\n#\n\nimport os\n"
    )


def test_header_goes_after_the_shebang(tool: ModuleType) -> None:
    result = tool.with_header("#!/usr/bin/env bash\n\nset -e\n", "hash", TEMPLATE)

    assert result.splitlines()[:6] == [
        "#!/usr/bin/env bash",
        "#",
        "# Copyright-Tag: 2026 Example",
        "# License-Tag: Example-1.0",
        "#",
        "",
    ]
    assert result.endswith("\nset -e\n")


def test_markdown_header_is_an_html_comment(tool: ModuleType) -> None:
    result = tool.with_header("# Title\n", "html", TEMPLATE)

    assert result == (
        "<!--\n"
        "Copyright-Tag: 2026 Example\n"
        "\n"
        "License-Tag: Example-1.0\n"
        "-->\n"
        "\n"
        "# Title\n"
    )


@pytest.mark.parametrize(
    ("name", "style"),
    [
        ("module.py", "hash"),
        ("ci.yml", "hash"),
        ("Makefile", "hash"),
        ("sample.env", "hash"),
        ("README.md", "html"),
        ("process.bpmn", None),
        ("logo.png", None),
    ],
)
def test_comment_style_by_file_type(
    tool: ModuleType, name: str, style: str | None
) -> None:
    assert tool.comment_style(Path(name)) == style


def test_reuse_globs_match_like_reuse(tool: ModuleType) -> None:
    single = tool.reuse_glob_to_regex("docs/images/*")
    recursive = tool.reuse_glob_to_regex("**/*.bpmn")

    assert single.match("docs/images/logo.png")
    assert not single.match("docs/images/media/demo.gif")
    assert recursive.match("tests/fixtures/approval.bpmn")
    assert not recursive.match("tests/fixtures/approval.bpmn.bak")


def test_check_skips_reuse_toml_paths_and_existing_headers(
    tool: ModuleType, tmp_path: Path
) -> None:
    (tmp_path / "covered.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "missing.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "done.py").write_text(
        f"# {tool.LICENSE_TAG}: Example-1.0\n", encoding="utf-8"
    )
    (tmp_path / "image.png").write_bytes(b"\x89PNG")
    patterns = [tool.reuse_glob_to_regex("covered.py")]

    flagged = [
        name
        for name in ("covered.py", "missing.py", "done.py", "image.png")
        if tool.needs_header(tmp_path, name, patterns)
    ]

    assert flagged == ["missing.py"]
