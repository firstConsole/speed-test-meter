"""Console adapters: a human-readable report and a live progress line.

Progress is written to standard error and the report to standard output. That
separation is what lets the output be redirected -- ``speedmeter URL > out``
keeps the progress visible on the terminal while the file receives only the
result, and it is what makes the machine-readable reporter pipeable at all.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Final

from speedmeter.domain import BYTES_PER_MEGABYTE
from speedmeter.ports import ProgressListener, Reporter

if TYPE_CHECKING:
    from typing import TextIO

    from speedmeter.domain import DownloadResult, SpeedReport

LABEL_WIDTH: Final = 18
"""Column width for the labels, so the values line up under each other."""

UNAVAILABLE: Final = "n/a"
"""Shown where a figure genuinely has no value.

Deliberately not ``0.00``: a zero standard deviation is a real and flattering
answer -- a perfectly steady connection -- so printing it for a run that could
not measure consistency would state the opposite of the truth.
"""

PERCENT: Final = 100
"""Multiplier turning the coefficient of variation into a percentage."""


def _megabytes(size_bytes: int) -> str:
    """Render a byte count in SI megabytes."""
    return f"{size_bytes / BYTES_PER_MEGABYTE:,.2f} MB"


def _line(label: str, value: str) -> str:
    """Render one aligned label and value pair."""
    return f"{label + ':':<{LABEL_WIDTH}}{value}"


class ConsoleReporter(Reporter):
    """Writes a finished report as aligned, human-readable text."""

    def __init__(self, stream: TextIO | None = None) -> None:
        """Choose where the report is written.

        Args:
            stream: Destination for the report. Defaults to standard output,
                resolved now rather than at import time so that a caller which
                replaces ``sys.stdout`` -- a test harness, or a shell
                redirection set up after import -- is respected.
        """
        self._stream = stream if stream is not None else sys.stdout

    def report(self, report: SpeedReport) -> None:
        """Write the outcome of a run.

        Args:
            report: The aggregated result.
        """
        lines = [
            _line("Target", report.url),
            _line(
                "Attempts",
                f"{report.successful_attempts} of {report.requested_attempts} succeeded",
            ),
            "",
            *self._measurement_lines(report),
        ]
        if report.failures:
            lines.extend(self._failure_lines(report))
        self._stream.write("\n".join(lines) + "\n")

    def _measurement_lines(self, report: SpeedReport) -> list[str]:
        """Render the figures, or explain why there are none.

        Args:
            report: The aggregated result.

        Returns:
            The lines describing what was measured.
        """
        if not report.has_measurements:
            return ["Every attempt failed, so no speed was measured."]

        return [
            _line("Downloaded", _megabytes(report.total_bytes)),
            _line("Average request", f"{report.average_seconds:.3f} s"),
            _line(
                "Speed",
                f"{report.megabytes_per_second:,.2f} MB/s"
                f"  ({report.megabits_per_second:,.2f} Mbit/s)",
            ),
            "",
            _line("Slowest attempt", f"{report.slowest_megabytes_per_second:,.2f} MB/s"),
            _line("Fastest attempt", f"{report.fastest_megabytes_per_second:,.2f} MB/s"),
            _line("Variation", self._variation(report)),
            "",
            "Units are decimal: 1 MB = 10^6 bytes, 1 Mbit = 10^6 bits.",
        ]

    def _variation(self, report: SpeedReport) -> str:
        """Render the consistency figures, or say they are unavailable.

        Args:
            report: The aggregated result.

        Returns:
            The rendered value.
        """
        stdev = report.megabytes_per_second_stdev
        if stdev is None:
            return f"{UNAVAILABLE} (needs at least two successful attempts)"

        variation = report.coefficient_of_variation
        if variation is None:
            return f"+/- {stdev:,.2f} MB/s"
        return f"+/- {stdev:,.2f} MB/s ({variation * PERCENT:.1f}% of the mean attempt)"

    def _failure_lines(self, report: SpeedReport) -> list[str]:
        """Render the reason for every failed attempt.

        Args:
            report: The aggregated result.

        Returns:
            The lines listing the failures.
        """
        return [
            "",
            f"Failed attempts ({report.failed_attempts}):",
            *(f"  - {reason}" for reason in report.failures),
        ]


class ConsoleProgressListener(ProgressListener):
    """Announces each attempt as it finishes.

    Ten sequential downloads of a deliberately heavy file take long enough that
    a silent terminal is indistinguishable from a hung one.
    """

    def __init__(self, stream: TextIO | None = None) -> None:
        """Choose where progress is written.

        Args:
            stream: Destination for progress. Defaults to standard error, so
                that redirecting the report to a file leaves progress on the
                terminal and leaves the file free of it.
        """
        self._stream = stream if stream is not None else sys.stderr

    def on_attempt_succeeded(self, number: int, total: int, result: DownloadResult) -> None:
        """Announce a completed attempt and what it measured.

        Args:
            number: One-based index of the attempt.
            total: How many attempts the run will make.
            result: What the attempt measured.
        """
        rate = result.bytes_per_second / BYTES_PER_MEGABYTE
        self._write(
            number,
            total,
            f"{rate:,.2f} MB/s  ({_megabytes(result.size_bytes)}"
            f" in {result.elapsed_seconds:.3f} s)",
        )

    def on_attempt_failed(self, number: int, total: int, reason: str) -> None:
        """Announce a failed attempt.

        Args:
            number: One-based index of the attempt.
            total: How many attempts the run will make.
            reason: Why it failed.
        """
        self._write(number, total, f"failed - {reason}")

    def _write(self, number: int, total: int, message: str) -> None:
        """Write one progress line and flush it.

        Flushing matters: without it the lines are buffered and arrive all at
        once when the run ends, which defeats the purpose of showing progress.

        Args:
            number: One-based index of the attempt.
            total: How many attempts the run will make.
            message: What to say about this attempt.
        """
        width = len(str(total))
        self._stream.write(f"[{number:>{width}}/{total}] {message}\n")
        self._stream.flush()
