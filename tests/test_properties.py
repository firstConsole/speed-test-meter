"""Property-based tests: invariants that must hold for any run at all.

The example-based tests pin specific numbers that a specific scenario must
produce. These state relationships that hold for *every* report, and let
Hypothesis look for the counterexample. That is a different kind of check: an
example test can only fail on the case its author thought of.

Where a property involves floating-point sums, it is stated up to a relative
tolerance. Summation is not associative in floating point, so "reordering the
attempts changes nothing" is true of the arithmetic but not bit-for-bit true
of the result.
"""

from __future__ import annotations

import statistics

import pytest
from hypothesis import given
from hypothesis import strategies as st

from speedmeter.domain import DownloadResult, SpeedReport
from speedmeter.exceptions import TransportError
from speedmeter.runner import SpeedTestRunner
from tests.helpers import URL, Outcome, ScriptedDownloader, make_result

TOLERANCE = 1e-9
"""Relative slack allowed where floating-point summation order is involved."""

attempts = st.builds(
    make_result,
    size_bytes=st.integers(min_value=0, max_value=2_000_000_000),
    elapsed_seconds=st.floats(
        min_value=0.001,
        max_value=600.0,
        allow_nan=False,
        allow_infinity=False,
    ),
)
"""A single plausible successful attempt, from an empty body to two gigabytes."""


def _build_report(results: list[DownloadResult], failures: list[str]) -> SpeedReport:
    """Assemble a report whose requested count matches what it was given."""
    return SpeedReport(
        url=URL,
        requested_attempts=len(results) + len(failures),
        results=tuple(results),
        failures=tuple(failures),
    )


def reports(min_attempts: int = 0) -> st.SearchStrategy[SpeedReport]:
    """Build arbitrary reports with at least ``min_attempts`` successful attempts."""
    return st.builds(
        _build_report,
        results=st.lists(attempts, min_size=min_attempts, max_size=20),
        failures=st.lists(st.text(min_size=1, max_size=40), max_size=5),
    )


class TestAggregation:
    """Relationships between the headline figure and the attempts behind it."""

    @given(report=reports(min_attempts=1))
    def test_the_headline_lies_between_the_slowest_and_fastest_attempt(
        self, report: SpeedReport
    ) -> None:
        """The aggregate rate can never fall outside the range it summarises.

        It is a duration-weighted mean of the per-attempt rates, and a weighted
        mean of any set lies within that set's bounds. A formula that violated
        this -- for instance one that divided by the wrong total -- would be
        caught here whatever the inputs.
        """
        slack = TOLERANCE * max(report.fastest_megabytes_per_second, 1.0)

        assert report.slowest_megabytes_per_second - slack <= report.megabytes_per_second
        assert report.megabytes_per_second <= report.fastest_megabytes_per_second + slack

    @given(report=reports(min_attempts=1))
    def test_the_totals_are_the_sums_of_the_parts(self, report: SpeedReport) -> None:
        assert report.total_bytes == sum(result.size_bytes for result in report.results)
        assert report.total_seconds == pytest.approx(
            sum(result.elapsed_seconds for result in report.results)
        )

    @given(report=reports(min_attempts=1))
    def test_the_average_duration_lies_between_the_extremes(self, report: SpeedReport) -> None:
        durations = [result.elapsed_seconds for result in report.results]

        assert min(durations) <= report.average_seconds * (1 + TOLERANCE)
        assert report.average_seconds <= max(durations) * (1 + TOLERANCE)

    @given(report=reports())
    def test_megabits_are_eight_times_megabytes(self, report: SpeedReport) -> None:
        # Both constants are SI, so the ratio is exactly the number of bits in
        # a byte. Mixing the IEC base into one of them would break this.
        assert report.megabits_per_second == pytest.approx(report.megabytes_per_second * 8)

    @given(data=st.data(), report=reports(min_attempts=2))
    def test_reordering_the_attempts_changes_nothing(
        self, data: st.DataObject, report: SpeedReport
    ) -> None:
        """Every figure describes the set of attempts, not their sequence.

        A measure that moved when the same attempts arrived in a different
        order would be reporting something about the ordering, which none of
        these figures claims to do.
        """
        shuffled = _build_report(
            results=data.draw(st.permutations(report.results)),
            failures=list(report.failures),
        )

        assert shuffled.total_bytes == report.total_bytes
        assert shuffled.megabytes_per_second == pytest.approx(report.megabytes_per_second, rel=1e-6)
        assert shuffled.slowest_megabytes_per_second == pytest.approx(
            report.slowest_megabytes_per_second
        )
        assert shuffled.fastest_megabytes_per_second == pytest.approx(
            report.fastest_megabytes_per_second
        )


class TestSpread:
    """Invariants of the consistency figures."""

    @given(report=reports())
    def test_is_available_exactly_when_two_attempts_succeeded(self, report: SpeedReport) -> None:
        # The gate and the value must never disagree: a None slipping through
        # a true has_spread would crash a reporter that trusted the predicate.
        expected = report.successful_attempts >= 2

        assert report.has_spread is expected
        assert (report.megabytes_per_second_stdev is not None) is expected

    @given(report=reports(min_attempts=2))
    def test_is_never_negative(self, report: SpeedReport) -> None:
        stdev = report.megabytes_per_second_stdev
        variation = report.coefficient_of_variation

        assert stdev is not None
        assert stdev >= 0.0
        assert variation is None or variation >= 0.0

    @given(report=reports(min_attempts=2))
    def test_never_exceeds_the_range_it_describes(self, report: SpeedReport) -> None:
        """A deviation wider than the whole spread of the data is impossible.

        The sample form is bounded by the range times a factor that is largest
        at two samples, where it is 1/sqrt(2). Comparing against the plain
        range is therefore a safe bound at every sample count, and it catches
        an off-by-one in the divisor.
        """
        stdev = report.megabytes_per_second_stdev
        observed_range = report.fastest_megabytes_per_second - report.slowest_megabytes_per_second

        assert stdev is not None
        assert stdev <= observed_range * (1 + TOLERANCE)

    @given(
        attempt=attempts,
        count=st.integers(min_value=2, max_value=15),
    )
    def test_identical_attempts_have_exactly_zero_spread(
        self, attempt: DownloadResult, count: int
    ) -> None:
        """Repeating one attempt must give exactly 0.0, not a small residue.

        A variance computed as the mean of squares minus the square of the
        mean leaves floating-point debris here, which would print as a phantom
        spread on a perfectly steady connection.
        """
        report = _build_report(results=[attempt] * count, failures=[])

        assert report.megabytes_per_second_stdev == 0.0
        assert report.coefficient_of_variation in (0.0, None)

    @given(report=reports(min_attempts=2))
    def test_the_coefficient_is_the_deviation_over_the_mean_of_the_rates(
        self, report: SpeedReport
    ) -> None:
        rates = [result.bytes_per_second / 1_000_000 for result in report.results]
        mean_rate = statistics.fmean(rates)
        variation = report.coefficient_of_variation

        if mean_rate <= 0:
            assert variation is None
        else:
            assert variation == pytest.approx(statistics.stdev(rates) / mean_rate)


class TestRunner:
    """Invariants of a whole run, whatever the connection does."""

    @given(
        script=st.lists(
            st.one_of(
                attempts,
                st.builds(TransportError, url=st.just(URL), cause=st.text(min_size=1, max_size=20)),
            ),
            min_size=1,
            max_size=15,
        )
    )
    def test_every_attempt_is_accounted_for(self, script: list[Outcome]) -> None:
        """No attempt may be silently dropped, whatever mixture of outcomes occurs.

        Successes plus failures must equal the number of attempts requested --
        an off-by-one in the loop, or a swallowed exception, shows up here for
        any arrangement of good and bad attempts rather than only the ones an
        example test happened to script.
        """
        requested = len(script)

        report = SpeedTestRunner(ScriptedDownloader(script)).run(URL, attempts=requested)

        assert report.successful_attempts + report.failed_attempts == requested
        assert report.requested_attempts == requested
