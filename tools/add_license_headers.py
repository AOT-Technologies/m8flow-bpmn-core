#!/usr/bin/env python3
#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#

"""Add the SPDX license header from .copyright.tmpl to files that lack one.

Run with --check to list the files without a header instead of editing them.
`reuse lint` remains the authoritative REUSE check; this tool only writes the
inline headers that it can place safely.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = ROOT / ".copyright.tmpl"
REUSE_TOML_PATH = ROOT / "REUSE.toml"

# Built without a trailing colon so REUSE doesn't read this file's code as a tag.
LICENSE_TAG = "SPDX-License-" + "Identifier"
HEAD_LINES_SCANNED = 15

HASH_SUFFIXES = frozenset(
    {".py", ".sh", ".ps1", ".mako", ".yml", ".yaml", ".toml", ".ini", ".cfg"}
)
HASH_NAMES = frozenset({"Makefile", "Dockerfile", ".gitignore", ".dockerignore"})
HASH_NAME_SUFFIXES = (".env",)
HTML_SUFFIXES = frozenset({".md"})


def read_template(path: Path = TEMPLATE_PATH) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line]


def hash_header(template: list[str]) -> str:
    body = "".join(f"# {line}\n" for line in template)
    return f"#\n{body}#\n"


def html_header(template: list[str]) -> str:
    return "<!--\n" + "\n\n".join(template) + "\n-->\n"


def comment_style(path: Path) -> str | None:
    if path.suffix in HTML_SUFFIXES:
        return "html"
    if (
        path.suffix in HASH_SUFFIXES
        or path.name in HASH_NAMES
        or path.name.endswith(HASH_NAME_SUFFIXES)
    ):
        return "hash"
    return None


def reuse_glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a REUSE.toml path glob: `**` crosses directories, `*` doesn't."""
    parts: list[str] = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**", index):
            parts.append(".*")
            index += 2
        elif pattern[index] == "*":
            parts.append("[^/]*")
            index += 1
        else:
            parts.append(re.escape(pattern[index]))
            index += 1
    return re.compile("".join(parts) + r"\Z")


def reuse_toml_patterns(path: Path = REUSE_TOML_PATH) -> list[re.Pattern[str]]:
    if not path.exists():
        return []
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    patterns: list[re.Pattern[str]] = []
    for annotation in data.get("annotations", []):
        paths = annotation.get("path", [])
        for glob in [paths] if isinstance(paths, str) else paths:
            patterns.append(reuse_glob_to_regex(glob))
    return patterns


def repository_files(root: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return sorted(set(completed.stdout.splitlines()))


def has_header(text: str) -> bool:
    return LICENSE_TAG in "\n".join(text.splitlines()[:HEAD_LINES_SCANNED])


def needs_header(root: Path, relative: str, patterns: list[re.Pattern[str]]) -> bool:
    path = root / relative
    if not path.is_file() or comment_style(path) is None:
        return False
    if any(pattern.match(relative) for pattern in patterns):
        return False
    if (root / f"{relative}.license").exists():
        return False
    return not has_header(path.read_text(encoding="utf-8"))


def with_header(text: str, style: str, template: list[str]) -> str:
    header = html_header(template) if style == "html" else hash_header(template)
    if text.startswith("#!"):
        shebang, _, rest = text.partition("\n")
        return f"{shebang}\n{header}\n{rest.lstrip(chr(10))}"
    if not text.strip():
        return header
    return f"{header}\n{text}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="list files without a header and exit 1 instead of editing them",
    )
    args = parser.parse_args(argv)

    template = read_template()
    patterns = reuse_toml_patterns()
    missing = [
        relative
        for relative in repository_files(ROOT)
        if needs_header(ROOT, relative, patterns)
    ]

    if args.check:
        for relative in missing:
            print(f"Missing license header: {relative}")
        return 1 if missing else 0

    for relative in missing:
        path = ROOT / relative
        style = comment_style(path)
        assert style is not None
        text = path.read_text(encoding="utf-8")
        path.write_text(with_header(text, style, template), encoding="utf-8")
        print(f"Added license header: {relative}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
