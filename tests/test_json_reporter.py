"""Tests for the machine-readable reporter.

The schema is a published interface: anything consuming this output breaks
silently if a key is renamed or a value quietly changes meaning. These pin the
parts a consumer would rely on.
"""

from __future__ import annotations

import io
import json

import pytest

from speedmeter.adapters.json_report import JsonReporter
from speedmeter.domain import BITS_PER_MEGABIT, BYTES_PER_MEGABYTE, SpeedReport
from speedmeter.ports import Reporter
from tests.helpers import URL, make_report, make_result, report_with_rates

UNDEFINED_WITHOUT_A_MEASUREMENT = (
    "average_seconds",
    "bytes_per_second",
    "megabytes_per_second",
    "megabits_per_second",
    "slowest_megabytes_per_second",
    "fastest_megabytes_per_second",
)


def emit(report: SpeedReport) -> dict[str, object]:
    """Render a report and parse the result back."""
    stream = io.StringIO()
    JsonReporter(stream).report(report)
    parsed: dict[str, object] = json.loads(stream.getvalue())
    return parsed


class TestDocumentShape:
    """The document is valid, complete and self-describing."""

    def test_implements_the_port(self) -> None:
        assert isinstance(JsonReporter(io.StringIO()), Reporter)

    def test_writes_one_parsable_document(self) -> None:
        stream = io.StringIO()

        JsonReporter(stream).report(report_with_rates(4.8, 4.9))

        # Parsing is the assertion: malformed output raises here.
        json.loads(stream.getvalue())

    def test_ends_with_a_newline(self) -> None:
        # Without it the shell prompt lands on the same line, and appending
        # documents to a file produces one unparsable run-on line.
        stream = io.StringIO()

        JsonReporter(stream).report(report_with_rates(4.8, 4.9))

        assert stream.getvalue().endswith("\n")

    def test_states_the_unit_base_in_the_document(self) -> None:
        # A consumer must not have to guess between 10^6 and 2^20.
        document = emit(report_with_rates(4.8, 4.9))

        assert document["units"] == {
            "bytes_per_megabyte": BYTES_PER_MEGABYTE,
            "bits_per_megabit": BITS_PER_MEGABIT,
        }

    def test_the_schema_mirrors_the_report_properties(self) -> None:
        """Every key that names a property must carry that property's value.

        The schema deliberately reuses the Python names, so a consumer and a
        developer share one vocabulary. This fails if a key is ever wired to
        the wrong figure.
        """
        report = report_with_rates(4.80, 4.52, 5.01)
        document = emit(report)

        mirrored = [
            key for key in document if isinstance(getattr(SpeedReport, key, None), property)
        ]
        assert len(mirrored) > 10, "the schema should mirror most of the report"
        for key in mirrored:
            assert document[key] == getattr(report, key), f"{key} does not match the report"


class TestSuccessfulRun:
    """What a complete run puts on the wire."""

    @pytest.fixture
    def document(self) -> dict[str, object]:
        return emit(report_with_rates(4.80, 4.52, 5.01))

    def test_names_the_target_and_the_counts(self, document: dict[str, object]) -> None:
        assert document["url"] == URL
        assert document["requested_attempts"] == 3
        assert document["successful_attempts"] == 3
        assert document["failed_attempts"] == 0

    def test_includes_every_attempt_separately(self, document: dict[str, object]) -> None:
        # Lets a consumer spot which attempt stalled, or apply a measure this
        # report deliberately does not compute, without measuring again.
        attempts = document["attempts"]

        assert isinstance(attempts, list)
        assert len(attempts) == 3
        assert set(attempts[0]) == {
            "status_code",
            "size_bytes",
            "elapsed_seconds",
            "megabytes_per_second",
        }

    def test_keeps_full_precision(self) -> None:
        """Values are not rounded for display, because this output is computed on.

        A consumer wanting two decimals can round; one needing the original
        value could not recover it.
        """
        report = report_with_rates(4.80, 4.52, 5.01)
        document = emit(report)

        assert document["megabytes_per_second"] == report.megabytes_per_second


class TestMissingFigures:
    """Absent values are null, so a consumer cannot mistake them for data."""

    @pytest.mark.parametrize("key", UNDEFINED_WITHOUT_A_MEASUREMENT)
    def test_are_null_when_every_attempt_failed(self, key: str) -> None:
        """Zero would describe a working but glacial connection.

        A consumer comparing null against a threshold gets an error; one
        comparing 0.0 gets a confident wrong answer.
        """
        document = emit(make_report(failures=("timed out", "timed out")))

        assert document[key] is None

    def test_totals_stay_zero_because_that_is_a_fact(self) -> None:
        # Nothing was downloaded, and zero says exactly that.
        document = emit(make_report(failures=("timed out",)))

        assert document["total_bytes"] == 0
        assert document["total_seconds"] == 0
        assert document["has_measurements"] is False
        assert document["attempts"] == []

    def test_spread_is_null_below_two_attempts(self) -> None:
        document = emit(report_with_rates(4.8))

        assert document["megabytes_per_second_stdev"] is None
        assert document["coefficient_of_variation"] is None
        assert document["has_spread"] is False

    def test_lists_every_failure_reason(self) -> None:
        document = emit(make_report(make_result(), failures=("timed out", "refused")))

        assert document["failures"] == ["timed out", "refused"]


class TestOutputDestination:
    """Where the document goes."""

    def test_defaults_to_stdout(self, capsys: pytest.CaptureFixture[str]) -> None:
        # Progress is written to stderr by a separate adapter, so this stream
        # carries nothing but the document and stays pipeable.
        JsonReporter().report(report_with_rates(4.8, 4.9))

        captured = capsys.readouterr()
        json.loads(captured.out)
        assert captured.err == ""
