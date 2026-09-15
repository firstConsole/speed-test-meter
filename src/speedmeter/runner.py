"""The use case: measure a connection by downloading one address repeatedly.

This module orchestrates the run and nothing else. It never opens a socket, it
never writes to a stream, and it does not know that either exists -- it holds a
:class:`speedmeter.ports.Downloader` and a
:class:`speedmeter.ports.ProgressListener` and calls them. That is what lets
the whole use case be tested without a network.
"""

from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING, Final

from speedmeter.domain import DownloadResult, SpeedReport
from speedmeter.exceptions import DownloadError
from speedmeter.ports import NullProgressListener

if TYPE_CHECKING:
    from speedmeter.ports import Downloader, ProgressListener

DEFAULT_ATTEMPTS: Final = 10
"""Number of attempts a run makes unless told otherwise."""


class SpeedTestRunner:
    """Runs a measurement as a series of sequential downloads.

    Sequential is a deliberate property, not an implementation detail. Parallel
    requests would share the same link and starve each other, so each one would
    measure a fraction of the bandwidth while the wall-clock total measured
    something else again. Running them one at a time means every attempt has
    the whole connection to itself and the numbers are comparable.
    """

    def __init__(
        self,
        downloader: Downloader,
        *,
        progress: ProgressListener | None = None,
        warmup_attempts: int = 0,
    ) -> None:
        """Assemble the runner from the collaborators it needs.

        Args:
            downloader: How a single attempt is performed.
            progress: Notified as attempts finish. Defaults to a listener that
                discards the notifications, so the runner can always call it.
            warmup_attempts: Attempts to perform and discard before measuring.
                The first request to a host pays for DNS resolution and, over
                HTTPS, a TLS handshake, which inflates it by an amount that has
                nothing to do with bandwidth. Zero by default, so the default
                behaviour is exactly the ten requests the task asks for.

        Raises:
            ValueError: If ``warmup_attempts`` is negative.
        """
        if warmup_attempts < 0:
            message = f"warmup_attempts must not be negative, got {warmup_attempts}."
            raise ValueError(message)

        self._downloader = downloader
        self._progress = progress if progress is not None else NullProgressListener()
        self._warmup_attempts = warmup_attempts

    def run(self, url: str, attempts: int = DEFAULT_ATTEMPTS) -> SpeedReport:
        """Download ``url`` ``attempts`` times in a row and summarise the result.

        A failed attempt is recorded and the run continues. On a real
        connection an occasional timeout is ordinary, and discarding nine good
        measurements because of one bad one would make the tool useless
        precisely when the connection is worth measuring.

        Args:
            url: Address to measure.
            attempts: How many times to download it.

        Returns:
            The aggregated report, including the reason for each failure.

        Raises:
            ValueError: If ``attempts`` is less than one.
            InvalidUrlError: If the address is unusable. This is deliberately
                not caught: no number of retries will fix a malformed address,
                so failing on the first attempt is both faster and clearer than
                reporting ten identical failures.
        """
        if attempts < 1:
            message = f"attempts must be at least 1, got {attempts}."
            raise ValueError(message)

        self._warm_up(url)

        results: list[DownloadResult] = []
        failures: list[str] = []
        for number in range(1, attempts + 1):
            try:
                result = self._downloader.download(url)
            # PERF203 warns about try/except inside a loop. Per-attempt
            # isolation is the entire point here, and the setup cost is
            # nothing next to a network request -- on 3.11+ it is zero.
            except DownloadError as error:  # noqa: PERF203
                reason = str(error)
                failures.append(reason)
                self._progress.on_attempt_failed(number, attempts, reason)
            else:
                results.append(result)
                self._progress.on_attempt_succeeded(number, attempts, result)

        return SpeedReport(
            url=url,
            requested_attempts=attempts,
            results=tuple(results),
            failures=tuple(failures),
        )

    def _warm_up(self, url: str) -> None:
        """Perform and discard the configured warm-up attempts.

        A warm-up that fails is ignored rather than reported: if the host is
        genuinely unreachable the measured attempts will say so, and a warm-up
        failure adds nothing but noise.

        Args:
            url: Address to fetch and discard.
        """
        for _ in range(self._warmup_attempts):
            with suppress(DownloadError):
                self._downloader.download(url)
