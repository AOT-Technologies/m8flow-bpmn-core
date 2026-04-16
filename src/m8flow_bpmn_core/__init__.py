from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("m8flow-bpmn-core")
except PackageNotFoundError:  # pragma: no cover - package not installed yet.
    __version__ = "0.1.0"

__all__ = ["__version__"]
