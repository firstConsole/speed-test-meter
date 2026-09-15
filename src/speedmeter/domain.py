"""Immutable value types describing the outcome of a measurement.

This is the innermost layer. It imports nothing from the rest of the package,
performs no I/O and knows nothing about HTTP, the command line or the console.
Every derived figure is computed here so that the aggregation rules -- which
are the only real domain logic in this project -- live in exactly one place and
can be tested without touching the network.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Final

BYTES_PER_MEGABYTE: Final = 1_000_000
"""Bytes in one megabyte, SI definition (10**6).

The task asks for speed in "MB/s", which is ambiguous: it can mean the SI
megabyte (10**6 bytes) or the IEC mebibyte (2**20 bytes), and the two differ
by 4.9%.
This package reports SI, and states so in its output, because network speeds
are quoted in SI by convention -- an ISP selling "100 Mbit/s" means
100 * 10**6 bits. Using one base for both printed figures keeps them directly
comparable with each other and with the number on the tariff.
"""

BITS_PER_MEGABIT: Final = 1_000_000
"""Bits in one megabit, SI definition (10**6)."""

BITS_PER_BYTE: Final = 8
"""Bits in one byte."""

MINIMUM_SAMPLES_FOR_SPREAD: Final = 2
"""Fewest successful attempts that can exhibit any spread at all.

One download has nothing to vary against, so the spread figures are
``None`` below this count rather than zero. Zero would be indistinguishable
from the most flattering real answer -- a perfectly steady connection --
and would assert that from a single data point.
"""


@dataclass(frozen=True, slots=True)
class DownloadResult:
    """One completed download and how long it took.

    Instances are immutable: a measurement is a historical fact, and nothing
    downstream has any business editing it.

    Attributes:
        url: The address that was downloaded.
        status_code: HTTP status code returned by the server.
        size_bytes: Payload bytes actually read from the response body.
        elapsed_seconds: Wall-clock duration of the whole request, from just
            before the connection is opened until the last byte is read.
    """

    url: str
    status_code: int
    size_bytes: int
    elapsed_seconds: float

    def __post_init__(self) -> None:
        """Reject values that cannot describe a real download.

        Raises:
            ValueError: If the size is negative or the duration is not
                positive. A non-positive duration would make the speed
                calculation either infinite or meaningless.
        """
        if self.size_bytes < 0:
            message = f"size_bytes must not be negative, got {self.size_bytes}."
            raise ValueError(message)
        if self.elapsed_seconds <= 0:
            message = f"elapsed_seconds must be positive, got {self.elapsed_seconds}."
            raise ValueError(message)

    @property
    def bytes_per_second(self) -> float:
        """Return the transfer rate of this single download."""
        return self.size_bytes / self.elapsed_seconds


@dataclass(frozen=True, slots=True)
class SpeedReport:
    """Aggregated outcome of a full measurement run.

    Holds the raw per-attempt results and derives every summary figure from
    them on demand. Keeping the arithmetic next to the data it describes avoids
    an anaemic record whose numbers are computed somewhere else and can drift
    out of step with the results they claim to summarise.

    A run is reported even when some attempts failed: ``results`` holds the
    successful ones and ``failures`` the reasons for the rest. All derived
    figures describe the successful attempts only.

    Attributes:
        url: The address that was measured.
        requested_attempts: How many attempts the caller asked for.
        results: Successful attempts, in the order they were made.
        failures: Human-readable reason for each failed attempt, in order.
    """

    url: str
    requested_attempts: int
    results: tuple[DownloadResult, ...]
    failures: tuple[str, ...]

    @property
    def successful_attempts(self) -> int:
        """Return how many attempts produced a measurement."""
        return len(self.results)

    @property
    def failed_attempts(self) -> int:
        """Return how many attempts failed."""
        return len(self.failures)

    @property
    def has_measurements(self) -> bool:
        """Return whether at least one attempt succeeded.

        When this is false every derived figure below is zero, which means
        "nothing was measured" rather than "the connection is infinitely slow".
        Callers are expected to check this before presenting a speed.
        """
        return bool(self.results)

    @property
    def total_bytes(self) -> int:
        """Return the number of bytes downloaded across all successful attempts."""
        return sum(result.size_bytes for result in self.results)

    @property
    def total_seconds(self) -> float:
        """Return the total time spent on successful attempts."""
        return sum(result.elapsed_seconds for result in self.results)

    @property
    def average_seconds(self) -> float:
        """Return the mean duration of a successful attempt, or zero if there were none."""
        if not self.results:
            return 0.0
        return self.total_seconds / self.successful_attempts

    @property
    def bytes_per_second(self) -> float:
        """Return the aggregate transfer rate over the whole run.

        This is total bytes divided by total elapsed time -- deliberately not
        the mean of the per-attempt rates. The two disagree whenever the
        attempts differ in size or duration: averaging rates gives every
        attempt the same weight no matter how little data it moved, which
        flatters the result when a quick small transfer sits beside a slow
        large one. Dividing the totals weights each attempt by its duration,
        which is what "how fast is this connection" actually asks.

        Returns zero when nothing was measured; see :attr:`has_measurements`.
        """
        total_seconds = self.total_seconds
        if total_seconds <= 0:
            return 0.0
        return self.total_bytes / total_seconds

    @property
    def megabytes_per_second(self) -> float:
        """Return the aggregate transfer rate in SI megabytes per second."""
        return self.bytes_per_second / BYTES_PER_MEGABYTE

    @property
    def megabits_per_second(self) -> float:
        """Return the aggregate transfer rate in SI megabits per second."""
        return self.bytes_per_second * BITS_PER_BYTE / BITS_PER_MEGABIT

    @property
    def _attempt_megabytes_per_second(self) -> tuple[float, ...]:
        """Return the rate each individual attempt achieved, in SI megabytes per second.

        The spread figures below are built from per-attempt rates rather than
        per-attempt durations. Both would work while the server keeps returning
        the same payload, but nothing guarantees that, and durations are only
        comparable between attempts that moved the same number of bytes. Rates
        are also the unit the headline is quoted in, so the two read together.
        """
        return tuple(result.bytes_per_second / BYTES_PER_MEGABYTE for result in self.results)

    @property
    def slowest_megabytes_per_second(self) -> float:
        """Return the rate of the slowest attempt, or zero if none succeeded.

        This is the floor the connection actually reached. No average can
        answer that, and it is the figure that decides whether something
        latency-sensitive survives.
        """
        if not self.results:
            return 0.0
        return min(self._attempt_megabytes_per_second)

    @property
    def fastest_megabytes_per_second(self) -> float:
        """Return the rate of the fastest attempt, or zero if none succeeded.

        Read together with :attr:`slowest_megabytes_per_second` it bounds the
        run. A ceiling far above the rest is a reason to look closer rather
        than a diagnosis: TCP slow start, an ISP burst allowance and a caching
        proxy all produce it, and one number cannot tell them apart.
        """
        if not self.results:
            return 0.0
        return max(self._attempt_megabytes_per_second)

    @property
    def has_spread(self) -> bool:
        """Return whether enough attempts succeeded to say anything about spread."""
        return self.successful_attempts >= MINIMUM_SAMPLES_FOR_SPREAD

    @property
    def megabytes_per_second_stdev(self) -> float | None:
        """Return the sample standard deviation of the per-attempt rates, in MB/s.

        ``None`` when fewer than :data:`MINIMUM_SAMPLES_FOR_SPREAD` attempts
        succeeded. That is deliberately not zero: the sample standard deviation
        of one observation is undefined, and returning zero would claim a
        perfectly steady connection on the evidence of a single download. The
        optional type also makes a type checker reject arithmetic on the value
        until the caller has handled the missing case.

        The sample form, dividing by ``n - 1``, is used because ten attempts
        sample an ongoing connection rather than constituting its entire
        population. Read the result as "steady or not" rather than as a precise
        figure: at ten samples squaring the deviations lets one bad attempt
        supply most of the sum.
        """
        if not self.has_spread:
            return None
        return statistics.stdev(self._attempt_megabytes_per_second)

    @property
    def coefficient_of_variation(self) -> float | None:
        """Return the spread of the attempt rates as a fraction of their mean.

        A standard deviation alone cannot say whether a spread is large: half a
        megabyte per second of wobble is nothing on a 50 MB/s link and
        crippling on a 1 MB/s one. Dividing one by the other gives a unitless
        ratio that compares across connections. Multiply by 100 to show a
        percentage.

        The divisor is the plain arithmetic mean of the per-attempt rates, not
        :attr:`megabytes_per_second`. The headline weights each attempt by its
        duration, so it sinks as the attempts disagree; using it would put the
        spread into the divisor as well as the numerator and overstate the
        ratio.

        ``None`` when the spread is unavailable, and when every attempt moved
        zero bytes, which leaves nothing to take a ratio against.
        """
        stdev = self.megabytes_per_second_stdev
        if stdev is None:
            return None
        mean_rate = statistics.fmean(self._attempt_megabytes_per_second)
        if mean_rate <= 0:
            return None
        return stdev / mean_rate
