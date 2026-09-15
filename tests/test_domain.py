"""Tests for the domain layer: value objects and the figures derived from them."""

from __future__ import annotations

import dataclasses

import pytest

from speedmeter.domain import (
    BITS_PER_BYTE,
    BITS_PER_MEGABIT,
    BYTES_PER_MEGABYTE,
    DownloadResult,
    SpeedReport,
)

URL = "https://example.invalid/heavy.jpg"


def make_result(size_bytes: int = 1_000_000, elapsed_seconds: float = 1.0) -> DownloadResult:
    """Build a valid result, overriding only what a test cares about."""
    return DownloadResult(
        url=URL,
        status_code=200,
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


class TestDownloadResult:
    """A single measured download."""

    def test_reports_its_own_transfer_rate(self) -> None:
        result = make_result(size_bytes=2_000_000, elapsed_seconds=4.0)

        assert result.bytes_per_second == pytest.approx(500_000.0)

    def test_is_immutable(self) -> None:
        result = make_result()

        with pytest.raises(dataclasses.FrozenInstanceError):
            result.size_bytes = 5  # type: ignore[misc]

    def test_accepts_an_empty_response_body(self) -> None:
        # A zero-byte body is a real answer from a server, not a broken result.
        assert make_result(size_bytes=0).bytes_per_second == 0.0

    def test_rejects_a_negative_size(self) -> None:
        with pytest.raises(ValueError, match="size_bytes must not be negative"):
            make_result(size_bytes=-1)

    @pytest.mark.parametrize("elapsed_seconds", [0.0, -0.5])
    def test_rejects_a_non_positive_duration(self, elapsed_seconds: float) -> None:
        # Guards the speed calculation: a zero duration would divide by zero,
        # and a negative one would silently produce a negative speed.
        with pytest.raises(ValueError, match="elapsed_seconds must be positive"):
            make_result(elapsed_seconds=elapsed_seconds)


class TestSpeedReportCounters:
    """How a report describes the shape of the run."""

    def test_counts_successes_and_failures_separately(self) -> None:
        report = make_report(make_result(), make_result(), failures=("timed out",))

        assert report.successful_attempts == 2
        assert report.failed_attempts == 1
        assert report.requested_attempts == 3

    def test_knows_it_has_measurements(self) -> None:
        assert make_report(make_result()).has_measurements is True

    def test_knows_it_has_none(self) -> None:
        assert make_report(failures=("timed out",)).has_measurements is False


class TestSpeedReportTotals:
    """Sums and averages over the successful attempts."""

    def test_sums_bytes_and_seconds(self) -> None:
        report = make_report(
            make_result(size_bytes=1_000, elapsed_seconds=0.5),
            make_result(size_bytes=3_000, elapsed_seconds=1.5),
        )

        assert report.total_bytes == 4_000
        assert report.total_seconds == pytest.approx(2.0)

    def test_averages_the_duration_of_successful_attempts(self) -> None:
        report = make_report(
            make_result(elapsed_seconds=1.0),
            make_result(elapsed_seconds=2.0),
            failures=("timed out",),
        )

        # The failed attempt must not dilute the average: 3.0 / 2, not / 3.
        assert report.average_seconds == pytest.approx(1.5)


class TestSpeedReportSpeed:
    """The aggregation rule and the units it is reported in."""

    def test_divides_total_bytes_by_total_time(self) -> None:
        report = make_report(
            make_result(size_bytes=1_000_000, elapsed_seconds=1.0),
            make_result(size_bytes=3_000_000, elapsed_seconds=1.0),
        )

        assert report.bytes_per_second == pytest.approx(2_000_000.0)

    def test_is_not_the_mean_of_per_attempt_rates(self) -> None:
        """Pin the aggregation rule so it cannot be "simplified" into a mean.

        A large slow transfer beside a small fast one is where the two formulas
        disagree most, because averaging rates gives the 1 KB attempt the same
        weight as the 10 MB one.
        """
        large_and_slow = make_result(size_bytes=10_000_000, elapsed_seconds=2.0)
        small_and_fast = make_result(size_bytes=1_000, elapsed_seconds=0.001)
        report = make_report(large_and_slow, small_and_fast)

        mean_of_rates = (large_and_slow.bytes_per_second + small_and_fast.bytes_per_second) / 2

        assert report.bytes_per_second == pytest.approx(10_001_000 / 2.001)
        assert mean_of_rates == pytest.approx(3_000_000.0)
        assert report.bytes_per_second > mean_of_rates

    def test_converts_to_si_megabytes(self) -> None:
        report = make_report(make_result(size_bytes=BYTES_PER_MEGABYTE, elapsed_seconds=1.0))

        assert report.megabytes_per_second == pytest.approx(1.0)

    def test_converts_to_si_megabits(self) -> None:
        report = make_report(make_result(size_bytes=BYTES_PER_MEGABYTE, elapsed_seconds=1.0))

        # One SI megabyte per second is exactly eight SI megabits per second.
        assert report.megabits_per_second == pytest.approx(8.0)

    def test_units_use_the_si_base(self) -> None:
        # Pinned because the IEC base (2**20) would make every printed figure
        # 4.9% smaller while looking equally plausible.
        assert BYTES_PER_MEGABYTE == 1_000_000
        assert BITS_PER_MEGABIT == 1_000_000
        assert BITS_PER_BYTE == 8


class TestEmptySpeedReport:
    """A run in which every attempt failed must still be presentable."""

    @pytest.fixture
    def report(self) -> SpeedReport:
        return make_report(failures=("timed out", "connection refused"))

    def test_reports_zero_instead_of_dividing_by_zero(self, report: SpeedReport) -> None:
        assert report.total_bytes == 0
        assert report.total_seconds == 0.0
        assert report.average_seconds == 0.0
        assert report.bytes_per_second == 0.0
        assert report.megabytes_per_second == 0.0
        assert report.megabits_per_second == 0.0

    def test_keeps_the_failure_reasons(self, report: SpeedReport) -> None:
        assert report.failures == ("timed out", "connection refused")
