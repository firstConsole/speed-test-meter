"""Tests for the error hierarchy.

The hierarchy is load-bearing rather than decorative: the runner catches
``DownloadError`` to keep a run alive after a failed attempt, and the CLI
catches ``SpeedMeterError`` to turn any deliberate failure into a clean exit
code instead of a traceback. Both rely on the relationships pinned here.
"""

from __future__ import annotations

import pytest

from speedmeter.exceptions import (
    DownloadError,
    HttpStatusError,
    InvalidUrlError,
    SpeedMeterError,
    TransportError,
)

URL = "https://example.invalid/heavy.jpg"


class TestHierarchy:
    """Who catches whom."""

    @pytest.mark.parametrize(
        "error",
        [
            InvalidUrlError(URL, "unsupported scheme"),
            DownloadError(URL, "something went wrong"),
            TransportError(URL, "timed out"),
            HttpStatusError(URL, 404),
        ],
    )
    def test_every_error_is_catchable_as_the_package_base(self, error: Exception) -> None:
        assert isinstance(error, SpeedMeterError)

    @pytest.mark.parametrize(
        "error",
        [TransportError(URL, "timed out"), HttpStatusError(URL, 404)],
    )
    def test_per_attempt_failures_are_catchable_as_download_error(self, error: Exception) -> None:
        # This is what lets one bad attempt be recorded instead of fatal.
        assert isinstance(error, DownloadError)

    def test_a_usage_error_is_not_a_download_error(self) -> None:
        # A bad URL must not be retried or recorded as a failed attempt:
        # no number of retries will fix it.
        assert not isinstance(InvalidUrlError(URL, "unsupported scheme"), DownloadError)

    def test_the_package_base_does_not_swallow_unrelated_failures(self) -> None:
        assert not issubclass(SpeedMeterError, KeyboardInterrupt)
        assert issubclass(SpeedMeterError, Exception)


class TestMessages:
    """Each class formats its own message, so raise sites stay short."""

    def test_invalid_url_names_the_address_and_the_reason(self) -> None:
        error = InvalidUrlError(URL, "unsupported scheme")

        assert str(error) == f"Invalid target URL {URL!r}: unsupported scheme."

    def test_http_status_names_the_code(self) -> None:
        error = HttpStatusError(URL, 503)

        assert str(error) == f"Failed to download {URL!r}: server responded with HTTP 503."

    def test_transport_names_the_underlying_cause(self) -> None:
        error = TransportError(URL, "timed out")

        assert str(error) == f"Failed to download {URL!r}: transport failure (timed out)."


class TestAttributes:
    """Structured detail survives, so callers need not parse the message."""

    def test_invalid_url_keeps_its_parts(self) -> None:
        error = InvalidUrlError(URL, "unsupported scheme")

        assert error.url == URL
        assert error.reason == "unsupported scheme"

    def test_http_status_keeps_the_code_as_an_integer(self) -> None:
        error = HttpStatusError(URL, 404)

        assert error.status_code == 404
        assert error.url == URL

    def test_transport_keeps_the_cause(self) -> None:
        error = TransportError(URL, "connection refused")

        assert error.cause == "connection refused"
        assert error.url == URL
