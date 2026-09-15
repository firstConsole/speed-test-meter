"""Measure internet download speed by timing sequential HTTP requests.

The package follows a ports-and-adapters layout. Dependencies point inwards
only: adapters depend on ports, ports depend on the domain, and nothing in the
domain layer imports from an outer layer. The object graph is assembled in a
single place, :mod:`speedmeter.cli`.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

__all__ = ["__version__"]

try:
    __version__ = version("speed-test-meter")
except PackageNotFoundError:  # pragma: no cover - source checkout, not installed
    # The distribution metadata is absent when the package is run straight from
    # a clone (``python -m speedmeter``). Everything still works; only the
    # reported version is unknown. Keeping the number in pyproject.toml alone
    # avoids having two places to update.
    __version__ = "0.0.0+unknown"
