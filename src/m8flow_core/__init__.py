#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("m8flow-core")
except PackageNotFoundError:  # pragma: no cover - package not installed yet.
    __version__ = "1.0.0"

__all__ = ["__version__"]
