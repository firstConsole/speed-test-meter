"""Entry point for ``python -m speedmeter``.

Present so the tool runs straight from a clone, with no installation step. The
installed ``speedmeter`` command and this module share the same ``main``, so
the two paths cannot drift apart.
"""

from __future__ import annotations

from speedmeter.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
