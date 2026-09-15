"""Concrete implementations of the ports declared in :mod:`speedmeter.ports`.

Everything that talks to the outside world -- sockets, streams, the terminal --
lives in this subpackage. Modules here may import from the inner layers; no
inner layer imports from here.
"""

from __future__ import annotations
