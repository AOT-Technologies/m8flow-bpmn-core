# Build and install the package

This guide shows how to build m8flow-core as a wheel and install it in
another project. The package isn't published to PyPI yet, so you build it from
source.

## Build the wheel

To build both the wheel and the source distribution, run the following
command from the repository root:

```bash
make package
```

`make package` runs `uv build`. To build only one artifact, run
`make package-wheel` or `make package-sdist`.

The artifacts are written to `dist/`. The wheel, such as
`dist/m8flow_core-1.0.0-py3-none-any.whl`, is the one to install. Its
filename changes with the version in `pyproject.toml`.

The wheel contains only the importable `m8flow_core` package. It doesn't
include the Alembic migrations, the examples, or the test fixtures.

### If `uv build` fails on Windows

On Windows, `uv build` can fail because a temporary `uv-trampoline-*.exe` file
is locked. That's a local tooling issue, not a package problem. To build with
the build backend directly instead, run the following commands:

```bash
python -m pip install build hatchling
python -m build --wheel --no-isolation
```

## Install the wheel in another project

To add the wheel to a project that uses uv, run the following command:

```bash
uv add ./dist/m8flow_core-1.0.0-py3-none-any.whl
```

To install it with pip, run the following command:

```bash
pip install ./dist/m8flow_core-1.0.0-py3-none-any.whl
```

In a CI pipeline, upload the wheel from `dist/` to your artifact repository
and install it from there in downstream builds.

The [sample app](../../sample_app/README.md) consumes the library this way,
with a helper script that copies the newest wheel into the app and refreshes
its lockfile.

## Install a database driver

The library depends on SQLAlchemy but not on a database driver. SQLite works
with no extra package. For other databases, install the matching extra:

| Database | Extra | Driver |
|---|---|---|
| PostgreSQL | `postgresql` | `psycopg[binary]` |
| MySQL | `mysql` | `pymysql` |

For example, to install the wheel with PostgreSQL support, run the following
command:

```bash
pip install "./dist/m8flow_core-1.0.0-py3-none-any.whl[postgresql]"
```

The `celery` extra installs Celery with Redis, which only the Celery scheduler
example needs.

## Related pages

- [Embed the library in your application](embed-in-your-application.md)
- [Configuration](../reference/configuration.md)
