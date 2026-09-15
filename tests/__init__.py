"""Test suite.

This file exists so that ``tests`` is an unambiguous package name. Without it,
mypy resolves ``tests/helpers.py`` as both ``helpers`` (because ``tests`` is a
check root) and ``tests.helpers`` (because the suite imports it that way), and
refuses to check anything at all. pytest is happy either way.
"""

from __future__ import annotations
