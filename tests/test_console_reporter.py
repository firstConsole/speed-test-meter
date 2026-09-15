"""Tests for the console adapters.

Most of these are about honesty rather than layout. A reporter that prints
"0.00 MB/s" where it has no measurement, or a variation of "0.00" where it has
only one sample, produces output that looks entirely plausible and says
something false -- which is the failure mode worth guarding.
"""

from __future__ import annotations

import io

import pytest

from speedmeter.adapters.console import ConsoleProgressListener, ConsoleReporter
from speedmeter.domain import DownloadResult, SpeedReport
from speedmeter.ports import ProgressListener, Reporter
from tests.helpers import URL, make_report, make_result, report_with_rates


def render(report: SpeedReport) -> str:
    """Render a report to a string."""
    stream = io.StringIO()
    ConsoleReporter(stream).report(report)
    return stream.getvalue()


class TestPortConformance:
    """The adapters are usable wherever the ports are declared."""

    def test_the_reporter_implements_its_port(self) -> None:
        assert isinstance(ConsoleReporter(io.StringIO()), Reporter)

    def test_the_progress_listener_implements_its_port(self) -> None:
        assert isinstance(ConsoleProgressListener(io.StringIO()), ProgressListener)


class TestSuccessfulRun:
    """What a complete run shows."""

    @pytest.fixture
    def output(self) -> str:
        return render(report_with_rates(4.80, 4.52, 5.01, 4.78, 4.83))

    def test_names_the_target(self, output: str) -> None:
        assert URL in output

    def test_shows_how_many_attempts_succeeded(self, output: str) -> None:
        assert "5 of 5 succeeded" in output

    def test_shows_the_three_figures_the_task_asks_for(self, output: str) -> None:
        # Volume downloaded, average request time, speed.
        assert "Downloaded:" in output
        assert "MB" in output
        assert "Average request:" in output
        assert " s" in output
        assert "Speed:" in output
        assert "MB/s" in output

    def test_shows_both_units(self, output: str) -> None:
        assert "MB/s" in output
        assert "Mbit/s" in output

    def test_states_the_unit_base(self, output: str) -> None:
        # "MB/s" is ambiguous between 10^6 and 2^20; saying which removes a
        # 4.9% misreading for free.
        assert "10^6 bytes" in output

    def test_shows_the_extremes_and_the_variation(self, output: str) -> None:
        assert "Slowest attempt:" in output
        assert "Fastest attempt:" in output
        assert "Variation:" in output
        assert "% of the mean attempt" in output

    def test_says_nothing_about_failures(self, output: str) -> None:
        assert "Failed attempts" not in output


class TestHonestyAboutMissingFigures:
    """Where a figure does not exist, the output must not invent one."""

    def test_an_unmeasurable_variation_is_not_printed_as_zero(self) -> None:
        """One attempt has no spread, and 0.00 would read as perfect stability.

        This is the whole reason the domain returns None here rather than a
        float.
        """
        output = render(report_with_rates(4.8))

        assert "n/a" in output
        assert "needs at least two successful attempts" in output
        assert "+/- 0.00" not in output

    def test_a_run_where_everything_failed_reports_no_speed_at_all(self) -> None:
        """Zero megabytes per second would describe a working but glacial link.

        The truthful statement is that nothing was measured, so no speed line
        is printed at all.
        """
        output = render(make_report(failures=("timed out", "timed out")))

        assert "Every attempt failed" in output
        assert "MB/s" not in output
        assert "0.00" not in output

    def test_lists_the_reason_for_every_failure(self) -> None:
        output = render(make_report(make_result(), failures=("timed out", "connection refused")))

        assert "Failed attempts (2):" in output
        assert "timed out" in output
        assert "connection refused" in output


class TestOutputDestination:
    """Where each stream goes, which is what makes redirection work."""

    def test_the_report_goes_to_the_given_stream(self) -> None:
        stream = io.StringIO()

        ConsoleReporter(stream).report(report_with_rates(4.8, 4.9))

        assert stream.getvalue() != ""

    def test_the_report_defaults_to_stdout(self, capsys: pytest.CaptureFixture[str]) -> None:
        ConsoleReporter().report(report_with_rates(4.8, 4.9))

        captured = capsys.readouterr()
        assert "Speed:" in captured.out
        assert captured.err == ""

    def test_progress_defaults_to_stderr(self, capsys: pytest.CaptureFixture[str]) -> None:
        # Keeps progress off the pipe, so that redirecting the report to a file
        # yields a file containing only the report.
        ConsoleProgressListener().on_attempt_failed(1, 10, "timed out")

        captured = capsys.readouterr()
        assert captured.out == ""
        assert "timed out" in captured.err


class TestProgressLines:
    """What the user sees while the run is still going."""

    @staticmethod
    def announce_success(number: int, total: int) -> str:
        stream = io.StringIO()
        result = DownloadResult(
            url=URL, status_code=200, size_bytes=4_720_000, elapsed_seconds=0.983
        )
        ConsoleProgressListener(stream).on_attempt_succeeded(number, total, result)
        return stream.getvalue()

    def test_shows_the_attempt_number_and_the_total(self) -> None:
        assert self.announce_success(3, 10).startswith("[ 3/10]")

    def test_pads_the_number_so_the_lines_stay_aligned(self) -> None:
        # Without padding the column jumps when the run reaches ten.
        assert len(self.announce_success(1, 10).split("]")[0]) == len(
            self.announce_success(10, 10).split("]")[0]
        )

    def test_shows_what_the_attempt_measured(self) -> None:
        line = self.announce_success(1, 10)

        assert "MB/s" in line
        assert "4.72 MB" in line
        assert "0.983 s" in line

    def test_shows_why_an_attempt_failed(self) -> None:
        stream = io.StringIO()

        ConsoleProgressListener(stream).on_attempt_failed(2, 10, "connection refused")

        assert "failed" in stream.getvalue()
        assert "connection refused" in stream.getvalue()
