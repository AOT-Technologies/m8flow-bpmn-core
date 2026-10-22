from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("m8flow-core")
except PackageNotFoundError:  # pragma: no cover - package not installed yet.
    __version__ = "0.2.1"

__all__ = ["__version__"]
