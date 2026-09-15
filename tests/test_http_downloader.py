"""Tests for the HTTP adapter, exercised against a real local server.

A loopback server is used rather than a mocked ``urlopen`` on purpose: the
things most likely to break here -- exception translation, chunked reading,
which headers actually reach the wire -- are precisely the things a mock would
have to assume rather than demonstrate.
"""

from __future__ import annotations

import tracemalloc
from typing import TYPE_CHECKING

import pytest

from speedmeter.adapters.http import HttpDownloader
from speedmeter.exceptions import HttpStatusError, InvalidUrlError, TransportError
from speedmeter.ports import Downloader
from tests.helpers import SERVER_PAYLOAD_SIZE

if TYPE_CHECKING:
    from tests.helpers import LocalServer

pytestmark = pytest.mark.network


@pytest.fixture
def downloader() -> HttpDownloader:
    return HttpDownloader(timeout_seconds=10.0)


class TestSuccessfulDownload:
    """The happy path, which is what the measurement is built on."""

    def test_implements_the_port(self, downloader: HttpDownloader) -> None:
        assert isinstance(downloader, Downloader)

    def test_counts_every_byte_of_the_body(
        self, downloader: HttpDownloader, server: LocalServer
    ) -> None:
        result = downloader.download(server.url("/heavy.jpg"))

        assert result.size_bytes == SERVER_PAYLOAD_SIZE

    def test_reports_the_status_and_the_url(
        self, downloader: HttpDownloader, server: LocalServer
    ) -> None:
        url = server.url("/heavy.jpg")

        result = downloader.download(url)

        assert result.status_code == 200
        assert result.url == url

    def test_measures_a_positive_duration(
        self, downloader: HttpDownloader, server: LocalServer
    ) -> None:
        # DownloadResult rejects a non-positive duration, so this also proves
        # the timer is not accidentally reset after the transfer.
        assert downloader.download(server.url("/heavy.jpg")).elapsed_seconds > 0

    def test_accepts_an_empty_body(self, downloader: HttpDownloader, server: LocalServer) -> None:
        assert downloader.download(server.url("/empty")).size_bytes == 0

    @pytest.mark.parametrize("chunk_size", [1, 512, 64 * 1024, SERVER_PAYLOAD_SIZE * 2])
    def test_chunk_size_does_not_change_the_byte_count(
        self, server: LocalServer, chunk_size: int
    ) -> None:
        # Covers the boundaries where a chunked read loop usually goes wrong:
        # one byte at a time, and a buffer larger than the whole body.
        downloader = HttpDownloader(chunk_size=chunk_size, timeout_seconds=10.0)

        assert downloader.download(server.url("/heavy.jpg")).size_bytes == SERVER_PAYLOAD_SIZE


class TestRequestHeaders:
    """What actually reaches the wire."""

    def test_sends_a_custom_user_agent(self, server: LocalServer) -> None:
        # The urllib default is rejected by some CDNs with a 403 that looks
        # inexplicable, so this is worth pinning.
        HttpDownloader(user_agent="probe/1.0", timeout_seconds=10.0).download(
            server.url("/heavy.jpg")
        )

        assert server.requests[-1].get("User-Agent") == "probe/1.0"

    def test_asks_intermediaries_not_to_serve_from_cache(
        self, downloader: HttpDownloader, server: LocalServer
    ) -> None:
        downloader.download(server.url("/heavy.jpg"))

        headers = server.requests[-1]
        assert headers.get("Cache-Control") == "no-cache, no-store"
        assert headers.get("Pragma") == "no-cache"


class TestMemoryUse:
    """The body is discarded as it arrives rather than buffered."""

    def test_peak_memory_is_far_below_the_payload_size(self, server: LocalServer) -> None:
        downloader = HttpDownloader(chunk_size=64 * 1024, timeout_seconds=10.0)

        tracemalloc.start()
        try:
            downloader.download(server.url("/heavy.jpg"))
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        # A buffering implementation would peak at or above SERVER_PAYLOAD_SIZE.
        # The bound is deliberately loose -- an order of magnitude below the
        # payload -- so this fails on a real regression, not on allocator noise.
        assert peak < SERVER_PAYLOAD_SIZE // 10


class TestHttpFailures:
    """A response arrived, but not one that can be measured.

    These also guard against a socket leak, without asserting on it directly.
    ``urllib`` raises ``HTTPError``, which is itself a response object holding
    an open connection, and hands it over unclosed. Because the suite runs with
    ``filterwarnings = ["error"]``, a leaked connection becomes a
    ``ResourceWarning`` raised at collection time by the garbage collector.

    Note the shape of that failure: the individual tests still report as passed
    and the summary line still reads "N passed", but the session ends with a
    non-zero exit code. CI catches it; a human skimming the summary does not.
    """

    @pytest.mark.parametrize(("path", "expected_code"), [("/missing", 404), ("/broken", 500)])
    def test_translates_an_error_status(
        self, downloader: HttpDownloader, server: LocalServer, path: str, expected_code: int
    ) -> None:
        # Without this an error page would be measured as a successful
        # download: it transfers bytes just like any other response.
        with pytest.raises(HttpStatusError) as caught:
            downloader.download(server.url(path))

        assert caught.value.status_code == expected_code


class TestTransportFailures:
    """No response arrived at all."""

    def test_translates_a_refused_connection(self, downloader: HttpDownloader) -> None:
        # Port 1 is reserved and nothing listens on it.
        with pytest.raises(TransportError):
            downloader.download("http://127.0.0.1:1/heavy.jpg")

    def test_translates_an_unresolvable_host(self, downloader: HttpDownloader) -> None:
        # .invalid is reserved by RFC 2606 and can never resolve.
        with pytest.raises(TransportError):
            downloader.download("http://nonexistent.invalid/heavy.jpg")


class TestUrlValidation:
    """Rejected before a socket is opened, because no retry would help."""

    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "ftp://example.com/heavy.jpg",
            "not-a-url",
            "",
        ],
    )
    def test_rejects_an_unsupported_scheme(self, downloader: HttpDownloader, url: str) -> None:
        # file:// matters twice over: urlopen would return an object with no
        # .status, and it would read a local file.
        with pytest.raises(InvalidUrlError, match="scheme"):
            downloader.download(url)

    def test_rejects_an_address_without_a_host(self, downloader: HttpDownloader) -> None:
        with pytest.raises(InvalidUrlError, match="no host"):
            downloader.download("http://")


class TestConstructorValidation:
    """Nonsense settings fail at construction, not mid-run."""

    @pytest.mark.parametrize("timeout_seconds", [0.0, -1.0])
    def test_rejects_a_non_positive_timeout(self, timeout_seconds: float) -> None:
        with pytest.raises(ValueError, match="timeout_seconds must be positive"):
            HttpDownloader(timeout_seconds=timeout_seconds)

    @pytest.mark.parametrize("chunk_size", [0, -1])
    def test_rejects_a_non_positive_chunk_size(self, chunk_size: int) -> None:
        with pytest.raises(ValueError, match="chunk_size must be positive"):
            HttpDownloader(chunk_size=chunk_size)
