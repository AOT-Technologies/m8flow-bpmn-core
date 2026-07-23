from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "tools" / "check_dco.py"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is required")


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return completed


def run_dco_check(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def init_repo(tmp_path: Path) -> None:
    git(tmp_path, "init")
    git(tmp_path, "config", "user.name", "Example User")
    git(tmp_path, "config", "user.email", "user@example.com")


def write_and_commit(tmp_path: Path, message: str, *, signoff: bool) -> None:
    file_path = tmp_path / "example.txt"
    file_path.write_text(message, encoding="utf-8")
    git(tmp_path, "add", "example.txt")
    commit_args = ["commit", "-m", message]
    if signoff:
        commit_args.append("--signoff")
    git(tmp_path, *commit_args)


def test_check_dco_passes_for_signed_commit(tmp_path: Path) -> None:
    init_repo(tmp_path)
    write_and_commit(tmp_path, "Add signed example", signoff=True)

    result = run_dco_check(tmp_path, "--commit", "HEAD")

    assert result.returncode == 0
    assert "Validated DCO trailers" in result.stdout


def test_check_dco_fails_for_unsigned_commit(tmp_path: Path) -> None:
    init_repo(tmp_path)
    write_and_commit(tmp_path, "Add unsigned example", signoff=False)

    result = run_dco_check(tmp_path, "--commit", "HEAD")

    assert result.returncode == 1
    assert "missing a Signed-off-by trailer" in result.stderr


def test_commit_message_mode_accepts_signed_message(tmp_path: Path) -> None:
    init_repo(tmp_path)
    message_file = tmp_path / "COMMIT_EDITMSG"
    message_file.write_text(
        "Add local hook coverage\n\nSigned-off-by: Example User <user@example.com>\n",
        encoding="utf-8",
    )

    result = run_dco_check(tmp_path, "--commit-msg-file", str(message_file))

    assert result.returncode == 0
    assert "DCO signoff trailer found" in result.stdout
