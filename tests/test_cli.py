"""Tests for the command line interface.

Two kinds of test live here. Most replace the transport, because what the
composition root is responsible for is wiring: which adapter satisfies which
port, where each stream goes, and what the process returns. A few run the
assembled program against a real loopback server, because wiring that
type-checks can still fail to reach anything.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from speedmeter import cli
from speedmeter.exceptions import InvalidUrlError, TransportError
from tests.helpers import URL, ScriptedDownloader, make_result, successes

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from tests.helpers import LocalServer, Outcome


@pytest.fixture
def install_downloader(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[Sequence[Outcome]], ScriptedDownloader]:
    """Replace the transport the composition root would otherwise build."""

    def install(script: Sequence[Outcome]) -> ScriptedDownloader:
        downloader = ScriptedDownloader(script)
        monkeypatch.setattr(cli, "HttpDownloader", lambda **_: downloader)
        return downloader

    return install


class TestExitCodes:
    """What the process returns, which is the only thing a script can read."""

    def test_a_measured_run_succeeds(
        self, install_downloader: Callable[[Sequence[Outcome]], ScriptedDownloader]
    ) -> None:
        install_downloader(successes(3))

        assert cli.main(["-n", "3", "--quiet", URL]) == cli.EXIT_SUCCESS

    def test_a_run_where_everything_failed_is_distinct_from_success(
        self, install_downloader: Callable[[Sequence[Outcome]], ScriptedDownloader]
    ) -> None:
        """A monitoring job must tell "connection down" from "command wrong"."""
        install_downloader([TransportError(URL, "timed out")] * 3)

        assert cli.main(["-n", "3", "--quiet", URL]) == cli.EXIT_NO_MEASUREMENT

    def test_an_unusable_address_is_a_usage_error(
        self, install_downloader: Callable[[Sequence[Outcome]], ScriptedDownloader]
    ) -> None:
        install_downloader([InvalidUrlError(URL, "unsupported scheme")])

        assert cli.main(["--quiet", URL]) == cli.EXIT_USAGE_ERROR

    def test_a_usage_error_prints_a_message_and_no_traceback(
        self,
        install_downloader: Callable[[Sequence[Outcome]], ScriptedDownloader],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A stack trace describes our call stack; the user needs their mistake."""
        install_downloader([InvalidUrlError(URL, "unsupported scheme")])

        cli.main(["--quiet", URL])

        captured = capsys.readouterr()
        assert "unsupported scheme" in captured.err
        assert "Traceback" not in captured.err
        assert captured.out == ""


class TestArgumentHandling:
    """What the parser accepts and what it refuses."""

    def test_sends_ten_requests_unless_told_otherwise(
        self, install_downloader: Callable[[Sequence[Outcome]], ScriptedDownloader]
    ) -> None:
        # The task specifies ten; this pins that the CLI does not override it.
        downloader = install_downloader(successes(10))

        cli.main(["--quiet", URL])

        assert downloader.call_count == 10

    def test_the_attempt_count_is_configurable(
        self, install_downloader: Callable[[Sequence[Outcome]], ScriptedDownloader]
    ) -> None:
        downloader = install_downloader(successes(4))

        cli.main(["-n", "4", "--quiet", URL])

        assert downloader.call_count == 4

    def test_warm_up_requests_are_extra_and_discarded(
        self,
        install_downloader: Callable[[Sequence[Outcome]], ScriptedDownloader],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        downloader = install_downloader(successes(4))

        cli.main(["-n", "3", "--warmup", "1", "--quiet", URL])

        assert downloader.call_count == 4
        assert "3 of 3 succeeded" in capsys.readouterr().out

    @pytest.mark.parametrize(
        "argv",
        [["-n", "abc", URL], ["-n", "0", URL], ["-n", "-5", URL], ["--warmup", "-1", URL], []],
    )
    def test_refuses_nonsense_arguments(self, argv: list[str]) -> None:
        with pytest.raises(SystemExit) as caught:
            cli.main(argv)

        assert caught.value.code == cli.EXIT_USAGE_ERROR

    def test_reports_its_version(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as caught:
            cli.main(["--version"])

        assert caught.value.code == cli.EXIT_SUCCESS
        assert "speedmeter" in capsys.readouterr().out


class TestOutputSelection:
    """Which adapters the composition root picks, and where they write."""

    def test_writes_human_readable_text_by_default(
        self,
        install_downloader: Callable[[Sequence[Outcome]], ScriptedDownloader],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        install_downloader(successes(2))

        cli.main(["-n", "2", "--quiet", URL])

        assert "Speed:" in capsys.readouterr().out

    def test_writes_json_when_asked(
        self,
        install_downloader: Callable[[Sequence[Outcome]], ScriptedDownloader],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        install_downloader(successes(2))

        cli.main(["-n", "2", "--quiet", "--json", URL])

        document = json.loads(capsys.readouterr().out)
        assert document["successful_attempts"] == 2

    def test_progress_goes_to_stderr_not_into_the_json(
        self,
        install_downloader: Callable[[Sequence[Outcome]], ScriptedDownloader],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--json must not have to suppress progress to stay parsable.

        The two use different streams, which is the whole reason the split
        exists: redirecting stdout to a file yields a file holding only the
        document, while progress stays on the terminal.
        """
        install_downloader(successes(2))

        cli.main(["-n", "2", "--json", URL])

        captured = capsys.readouterr()
        json.loads(captured.out)
        assert "[1/2]" in captured.err

    def test_quiet_suppresses_progress(
        self,
        install_downloader: Callable[[Sequence[Outcome]], ScriptedDownloader],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        install_downloader(successes(2))

        cli.main(["-n", "2", "--quiet", URL])

        assert capsys.readouterr().err == ""

    def test_a_failed_attempt_is_announced_and_listed(
        self,
        install_downloader: Callable[[Sequence[Outcome]], ScriptedDownloader],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        install_downloader([make_result(), TransportError(URL, "timed out")])

        cli.main(["-n", "2", URL])

        captured = capsys.readouterr()
        assert "timed out" in captured.err
        assert "Failed attempts (1):" in captured.out


@pytest.mark.network
class TestEndToEnd:
    """The assembled program against a real server, with nothing replaced."""

    def test_measures_a_real_download(
        self, server: LocalServer, capsys: pytest.CaptureFixture[str]
    ) -> None:
        exit_code = cli.main(["-n", "2", "--quiet", server.url("/heavy.jpg")])

        captured = capsys.readouterr()
        assert exit_code == cli.EXIT_SUCCESS
        assert "2 of 2 succeeded" in captured.out
        assert "MB/s" in captured.out

    def test_produces_a_parsable_document(
        self, server: LocalServer, capsys: pytest.CaptureFixture[str]
    ) -> None:
        exit_code = cli.main(["-n", "2", "--quiet", "--json", server.url("/heavy.jpg")])

        document = json.loads(capsys.readouterr().out)
        assert exit_code == cli.EXIT_SUCCESS
        assert document["successful_attempts"] == 2
        assert document["total_bytes"] > 0

    def test_a_missing_resource_is_reported_not_measured(
        self, server: LocalServer, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # An error page transfers bytes; measuring it would be worse than
        # failing, because the number would look entirely reasonable.
        exit_code = cli.main(["-n", "2", "--quiet", server.url("/missing")])

        assert exit_code == cli.EXIT_NO_MEASUREMENT
        assert "HTTP 404" in capsys.readouterr().out
