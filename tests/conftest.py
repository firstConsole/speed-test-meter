"""Shared pytest configuration.

Hypothesis aborts an example that exceeds a wall-clock deadline, 200 ms by
default. Every property here is arithmetic over at most twenty items, so a
deadline failure could only ever mean the machine paused the process -- which
a shared CI runner does routinely. Disabling it removes a class of failure
that would always be a false alarm, and costs nothing: a genuinely slow
property would show up in the suite's own runtime.
"""

from __future__ import annotations

from hypothesis import settings

settings.register_profile("default", deadline=None)
settings.load_profile("default")
