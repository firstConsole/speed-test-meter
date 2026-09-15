"""Error hierarchy for the package.

Every exception raised on purpose derives from :class:`SpeedMeterError`, so a
caller can catch the whole package with a single ``except`` clause without also
swallowing unrelated failures such as ``KeyboardInterrupt``.

Each class builds its own message in ``__init__`` rather than receiving a
formatted string at the raise site. That keeps the wording of an error in one
place and keeps raise statements short enough to read at a glance.
"""

from __future__ import annotations


class SpeedMeterError(Exception):
    """Base class for every error this package raises deliberately."""


class InvalidUrlError(SpeedMeterError):
    """Raised when the target address cannot be used for a measurement.

    This is a usage error rather than a runtime failure: it is detected before
    any request is sent, and no retry can fix it.
    """

    def __init__(self, url: str, reason: str) -> None:
        """Store the rejected address and describe why it was rejected.

        Args:
            url: The address supplied by the caller.
            reason: Short lowercase explanation, e.g. ``"unsupported scheme"``.
        """
        self.url = url
        self.reason = reason
        super().__init__(f"Invalid target URL {url!r}: {reason}.")


class DownloadError(SpeedMeterError):
    """Raised when one download attempt fails to produce a measurement.

    The runner catches this per attempt and records it, so a single failed
    request degrades the result instead of discarding the whole run. That
    matters on a real connection, where an occasional timeout is normal.
    """

    def __init__(self, url: str, reason: str) -> None:
        """Store the address that failed and describe the failure.

        Args:
            url: The address that was being downloaded.
            reason: Short lowercase explanation of what went wrong.
        """
        self.url = url
        self.reason = reason
        super().__init__(f"Failed to download {url!r}: {reason}.")


class TransportError(DownloadError):
    """Raised when the connection itself fails.

    Covers DNS resolution, TCP connection, TLS handshake and timeouts -- every
    case where no HTTP response was received at all.
    """

    def __init__(self, url: str, cause: str) -> None:
        """Store the underlying transport failure.

        Args:
            url: The address that was being downloaded.
            cause: Description of the underlying error, usually taken from the
                exception raised by the transport library.
        """
        self.cause = cause
        super().__init__(url, f"transport failure ({cause})")


class HttpStatusError(DownloadError):
    """Raised when the server answers with a non-success status code.

    A 404 page still transfers bytes, so without this check the run would
    happily measure the download speed of an error page.
    """

    def __init__(self, url: str, status_code: int) -> None:
        """Store the unsuccessful status code.

        Args:
            url: The address that was being downloaded.
            status_code: The HTTP status code returned by the server.
        """
        self.status_code = status_code
        super().__init__(url, f"server responded with HTTP {status_code}")
