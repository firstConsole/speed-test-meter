"""Boundaries between the application and everything outside it.

This module declares *what* the application needs, never *how* it is provided.
Nothing here imports a transport library, touches a socket or writes to a
stream; the concrete implementations live in :mod:`speedmeter.adapters` and are
supplied from the composition root in :mod:`speedmeter.cli`.

Keeping the boundary explicit is what lets the runner be tested without a
network: a test supplies its own :class:`Downloader` that returns canned
results, and the code under test cannot tell the difference.

Abstract base classes are used rather than :class:`typing.Protocol` so that an
incomplete implementation fails loudly at construction time instead of at the
first missing attribute access.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from speedmeter.domain import DownloadResult, SpeedReport


class Downloader(ABC):
    """Fetches a resource and measures how long that took.

    Implementations are expected to read the response body to the end -- the
    measurement is meaningless otherwise -- and to release the connection
    before returning.
    """

    @abstractmethod
    def download(self, url: str) -> DownloadResult:
        """Download the resource at ``url`` and return the measurement.

        Args:
            url: Absolute address of the resource to download.

        Returns:
            The measured outcome of this single attempt.

        Raises:
            DownloadError: If the attempt did not produce a measurement, for
                any reason. Callers treat this as a recoverable, per-attempt
                failure, so implementations must translate transport-specific
                exceptions into this hierarchy rather than letting them escape.
        """


class Reporter(ABC):
    """Presents a finished report to the user.

    Exists so that the use case can hand off a result without knowing whether
    it ends up on a terminal, in a log or in a JSON document. Adding an output
    format means adding an implementation of this port, not editing the runner
    -- the open/closed principle applied to the one axis along which this
    program is actually likely to change.
    """

    @abstractmethod
    def report(self, report: SpeedReport) -> None:
        """Present the outcome of a completed run.

        Args:
            report: The aggregated result of the run.
        """


class ProgressListener(ABC):
    """Observes attempts as they finish, while the run is still going.

    Ten sequential downloads of a heavy image take a noticeable amount of time,
    and a silent terminal is indistinguishable from a hung one. This port lets
    the runner announce progress without knowing that a console exists.
    """

    @abstractmethod
    def on_attempt_succeeded(self, number: int, total: int, result: DownloadResult) -> None:
        """React to an attempt that produced a measurement.

        Args:
            number: One-based index of the attempt that just finished.
            total: How many attempts the run will make in all.
            result: What the attempt measured.
        """

    @abstractmethod
    def on_attempt_failed(self, number: int, total: int, reason: str) -> None:
        """React to an attempt that failed.

        Args:
            number: One-based index of the attempt that just finished.
            total: How many attempts the run will make in all.
            reason: Human-readable description of the failure.
        """


class NullProgressListener(ProgressListener):
    """A listener that does nothing.

    The Null Object lets the runner call the port unconditionally instead of
    guarding every notification with a ``None`` check. The behaviour "report no
    progress" then lives in one named place rather than being implied by
    scattered conditionals.
    """

    def on_attempt_succeeded(self, number: int, total: int, result: DownloadResult) -> None:
        """Ignore the successful attempt."""

    def on_attempt_failed(self, number: int, total: int, reason: str) -> None:
        """Ignore the failed attempt."""
