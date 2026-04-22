# Packaging And Dependency Use

The project is already configured as a Python package through `pyproject.toml`
and `hatchling`. The simplest build path is the root `Makefile`.

## Build The Package

Create both the wheel and the source distribution:

```bash
make package
```

This is the same as:

```bash
uv build
```

Artifacts are written to `dist/`:

- a wheel, which is the preferred dependency artifact
- a source distribution, which is useful for source-based publishing

If you only want one artifact type:

- `make package-wheel`
- `make package-sdist`

## Use The Package As A Dependency

Once the wheel is built, another project can depend on it directly.

Example with `uv`:

```bash
uv add ./dist/m8flow_bpmn_core-0.1.0-py3-none-any.whl
```

Example with `pip`:

```bash
pip install ./dist/m8flow_bpmn_core-0.1.0-py3-none-any.whl
```

In a CI pipeline or artifact repository, upload the wheel from `dist/` and use
that wheel in downstream builds.

## Recommended Workflow

1. Run tests locally.
2. Build the package with `make package`.
3. Take the wheel from `dist/` and consume it in the other project.

## Notes

- The wheel filename changes when the project version changes.
- The package metadata comes from `pyproject.toml`.
- The code is laid out under `src/m8flow_bpmn_core`, so the built wheel will
  contain the importable `m8flow_bpmn_core` package.
