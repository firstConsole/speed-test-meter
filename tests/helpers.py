"""Shared test doubles and builders.

Import these as ``from tests.helpers import ...``. The suite runs with
``--import-mode=importlib``, which does not put the test directory on
``sys.path``, so a bare ``import helpers`` would not resolve. pytest
synthesises a namespace package for ``tests`` instead, and the qualified form
works without any ``__init__.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from speedmeter.domain import DownloadResult, SpeedReport
from speedmeter.ports import Downloader, ProgressListener

if TYPE_CHECKING:
    from collections.abc import Sequence

URL = "https://example.invalid/heavy.jpg"
"""A syntactically valid address that can never resolve (RFC 2606)."""

Outcome = DownloadResult | Exception
"""One entry in a :class:`ScriptedDownloader` script: a result or a failure."""


def make_result(
    size_bytes: int = 1_000_000,
    elapsed_seconds: float = 1.0,
    *,
    url: str = URL,
    status_code: int = 200,
) -> DownloadResult:
    """Build a valid result, overriding only what a test cares about."""
    return DownloadResult(
        url=url,
        status_code=status_code,
        size_bytes=size_bytes,
        elapsed_seconds=elapsed_seconds,
    )


def make_report(*results: DownloadResult, failures: tuple[str, ...] = ()) -> SpeedReport:
    """Build a report whose requested attempt count matches what it was given."""
    return SpeedReport(
        url=URL,
        requested_attempts=len(results) + len(failures),
        results=results,
        failures=failures,
    )


def successes(count: int) -> list[Outcome]:
    """Build a script of ``count`` identical successful outcomes."""
    return [make_result() for _ in range(count)]


class ScriptedDownloader(Downloader):
    """A downloader that replays a prepared sequence of outcomes.

    Each entry is either a :class:`DownloadResult` to return or an exception to
    raise. Running out of entries raises rather than repeating the last one, so
    a test that provokes more attempts than it planned for fails loudly instead
    of quietly passing on stale data.
    """

    def __init__(self, script: Sequence[Outcome]) -> None:
        """Prepare the outcomes this downloader will produce, in order."""
        self._script = list(script)
        self.requested_urls: list[str] = []

    @property
    def call_count(self) -> int:
        """Return how many times :meth:`download` has been called."""
        return len(self.requested_urls)

    def download(self, url: str) -> DownloadResult:
        """Return or raise the next scripted outcome."""
        self.requested_urls.append(url)
        if not self._script:
            message = f"ScriptedDownloader ran out of outcomes after {self.call_count} calls."
            raise AssertionError(message)
        outcome = self._script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    """One notification received by :class:`RecordingProgressListener`."""

    kind: Literal["succeeded", "failed"]
    number: int
    total: int


class RecordingProgressListener(ProgressListener):
    """A listener that keeps every notification it receives, in order."""

    def __init__(self) -> None:
        """Start with an empty log."""
        self.events: list[ProgressEvent] = []

    def on_attempt_succeeded(self, number: int, total: int, result: DownloadResult) -> None:
        """Record the success."""
        self.events.append(ProgressEvent(kind="succeeded", number=number, total=total))

    def on_attempt_failed(self, number: int, total: int, reason: str) -> None:
        """Record the failure."""
        self.events.append(ProgressEvent(kind="failed", number=number, total=total))
