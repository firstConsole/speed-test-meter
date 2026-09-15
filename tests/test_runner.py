"""Tests for the use case.

Not one of these opens a socket. The runner holds a Downloader rather than a
transport, so a scripted double covers cases a real server could not produce on
demand -- a timeout on exactly the fourth attempt, or a run where every single
request fails.
"""

from __future__ import annotations

import pytest

from speedmeter.exceptions import HttpStatusError, InvalidUrlError, TransportError
from speedmeter.runner import DEFAULT_ATTEMPTS, SpeedTestRunner
from tests.helpers import (
    URL,
    Outcome,
    RecordingProgressListener,
    ScriptedDownloader,
    make_result,
    successes,
)


class TestAttemptCount:
    """How many requests actually go out."""

    def test_defaults_to_ten_attempts(self) -> None:
        # The task specifies ten. Pinned so it cannot drift silently.
        assert DEFAULT_ATTEMPTS == 10

        downloader = ScriptedDownloader(successes(10))
        SpeedTestRunner(downloader).run(URL)

        assert downloader.call_count == 10

    @pytest.mark.parametrize("attempts", [1, 3, 25])
    def test_performs_exactly_the_requested_number(self, attempts: int) -> None:
        downloader = ScriptedDownloader(successes(attempts))

        SpeedTestRunner(downloader).run(URL, attempts=attempts)

        assert downloader.call_count == attempts

    def test_asks_for_the_given_address_every_time(self) -> None:
        downloader = ScriptedDownloader(successes(3))

        SpeedTestRunner(downloader).run(URL, attempts=3)

        assert downloader.requested_urls == [URL] * 3


class TestSuccessfulRun:
    """A run in which nothing goes wrong."""

    def test_collects_the_results_in_order(self) -> None:
        script: list[Outcome] = [make_result(size_bytes=size) for size in (100, 200, 300)]
        downloader = ScriptedDownloader(list(script))

        report = SpeedTestRunner(downloader).run(URL, attempts=3)

        assert [result.size_bytes for result in report.results] == [100, 200, 300]

    def test_reports_the_address_and_the_requested_count(self) -> None:
        report = SpeedTestRunner(ScriptedDownloader(successes(4))).run(URL, attempts=4)

        assert report.url == URL
        assert report.requested_attempts == 4

    def test_the_report_aggregates_what_was_collected(self) -> None:
        script: list[Outcome] = [
            make_result(size_bytes=1_000_000, elapsed_seconds=0.5) for _ in range(2)
        ]
        downloader = ScriptedDownloader(script)

        report = SpeedTestRunner(downloader).run(URL, attempts=2)

        assert report.total_bytes == 2_000_000
        assert report.bytes_per_second == pytest.approx(2_000_000.0)


class TestFailureHandling:
    """One bad attempt must not discard the good ones."""

    def test_a_failed_attempt_does_not_stop_the_run(self) -> None:
        script: list[Outcome] = [make_result(), TransportError(URL, "timed out"), make_result()]
        downloader = ScriptedDownloader(script)

        report = SpeedTestRunner(downloader).run(URL, attempts=3)

        assert downloader.call_count == 3
        assert report.successful_attempts == 2
        assert report.failed_attempts == 1

    def test_records_the_reason_for_each_failure(self) -> None:
        script: list[Outcome] = [
            TransportError(URL, "timed out"),
            HttpStatusError(URL, 503),
            make_result(),
        ]

        report = SpeedTestRunner(ScriptedDownloader(script)).run(URL, attempts=3)

        assert "timed out" in report.failures[0]
        assert "503" in report.failures[1]

    def test_a_run_where_everything_fails_still_returns_a_report(self) -> None:
        # Raising here would leave the caller with nothing to explain to the
        # user, when "all ten attempts timed out" is the useful answer.
        script: list[Outcome] = [TransportError(URL, "timed out") for _ in range(10)]

        report = SpeedTestRunner(ScriptedDownloader(script)).run(URL)

        assert report.has_measurements is False
        assert report.failed_attempts == 10


class TestProgressNotifications:
    """What the listener is told, and when."""

    def test_announces_every_attempt_exactly_once(self) -> None:
        listener = RecordingProgressListener()

        SpeedTestRunner(ScriptedDownloader(successes(5)), progress=listener).run(URL, attempts=5)

        assert len(listener.events) == 5
        assert all(event.kind == "succeeded" for event in listener.events)

    def test_distinguishes_failures_from_successes(self) -> None:
        script: list[Outcome] = [make_result(), TransportError(URL, "timed out")]
        listener = RecordingProgressListener()

        SpeedTestRunner(ScriptedDownloader(script), progress=listener).run(URL, attempts=2)

        assert [event.kind for event in listener.events] == ["succeeded", "failed"]

    def test_numbering_is_continuous_across_successes_and_failures(self) -> None:
        """Attempts are numbered 1..N overall, not per outcome.

        Separate counters would show the user "attempt 1 of 5" twice, which is
        the kind of defect that survives review because the output still looks
        plausible.
        """
        script: list[Outcome] = [
            make_result(),
            TransportError(URL, "timed out"),
            make_result(),
            TransportError(URL, "timed out"),
            make_result(),
        ]
        listener = RecordingProgressListener()

        SpeedTestRunner(ScriptedDownloader(script), progress=listener).run(URL, attempts=5)

        assert [event.number for event in listener.events] == [1, 2, 3, 4, 5]
        assert {event.total for event in listener.events} == {5}

    def test_runs_without_a_listener(self) -> None:
        # The Null Object default is what lets the runner call the port
        # unconditionally; if it were missing this would raise AttributeError.
        report = SpeedTestRunner(ScriptedDownloader(successes(2))).run(URL, attempts=2)

        assert report.successful_attempts == 2


class TestWarmUp:
    """Discarded attempts that absorb DNS resolution and the TLS handshake."""

    def test_is_off_by_default(self) -> None:
        # Guarantees the default behaviour is exactly the ten requests asked
        # for, with nothing extra going over the wire.
        downloader = ScriptedDownloader(successes(10))

        SpeedTestRunner(downloader).run(URL)

        assert downloader.call_count == 10

    def test_performs_the_extra_attempts(self) -> None:
        downloader = ScriptedDownloader(successes(12))

        SpeedTestRunner(downloader, warmup_attempts=2).run(URL, attempts=10)

        assert downloader.call_count == 12

    def test_excludes_them_from_the_report(self) -> None:
        downloader = ScriptedDownloader(successes(12))

        report = SpeedTestRunner(downloader, warmup_attempts=2).run(URL, attempts=10)

        assert report.successful_attempts == 10

    def test_happens_before_the_measured_attempts(self) -> None:
        # The warm-up must consume the slow first outcome, leaving the fast one
        # to be measured -- not the other way round.
        script: list[Outcome] = [make_result(elapsed_seconds=9.0), make_result(elapsed_seconds=1.0)]
        downloader = ScriptedDownloader(script)

        report = SpeedTestRunner(downloader, warmup_attempts=1).run(URL, attempts=1)

        assert report.results[0].elapsed_seconds == pytest.approx(1.0)

    def test_a_failing_warm_up_does_not_abort_the_run(self) -> None:
        # If the host is genuinely unreachable the measured attempts will say
        # so; a warm-up failure adds nothing but noise.
        script: list[Outcome] = [TransportError(URL, "timed out"), make_result()]

        report = SpeedTestRunner(ScriptedDownloader(script), warmup_attempts=1).run(URL, attempts=1)

        assert report.successful_attempts == 1
        assert report.failed_attempts == 0

    def test_does_not_announce_warm_up_attempts(self) -> None:
        listener = RecordingProgressListener()
        downloader = ScriptedDownloader(successes(3))

        SpeedTestRunner(downloader, warmup_attempts=2, progress=listener).run(URL, attempts=1)

        assert len(listener.events) == 1


class TestUsageErrors:
    """Mistakes that no retry can fix."""

    def test_an_invalid_address_is_not_retried(self) -> None:
        downloader = ScriptedDownloader([InvalidUrlError(URL, "unsupported scheme")])

        with pytest.raises(InvalidUrlError):
            SpeedTestRunner(downloader).run(URL)

        # One call, not ten identical failures.
        assert downloader.call_count == 1

    @pytest.mark.parametrize("attempts", [0, -1])
    def test_rejects_a_non_positive_attempt_count(self, attempts: int) -> None:
        downloader = ScriptedDownloader([])

        with pytest.raises(ValueError, match="attempts must be at least 1"):
            SpeedTestRunner(downloader).run(URL, attempts=attempts)

        assert downloader.call_count == 0

    def test_rejects_a_negative_warm_up_count(self) -> None:
        with pytest.raises(ValueError, match="warmup_attempts must not be negative"):
            SpeedTestRunner(ScriptedDownloader([]), warmup_attempts=-1)
