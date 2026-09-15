"""HTTP transport adapter: the only module in the package that opens a socket.

Implements :class:`speedmeter.ports.Downloader` on top of the standard library
so the project needs no runtime dependencies. Swapping in ``requests`` or
``httpx`` later means adding a sibling class here and changing one line in the
composition root; nothing else in the package knows how bytes arrive.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from http import HTTPStatus
from http.client import HTTPException, HTTPResponse
from typing import Final
from urllib.parse import urlparse

from speedmeter import __version__
from speedmeter.domain import DownloadResult
from speedmeter.exceptions import HttpStatusError, InvalidUrlError, TransportError
from speedmeter.ports import Downloader

DEFAULT_CHUNK_SIZE: Final = 64 * 1024
"""Bytes read per call while draining the response body.

Large enough that the loop overhead is negligible next to the socket read,
small enough that memory use stays flat no matter how heavy the target is.
"""

DEFAULT_TIMEOUT_SECONDS: Final = 30.0
"""Per-attempt timeout.

Applies to each socket operation rather than to the attempt as a whole, which
is what :func:`urllib.request.urlopen` offers. Generous enough not to abort a
slow but working connection, short enough that a dead host does not stall the
run for minutes.
"""

SUPPORTED_SCHEMES: Final = frozenset({"http", "https"})
"""Schemes this adapter accepts.

The check is not cosmetic. ``urlopen`` returns an
:class:`http.client.HTTPResponse` for HTTP and HTTPS but a different object for
``file://`` and ``ftp://``, one that has no ``status`` attribute -- so an
unvalidated scheme would turn into an ``AttributeError`` at runtime instead of
a clear error message. It also keeps a user-supplied address from reading local
files.
"""

_NO_CACHE_HEADERS: Final = {
    "Cache-Control": "no-cache, no-store",
    "Pragma": "no-cache",
}
"""Asks intermediaries not to answer from a cache.

Without this, a corporate proxy can serve attempts 2..N from local disk in
milliseconds, and the run measures the proxy rather than the connection. It
cannot force a CDN edge to re-fetch from origin, and is not meant to: an edge
hit is still a real network transfer.
"""


class HttpDownloader(Downloader):
    """Downloads a resource over HTTP(S) and times the whole transfer.

    The body is read to the end in fixed-size chunks and discarded. Only the
    byte count and the duration are kept, so memory use is constant whether the
    target is one megabyte or one gigabyte.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        user_agent: str = f"speedmeter/{__version__}",
    ) -> None:
        """Configure the transport.

        Args:
            timeout_seconds: Socket timeout for each attempt.
            chunk_size: Bytes to read per call while draining the body.
            user_agent: Value sent in the ``User-Agent`` header. The urllib
                default is rejected by some CDNs, which would show up as an
                inexplicable HTTP 403.

        Raises:
            ValueError: If the timeout or the chunk size is not positive.
        """
        if timeout_seconds <= 0:
            message = f"timeout_seconds must be positive, got {timeout_seconds}."
            raise ValueError(message)
        if chunk_size <= 0:
            message = f"chunk_size must be positive, got {chunk_size}."
            raise ValueError(message)

        self._timeout_seconds = timeout_seconds
        self._chunk_size = chunk_size
        self._user_agent = user_agent

    def download(self, url: str) -> DownloadResult:
        """Download ``url`` once and return how long it took and how big it was.

        Args:
            url: Absolute ``http://`` or ``https://`` address.

        Returns:
            The measurement for this single attempt.

        Raises:
            InvalidUrlError: If the address is malformed or uses an
                unsupported scheme. This is a usage error, not a transient
                failure, so retrying it is pointless.
            HttpStatusError: If the server answered with a non-success status.
            TransportError: If no response arrived at all.
        """
        self._reject_unusable(url)
        # S310 is suppressed at both call sites below because
        # _reject_unusable has already restricted the scheme to http/https.
        request = urllib.request.Request(  # noqa: S310
            url,
            headers={"User-Agent": self._user_agent, **_NO_CACHE_HEADERS},
            method="GET",
        )

        started_at = time.perf_counter()
        try:
            # urlopen is declared as returning Any in typeshed, which would let
            # every attribute read below flow unchecked into typed fields.
            # Naming the type here is what makes the rest of this method
            # genuinely type-checked.
            response: HTTPResponse
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:  # noqa: S310
                status_code = response.status
                if not HTTPStatus.OK <= status_code < HTTPStatus.MULTIPLE_CHOICES:
                    raise HttpStatusError(url, status_code)
                size_bytes = self._drain(response)
        except urllib.error.HTTPError as error:
            # Subclasses URLError, so it has to be caught first. urllib raises
            # this for 4xx and 5xx; a 404 page still transfers bytes, and
            # without this branch the run would measure the error page.
            raise HttpStatusError(url, error.code) from error
        except urllib.error.URLError as error:
            raise TransportError(url, str(error.reason)) from error
        except TimeoutError as error:
            # Raised directly when a read times out, as opposed to a connect
            # timeout, which arrives wrapped in URLError.
            raise TransportError(url, "timed out") from error
        except (OSError, HTTPException) as error:
            # Connection reset, malformed response, truncated body.
            raise TransportError(url, str(error) or type(error).__name__) from error
        elapsed_seconds = time.perf_counter() - started_at

        return DownloadResult(
            url=url,
            status_code=status_code,
            size_bytes=size_bytes,
            elapsed_seconds=elapsed_seconds,
        )

    def _reject_unusable(self, url: str) -> None:
        """Reject an address before any connection is attempted.

        Args:
            url: The address to check.

        Raises:
            InvalidUrlError: If the address cannot be parsed, names no host or
                uses a scheme this adapter does not handle.
        """
        try:
            parsed = urlparse(url)
        except ValueError as error:
            raise InvalidUrlError(url, f"cannot be parsed ({error})") from error

        if parsed.scheme not in SUPPORTED_SCHEMES:
            expected = " or ".join(sorted(SUPPORTED_SCHEMES))
            reason = f"unsupported scheme {parsed.scheme or '(none)'!r}, expected {expected}"
            raise InvalidUrlError(url, reason)
        if not parsed.netloc:
            raise InvalidUrlError(url, "address names no host")

    def _drain(self, response: HTTPResponse) -> int:
        """Read the whole body in chunks and return how many bytes it held.

        The payload itself is discarded on purpose. This tool is pointed at
        deliberately heavy files, and buffering one in memory would make usage
        scale with the target size to produce a number that is already known
        from the length of each chunk.

        Args:
            response: An open response, positioned at the start of the body.

        Returns:
            Total number of payload bytes read.
        """
        total_bytes = 0
        while chunk := response.read(self._chunk_size):
            total_bytes += len(chunk)
        return total_bytes
