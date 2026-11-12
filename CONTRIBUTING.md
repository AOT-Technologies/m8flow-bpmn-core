<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Contributing to m8flow-core

Thanks for your interest in contributing. This guide covers how to set up a
development environment, the checks a pull request must pass, and the rules
for changing the library.

By contributing, you agree that your contributions are licensed under the
project's [Apache License 2.0](LICENSE), and you certify the
[Developer Certificate of Origin (DCO) 1.1](https://developercertificate.org/)
for each commit. See [Sign off your commits](#sign-off-your-commits).

Read the [code of conduct](CODE_OF_CONDUCT.md) before you take part.
[GOVERNANCE.md](GOVERNANCE.md) explains how decisions are made, and
[MAINTAINERS.md](MAINTAINERS.md) lists who reviews and merges changes. Check
the [roadmap](ROADMAP.md) before you start on a large feature.

## Prerequisites

You need the following tools:

| Tool | Version | Notes |
|---|---|---|
| Git | Any recent version | |
| Python | 3.12 | Pinned in `.python-version` |
| [uv](https://docs.astral.sh/uv/) | Latest | Installs dependencies from the committed `uv.lock` |
| Docker | Any recent version | Optional. Only the PostgreSQL integration test and some examples need it. |

## Development setup

To clone the repository, install the development environment, and install the
Git hooks, run the following commands:

```bash
git clone https://github.com/AOT-Technologies/m8flow-core.git
cd m8flow-core
uv sync
make precommit-install
```

The hooks run Ruff, mypy, and Bandit before each commit, and check each
commit message for a `Signed-off-by` trailer. [Develop and test](docs/how-to/develop-and-test.md)
covers the full workflow, including PostgreSQL test runs and migrations.

## Sign off your commits

Every commit must include a `Signed-off-by` trailer that matches the commit
author or committer. The trailer certifies the
[Developer Certificate of Origin (DCO) 1.1](https://developercertificate.org/):
that you have the right to submit the change under the project's license.

To sign off a new commit, the most recent commit, or every commit on your
branch, use the matching command:

```bash
git commit -s -m "Describe the change"
git commit --amend -s
git rebase --signoff origin/main
```

The `commit-msg` hook that `make precommit-install` installs rejects a commit
without a trailer. A DCO workflow checks every commit in a pull request and in
the merge queue.

## Checks

CI runs the following checks on every pull request. Run the ones that match
your change before you open a pull request:

| Check | Command |
|---|---|
| Lint | `make lint` |
| Formatting | `make format-check` (`make format` fixes it) |
| Type check | `make typecheck` |
| Security lint | `make security` |
| License headers | `make license-headers` |
| Unit tests, on SQLite and PostgreSQL | `make test` |
| Sample app lint and tests | See [Run the sample app tests](docs/how-to/develop-and-test.md#run-the-sample-app-tests) |

CI also runs the DCO sign-off check, CodeQL, a Gitleaks secret scan, and
dependency review. Before a
release, also run `make test-integration` (needs Docker) and `make package`.
Maintainers publish releases as described in
[Publish a release](docs/how-to/publish-a-release.md).

## Rules for changes

Reviews check the following rules:

- Keep changes small and inside the existing package boundaries. The
  [repository layout](docs/reference/repository-layout.md) describes them.
- Add or update tests with every behavior change.
- Keep tenant isolation intact. Every user-scoped operation must check tenant
  membership before it acts, and every read and write of tenant data must
  filter on the tenant.
- Raise only errors from the `BpmnCoreError` hierarchy in public code paths.
- Use timezone-aware UTC `datetime` values for every timestamp.
- Never edit a released Alembic migration. Add a new one.
- Only add code, BPMN files, or other content that you wrote or whose license
  is compatible with Apache-2.0. If you adapt a file from another project,
  record its source and license in [NOTICE](NOTICE).
- Start every new file with an SPDX license header. See
  [Add license headers](#add-license-headers).
- Don't add a dependency unless you need it, and explain why in the pull
  request.
- Add an entry under `## [Unreleased]` in [CHANGELOG.md](CHANGELOG.md) for any
  change a library user would notice.

### Changing the public API or schema

The `m8flow_core.api` module and the database schema are the public contract.
Commands and queries are frozen, slotted dataclasses, and `tenant_id` is
always the first field. If you change either, update all of the following in
the same pull request:

- `src/m8flow_core/api.py`, and a migration for any schema change
- [`docs/reference/api.md`](docs/reference/api.md) and
  [`docs/reference/database-schema.md`](docs/reference/database-schema.md)
- the how-to guides and examples that use it
- `tests/test_api_surface.py`, which locks the exported names, and
  `tests/test_compatibility_contract.py`, which locks the schema contract

## Add license headers

The repository follows the [REUSE specification](https://reuse.software/):
every file declares its copyright and license. Files that can hold a comment
start with an SPDX header built from [.copyright.tmpl](.copyright.tmpl). In
code and config files the header is a `#` block:

<!-- REUSE-IgnoreStart -->
```text
#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
```

Markdown files use the same two tags in an HTML comment:

```text
<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->
```
<!-- REUSE-IgnoreEnd -->

To add the header to every new file that lacks one, run the following
command:

```bash
make license-headers-fix
```

The command skips files that [REUSE.toml](REUSE.toml) covers. Add a path there
for a file that can't hold a comment, such as an image, a lock file, or a BPMN
file. Never add a header to a released Alembic migration; `REUSE.toml` covers
those too.

`make license-headers` checks the whole repository. It also runs as a
pre-commit hook and in CI.

## Submitting changes

To submit a change, follow these steps:

1. Fork the repository and create a branch from `main`, for example
   `feature/short-description` or `fix/short-description`.
2. Make your change, with tests and documentation updates.
3. Run the [checks](#checks).
4. Open a pull request against `main`. Describe the change and why you made
   it, and link any related issue.
5. A [maintainer](MAINTAINERS.md) reviews your pull request. Address feedback
   by pushing more commits to your branch. Public API, schema, and dependency
   changes need an issue first and approval from two maintainers; see
   [GOVERNANCE.md](GOVERNANCE.md#how-decisions-are-made).

## Reporting bugs and requesting features

Use the
[issue templates](https://github.com/AOT-Technologies/m8flow-core/issues/new/choose).
For security vulnerabilities, don't open a public issue. Follow the
[security policy](SECURITY.md) instead.
