# Contributing

## Local Workflow

1. Sync dependencies with `uv sync`.
2. Install the local hooks with `make precommit-install`.
3. Run `make lint`, `make typecheck`, `make security`, and `make test` before opening a pull request.

## DCO Signoff

Every commit in this repository must include a `Signed-off-by` trailer that
matches the commit author or committer identity. The simplest way to do that is
to use Git's signoff flag when you commit:

```bash
git commit -s -m "Describe the change"
```

To fix the most recent commit, use:

```bash
git commit --amend -s
```

To add signoffs across a branch before opening or updating a pull request, use:

```bash
git rebase --signoff origin/main
```

The local `commit-msg` hook added by `make precommit-install` checks for a
`Signed-off-by` trailer before the commit is created. GitHub Actions also run a
repository-level DCO check on pull requests and merge queue validations.

By contributing, you agree to the Developer Certificate of Origin (DCO) 1.1:
https://developercertificate.org/

## GitHub Settings

GitHub can automatically sign off commits made in the web editor if repository
or organization administrators enable `Require contributors to sign off on
web-based commits` in GitHub settings. That setting is separate from the
repository files in this repo.
