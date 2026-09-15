"""Tests for the boundary declarations.

These pin two design claims made in :mod:`speedmeter.ports`: that an
incomplete implementation is rejected at construction time rather than at the
first missing attribute access, and that the null listener is safe to call
unconditionally.
"""

from __future__ import annotations

import pytest

from speedmeter.domain import DownloadResult
from speedmeter.ports import (
    Downloader,
    NullProgressListener,
    ProgressListener,
    Reporter,
)


@pytest.mark.parametrize("port", [Downloader, Reporter, ProgressListener])
def test_a_port_cannot_be_instantiated(port: type) -> None:
    with pytest.raises(TypeError, match="abstract"):
        port()


def test_a_partial_implementation_is_rejected_at_construction() -> None:
    """A half-finished listener must fail immediately, not mid-run.

    Without this guarantee the omission would only surface on the first
    failed attempt, which on a healthy connection might be never.
    """

    class HalfFinishedListener(ProgressListener):
        def on_attempt_succeeded(self, number: int, total: int, result: DownloadResult) -> None:
            """Handle the success but forget the failure case."""

    with pytest.raises(TypeError, match="on_attempt_failed"):
        HalfFinishedListener()  # type: ignore[abstract]


def test_a_complete_implementation_is_accepted() -> None:
    class RecordingListener(ProgressListener):
        def on_attempt_succeeded(self, number: int, total: int, result: DownloadResult) -> None:
            """Do nothing."""

        def on_attempt_failed(self, number: int, total: int, reason: str) -> None:
            """Do nothing."""

    assert isinstance(RecordingListener(), ProgressListener)


class TestNullProgressListener:
    """The Null Object the runner uses when nobody is watching."""

    def test_is_a_progress_listener(self) -> None:
        assert isinstance(NullProgressListener(), ProgressListener)

    def test_swallows_both_notifications(self) -> None:
        """Both notifications must be safe to call and must do nothing.

        There is no observable effect to assert on -- that absence *is* the
        contract -- so the test passes by not raising. If either method were
        left abstract or started touching a stream, this would fail.
        """
        listener = NullProgressListener()
        result = DownloadResult(
            url="https://example.invalid/heavy.jpg",
            status_code=200,
            size_bytes=1,
            elapsed_seconds=1.0,
        )

        listener.on_attempt_succeeded(1, 10, result)
        listener.on_attempt_failed(2, 10, "timed out")
