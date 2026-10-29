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
| `release` | The version in `pyproject.toml`, unchanged, such as `1.0.0` | A dated `CHANGELOG.md` entry, a GitHub Release for the version whose tag points at the commit you publish, and a version that isn't on PyPI yet |

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
   same version, and click **Run workflow**. The workflow creates the GitHub
   Release for the tag, with the version's changelog section as release notes
   and a software bill of materials (`sbom.json`) of the runtime dependencies
   attached.
6. Go to **Actions** > **Publish m8flow-core** and click **Run workflow**.
7. In **Use workflow from**, choose the tag, such as `1.0.1`.
8. In **Release channel**, choose `release`, then click **Run workflow**.

**Create release tag** stops without tagging if the tag already exists or the
version and changelog don't match. **GitHub Release** stops if a release for
the version already exists. Because the GitHub Release is published as soon
as it's created, review the release notes in the changelog pull request.

The publish workflow stops before uploading anything if a check fails: for example,
if the version is already on PyPI, the changelog entry or link is missing, the
GitHub Release doesn't exist, the release tag points at a different commit
than the one you chose in **Use workflow from**, a test fails, or a dependency
has a known vulnerability. Fix the cause and run the workflow again.

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

## Related pages

- [Changelog](../../CHANGELOG.md)
- [Build and install the package](build-and-install-the-package.md)
- [Develop and test](develop-and-test.md)
