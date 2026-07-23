#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

SIGNOFF_RE = re.compile(
    r"^Signed-off-by:\s*(?P<name>.+?)\s*<(?P<email>[^<>\s]+@[^<>\s]+)>\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Identity:
    name: str
    email: str

    def normalized(self) -> tuple[str, str]:
        normalized_name = " ".join(self.name.split()).casefold()
        normalized_email = self.email.strip().casefold()
        return normalized_name, normalized_email

    def rendered(self) -> str:
        return f"{self.name} <{self.email}>"


@dataclass(frozen=True)
class CommitCheck:
    sha: str
    author: Identity
    committer: Identity
    message: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate DCO Signed-off-by trailers.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--rev-range",
        help="Git revision range to inspect, for example origin/main..HEAD.",
    )
    group.add_argument(
        "--commit",
        action="append",
        dest="commits",
        help="Specific commit SHA to inspect. May be passed multiple times.",
    )
    group.add_argument(
        "--commit-msg-file",
        type=Path,
        help="Path to a commit message file. Intended for commit-msg hooks.",
    )
    return parser.parse_args()


def run_git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return completed.stdout


def parse_signoffs(message: str) -> list[Identity]:
    signoffs: list[Identity] = []
    for line in message.splitlines():
        match = SIGNOFF_RE.match(line.strip())
        if match is None:
            continue
        signoffs.append(Identity(match.group("name"), match.group("email")))
    return signoffs


def load_commit(sha: str) -> CommitCheck:
    raw = run_git("show", "-s", "--format=%an%x00%ae%x00%cn%x00%ce%x00%B", sha)
    parts = raw.split("\x00", 4)
    if len(parts) != 5:
        raise RuntimeError(f"Unable to parse commit metadata for {sha}.")
    author = Identity(parts[0].strip(), parts[1].strip())
    committer = Identity(parts[2].strip(), parts[3].strip())
    return CommitCheck(sha=sha, author=author, committer=committer, message=parts[4])


def commit_check_error(commit: CommitCheck) -> str | None:
    signoffs = parse_signoffs(commit.message)
    if not signoffs:
        return (
            f"commit {commit.sha[:12]} is missing a Signed-off-by trailer. "
            "Use `git commit -s`, `git commit --amend -s`, or "
            "`git rebase --signoff <base>`."
        )

    expected = {commit.author.normalized(), commit.committer.normalized()}
    found = {signoff.normalized() for signoff in signoffs}
    if expected.isdisjoint(found):
        expected_rendered = ", ".join(
            identity.rendered()
            for identity in (commit.author, commit.committer)
            if identity.name and identity.email
        )
        found_rendered = ", ".join(signoff.rendered() for signoff in signoffs)
        return (
            f"commit {commit.sha[:12]} has Signed-off-by trailer(s), but none match "
            f"the author or committer identity. Expected one of: {expected_rendered}. "
            f"Found: {found_rendered}."
        )
    return None


def validate_commit_message_file(path: Path) -> int:
    message = path.read_text(encoding="utf-8")
    if parse_signoffs(message):
        print(f"DCO signoff trailer found in {path}.")
        return 0

    print(
        "Commit message is missing a Signed-off-by trailer. "
        "Use `git commit -s` or add a `Signed-off-by: Your Name <you@example.com>` "
        "line before committing.",
        file=sys.stderr,
    )
    return 1


def validate_commits(commits: list[str]) -> int:
    failures: list[str] = []
    for sha in commits:
        failure = commit_check_error(load_commit(sha))
        if failure is not None:
            failures.append(failure)

    if failures:
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1

    print(f"Validated DCO trailers for {len(commits)} commit(s).")
    return 0


def commits_from_range(rev_range: str) -> list[str]:
    output = run_git("rev-list", "--reverse", rev_range)
    return [line.strip() for line in output.splitlines() if line.strip()]


def main() -> int:
    args = parse_args()

    if args.commit_msg_file is not None:
        return validate_commit_message_file(args.commit_msg_file)

    commits = args.commits or commits_from_range(args.rev_range)
    if not commits:
        print("No commits found to validate.")
        return 0

    return validate_commits(commits)


if __name__ == "__main__":
    sys.exit(main())
