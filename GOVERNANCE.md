<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Governance

m8flow-core is an open source project sponsored by AOT Technologies. This page
describes the roles in the project and how decisions are made.

## Roles

**Users** embed m8flow-core in their applications. They help by reporting
bugs, requesting features, and answering questions.

**Contributors** submit pull requests, documentation, or reviews. Anyone can
become a contributor by following the [contributing guide](CONTRIBUTING.md).

**Maintainers** are listed in [MAINTAINERS.md](MAINTAINERS.md) and in
[`.github/CODEOWNERS`](.github/CODEOWNERS). Maintainers:

- review and merge pull requests
- triage issues and set labels and milestones
- keep the [roadmap](ROADMAP.md) current
- publish releases, as described in
  [Publish a release](docs/how-to/publish-a-release.md)
- enforce the [code of conduct](CODE_OF_CONDUCT.md)

## How decisions are made

Most decisions are made by **lazy consensus** on the pull request or issue:
a proposal goes ahead if no maintainer objects within a reasonable time,
normally five working days for anything non-trivial.

Every pull request needs approval from at least one maintainer other than
its author before it merges.

The following changes need an issue first, so the design can be discussed
before code is written, and approval from at least two maintainers:

- changes to the public contract: `m8flow_core.api`, the database schema, or
  documented error semantics (see [Versioning](docs/reference/versioning.md))
- new runtime dependencies
- changes to licensing, governance, or the release process

If maintainers can't reach consensus, a simple majority vote of the
maintainers decides. AOT Technologies, as project sponsor, breaks a tie.

## Becoming a maintainer

A contributor who has made sustained, high-quality contributions, through
code, reviews, or documentation, can be nominated by any maintainer. The
nomination is made in a pull request that adds the contributor to
`MAINTAINERS.md` and `.github/CODEOWNERS`, and it's accepted by lazy consensus
of the existing maintainers.

## Stepping down

A maintainer can step down at any time by opening a pull request that moves
them to the emeritus list in `MAINTAINERS.md`. A maintainer who has been
inactive for six months may be moved to emeritus by the other maintainers,
after asking them first.

## Changing this document

Changes to this document follow the decision process above and need approval
from at least two maintainers.
