"""Machine-readable reporter: the same report as a JSON document.

The schema mirrors the property names on :class:`speedmeter.domain.SpeedReport`
one for one, so a consumer reading the JSON and a developer reading the Python
are looking at the same vocabulary, and a key cannot drift away from the figure
it names.

Two schema decisions worth stating outright.

Figures that do not exist are ``null``, never zero. A standard deviation of
zero is a real and flattering answer, so emitting it for a run that could not
measure consistency would be a lie a consumer has no way to detect.

Floats are written at full precision rather than rounded for display. Rounding
is lossy, and this output exists to be computed on; a consumer that wants two
decimal places can round, while one that needs the original value cannot
recover it. That is why the human-readable reporter exists separately.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Final

from speedmeter.domain import (
    BITS_PER_MEGABIT,
    BYTES_PER_MEGABYTE,
    SpeedReport,
)
from speedmeter.ports import Reporter

if TYPE_CHECKING:
    from typing import TextIO

    from speedmeter.domain import DownloadResult

INDENT: Final = 2
"""Spaces per nesting level.

Indented rather than compact because the output is read by people at least as
often as by programs, and every JSON consumer accepts either.
"""


class JsonReporter(Reporter):
    """Writes a finished report as a single JSON document."""

    def __init__(self, stream: TextIO | None = None) -> None:
        """Choose where the document is written.

        Args:
            stream: Destination for the document. Defaults to standard output,
                resolved now rather than at import time. Progress is written to
                standard error by a separate adapter, so the stream carrying
                this document contains nothing but the document.
        """
        self._stream = stream if stream is not None else sys.stdout

    def report(self, report: SpeedReport) -> None:
        """Write the outcome of a run as JSON.

        Args:
            report: The aggregated result.
        """
        json.dump(self._as_document(report), self._stream, indent=INDENT)
        self._stream.write("\n")

    def _as_document(self, report: SpeedReport) -> dict[str, object]:
        """Build the document for a report.

        Args:
            report: The aggregated result.

        Returns:
            A JSON-serialisable mapping.
        """

        def measured(value: float) -> float | None:
            """Return the value, or None when the run measured nothing."""
            return value if report.has_measurements else None

        return {
            "url": report.url,
            "requested_attempts": report.requested_attempts,
            "successful_attempts": report.successful_attempts,
            "failed_attempts": report.failed_attempts,
            "has_measurements": report.has_measurements,
            # Totals are facts even when every attempt failed: nothing was
            # downloaded, and zero says exactly that.
            "total_bytes": report.total_bytes,
            "total_seconds": report.total_seconds,
            # The rest are undefined without a measurement, so they are null
            # rather than zero. A consumer comparing null against a threshold
            # gets an error; one comparing 0.0 gets a confident wrong answer.
            "average_seconds": measured(report.average_seconds),
            "bytes_per_second": measured(report.bytes_per_second),
            "megabytes_per_second": measured(report.megabytes_per_second),
            "megabits_per_second": measured(report.megabits_per_second),
            "slowest_megabytes_per_second": measured(report.slowest_megabytes_per_second),
            "fastest_megabytes_per_second": measured(report.fastest_megabytes_per_second),
            "has_spread": report.has_spread,
            "megabytes_per_second_stdev": report.megabytes_per_second_stdev,
            "coefficient_of_variation": report.coefficient_of_variation,
            "attempts": [self._as_attempt(result) for result in report.results],
            "failures": list(report.failures),
            "units": {
                "bytes_per_megabyte": BYTES_PER_MEGABYTE,
                "bits_per_megabit": BITS_PER_MEGABIT,
            },
        }

    def _as_attempt(self, result: DownloadResult) -> dict[str, object]:
        """Build the entry for one successful attempt.

        Per-attempt detail is included so a consumer can do its own analysis --
        spot which attempt stalled, or apply a measure this report deliberately
        does not compute -- without rerunning the measurement.

        Args:
            result: One measured attempt.

        Returns:
            A JSON-serialisable mapping.
        """
        return {
            "status_code": result.status_code,
            "size_bytes": result.size_bytes,
            "elapsed_seconds": result.elapsed_seconds,
            "megabytes_per_second": result.bytes_per_second / BYTES_PER_MEGABYTE,
        }
