<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Versioning policy

m8flow-core follows [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html)
from 1.0.0 onward. A version is `MAJOR.MINOR.PATCH`. A change that breaks the
public contract described on this page ships only in a new major version.
The [changelog](../../CHANGELOG.md) records every release.

## The public contract

The following make up the public contract. A change that breaks any of them
is a breaking change:

| Surface | What is covered |
|---|---|
| Python API | Every name exported from `m8flow_core.api` and documented in the [API reference](api.md): commands, queries, dispatchers, service functions, enums, hooks, and errors |
| Command and query fields | The name, order, type, and default of every field. `tenant_id` stays the first field. |
| Error semantics | The `BpmnCoreError` hierarchy and which error each operation raises, as documented in [Errors](api.md#errors) |
| Returned models | The columns and behavior of returned ORM models that the [API reference](api.md) documents |
| Database schema | Table and column names in the [database schema](database-schema.md), which host applications share |
| Migrations | Released Alembic revisions. They are immutable, and a new release only adds revisions on top of them. |
| Configuration | Settings and environment variables in [Configuration](configuration.md) |
| Python support | The minimum supported Python version in [Supported Python versions](#supported-python-versions) |

Everything else is internal and can change in any release. That includes
modules other than `m8flow_core.api`, names that start with an underscore,
ORM relationships and undocumented columns, log messages, and the wording of
error messages.

## What each part of the version means

| Part | Bumped for | Examples |
|---|---|---|
| `MAJOR` | A breaking change to the public contract | Removing or renaming an exported name, a command field, a table, or a column; changing a field's type or a documented error; raising the minimum Python version |
| `MINOR` | A backward-compatible addition | A new command, query, or enum member; a new optional field with a default at the end of a command; a new table, nullable column, or additive migration; a deprecation |
| `PATCH` | A backward-compatible fix | A bug fix that makes behavior match the documentation; a documentation fix; a security fix that doesn't change the contract |

A security fix can break the contract when there's no other way to close the
hole. The changelog then lists it under **Security** and says what changed.

Pre-releases, such as `1.1.0b1`, carry no compatibility promise. They use the
[PEP 440](https://peps.python.org/pep-0440/) form, so `pip install
m8flow-core` never picks one up. See
[Publish a release](../how-to/publish-a-release.md).

## Deprecations

Before a part of the public contract is removed, it's deprecated first:

1. A minor release marks it as deprecated. It keeps working, emits a
   `DeprecationWarning` when used, and its entry in the
   [API reference](api.md) names the replacement.
2. The changelog lists it under **Deprecated**.
3. The next major release can remove it. The changelog lists the removal
   under **Removed**, and an upgrade guide covers the move, as
   [Upgrade to 1.0](../how-to/upgrade-to-1.0.md) does for 1.0.

No part of the 1.0 contract is deprecated or experimental today.

## Supported Python versions

m8flow-core requires Python 3.12 or later, as `requires-python` in
`pyproject.toml` declares. After every merge to `main`, CI runs the test
suite on Python 3.12, 3.13, and 3.14, each on Linux, macOS, and Windows. Development tooling uses 3.12, the
version pinned in `.python-version`. Later 3.x versions are allowed by the
package metadata but aren't tested yet.

## Dependencies

Runtime dependencies such as SpiffWorkflow and SQLAlchemy are pinned below
their next major version in `pyproject.toml`. Widening a range to accept a new
major version of a dependency ships in a minor release when the public
contract doesn't change. If the upgrade changes the public contract, it ships
in a major release.
