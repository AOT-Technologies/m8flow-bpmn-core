<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Publish a release

This guide is for maintainers. It shows how to publish m8flow-core to PyPI
with the **Publish m8flow-core** workflow, either as a beta for testing or as a
final release.

The workflow runs only when you start it. It reads the version from
`pyproject.toml`, runs the tests, builds the wheel and the source
distribution, checks them, scans their dependencies for known
vulnerabilities, and uploads them to PyPI with build attestations.

## Before you begin

You need the following:

- Permission to run workflows in the GitHub repository.
- A trusted publisher on PyPI for this repository. It lets the workflow upload
  without a stored API token. Set it up once, as described in
  [Set up PyPI publishing](#set-up-pypi-publishing).

## Choose a channel

The workflow has two channels:

| Channel | Publishes | Requirements |
|---|---|---|
| `beta` | A pre-release derived from the version in `pyproject.toml`: `1.0.0b1`, then `1.0.0b2`, and so on. The workflow picks the next number from what's already on PyPI. | None |
| `release` | The version in `pyproject.toml`, unchanged, such as `1.0.0` | A dated `CHANGELOG.md` entry, a GitHub Release for the version (draft or published) whose tag points at the commit you publish, and a version that isn't on PyPI yet |

`pip install m8flow-core` never installs a beta. To try one, install it by
version, such as `pip install m8flow-core==1.0.0b1`, or pass `--pre`.

## Publish a beta

To publish a beta, follow these steps:

1. In the repository, go to **Actions** > **Publish m8flow-core**.
2. Click **Run workflow**.
3. In **Use workflow from**, choose the branch or tag to publish.
4. In **Release channel**, choose `beta`, then click **Run workflow**.

The run summary shows the version it published.

## Publish a release

To publish a release, follow these steps:

1. Set `version` in `pyproject.toml` to the new version, in the form
   `MAJOR.MINOR.PATCH`.
2. In `CHANGELOG.md`, rename `## [Unreleased]` to the version and today's
   date, such as `## [1.0.1] - 2026-11-03`, add a new empty
   `## [Unreleased]` section above it, and add the version's link at the
   bottom:

   ```text
   [1.0.1]: https://github.com/AOT-Technologies/m8flow-core/releases/tag/1.0.1
   ```

3. Merge those changes to `main`.
4. Go to **Actions** > **Create release tag**, click **Run workflow**, enter
   the version, such as `1.0.1`, and click **Run workflow**. The workflow
   checks that `pyproject.toml` and `CHANGELOG.md` on `main` match the
   version, then tags the tip of `main` with the bare version, with no `v`
   prefix. m8flow refers to core versions by this tag.
5. Go to **Actions** > **GitHub Release**, click **Run workflow**, enter the
   same version, and click **Run workflow**. The workflow creates a draft
   GitHub Release for the tag, with the version's changelog section as release
   notes and a software bill of materials (`sbom.json`) of the runtime
   dependencies attached.
6. On the repository's **Releases** page, open the draft and review its notes
   and assets. Don't publish it yet.
7. Go to **Actions** > **Publish m8flow-core** and click **Run workflow**.
8. In **Use workflow from**, choose the tag, such as `1.0.1`.
9. In **Release channel**, choose `release`, then click **Run workflow**.
10. When the version is on PyPI, open the draft release, click **Edit**, and
    click **Publish release**.

**Create release tag** stops without tagging if the tag already exists or the
version and changelog don't match. **GitHub Release** stops if a release for
the version already exists, draft or published. To regenerate a draft, delete
it first; the tag stays.

The publish workflow stops before uploading anything if a check fails: for example,
if the version is already on PyPI, the changelog entry or link is missing, the
GitHub Release doesn't exist, the release tag points at a different commit
than the one you chose in **Use workflow from**, a test fails, or a dependency
has a known vulnerability. Fix the cause and run the workflow again.

## Withdraw or replace a release

A published version can't be changed. PyPI never accepts a file name or a
version number a second time, even after the file is deleted, and m8flow
pins core by its release tag. A broken release therefore isn't rolled back in
place. You yank it, so new installs skip it, and publish a patch release
that fixes it.

### Yank the broken version

Yanking hides a version from `pip install m8flow-core` and from version
ranges, but `pip install m8flow-core==1.0.1` still installs it, so pinned
installs keep working. To yank a version, follow these steps:

1. Sign in to [pypi.org](https://pypi.org) as an owner or maintainer of
   `m8flow-core`.
2. Go to **Your projects** > **m8flow-core** > **Manage** > **Releases**.
3. Next to the version, click **Options** > **Yank**.
4. Enter a reason, such as `Corrupts process metadata on retry; use 1.0.2`,
   and confirm. pip shows the reason to anyone who installs the version by
   pin.

Yank rather than delete. Deleting breaks every pinned install and lockfile,
and the version number stays used anyway. Delete only a release that must
not be downloaded at all, such as one that contains a secret, and rotate
the secret first.

A yank can be undone from the same **Options** menu.

### Mark the release on GitHub and in the changelog

Leave the tag where it is. m8flow and lockfiles refer to it, and the
**Create release tag** workflow won't move an existing tag. Instead, follow
these steps:

1. On the repository's **Releases** page, edit the release. Add a note at the
   top of the notes that says the version is yanked, why, and which version
   replaces it.
2. In `CHANGELOG.md`, add ` [YANKED]` to the version's heading, as
   [Keep a Changelog](https://keepachangelog.com/en/1.1.0/#yanked) describes:

   ```text
   ## [1.0.1] - 2026-11-03 [YANKED]
   ```

   Make this edit in the same pull request as the fix, described next.

### Publish the fix as a patch release

To replace the yanked version, follow these steps:

1. Fix the problem on `main` and add a changelog entry under **Fixed** or
   **Security**.
2. Publish the next patch version, such as `1.0.2`, by following
   [Publish a release](#publish-a-release).
3. In m8flow, move the core reference to the new tag and release m8flow if
   its users need the fix.

The release workflows always tag the tip of `main`, so the patch release
contains everything merged since the broken one. If `main` holds changes that
aren't backward-compatible fixes, revert them first or release a minor version
instead. See the [versioning policy](../reference/versioning.md).

If the broken release added an Alembic migration, don't edit or delete that
revision: hosts may have already applied it. Add a new revision on top that
corrects the schema.

## Set up PyPI publishing

The workflow uses [trusted publishing](https://docs.pypi.org/trusted-publishers/),
so no PyPI token is stored in GitHub. To set it up before the first release,
follow these steps:

1. Sign in to [pypi.org](https://pypi.org) with an account that will own the
   `m8flow-core` project.
2. Go to **Your account** > **Publishing**, and add a pending publisher with
   the following values:

   | Field | Value |
   |---|---|
   | PyPI project name | `m8flow-core` |
   | Owner | `AOT-Technologies` |
   | Repository name | `m8flow-core` |
   | Workflow name | `publish.yml` |
   | Environment name | Leave empty |

The first successful run creates the project on PyPI. After that, the
publisher appears under the project's **Publishing** settings.

## Signing and provenance

Release artifacts are signed with [Sigstore](https://www.sigstore.dev/)
through the workflow's GitHub identity, so no signing key is stored. Each
release carries the following:

| What | How it's produced | How to check it |
|---|---|---|
| PyPI attestations for the wheel and sdist | `pypa/gh-action-pypi-publish` with `attestations: true` | The **Provenance** section of the file on pypi.org, or `pypi-attestations verify pypi` |
| GitHub build provenance for the wheel and sdist | `actions/attest-build-provenance` in the **Test and build** job | `gh attestation verify` |
| SHA-256 checksums | The **Record checksums** step, uploaded as a workflow artifact | `sha256sum --check` |

Release tags are annotated but not GPG-signed. Only the **Create release
tag** workflow creates them, and the attestations above tie each artifact to
the workflow run and commit it was built from.

To verify a downloaded wheel, run the following commands, replacing the
version as needed:

```bash
gh attestation verify m8flow_core-1.0.0-py3-none-any.whl \
    --repo AOT-Technologies/m8flow-core
uvx pypi-attestations verify pypi \
    --repository https://github.com/AOT-Technologies/m8flow-core \
    pypi:m8flow_core-1.0.0-py3-none-any.whl
```

## Related pages

- [Changelog](../../CHANGELOG.md)
- [Build and install the package](build-and-install-the-package.md)
- [Develop and test](develop-and-test.md)
