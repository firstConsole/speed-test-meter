"""Command line entry point and composition root.

This is the one module that knows every other. It reads the arguments, decides
which concrete adapters satisfy the ports, assembles the object graph and hands
it to the use case. Nothing else in the package constructs a collaborator, so
substituting one -- a different transport, a different output format -- is a
change confined to this file.
"""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING, Final

from speedmeter import __version__
from speedmeter.adapters.console import ConsoleProgressListener, ConsoleReporter
from speedmeter.adapters.http import DEFAULT_TIMEOUT_SECONDS, HttpDownloader
from speedmeter.adapters.json_report import JsonReporter
from speedmeter.exceptions import SpeedMeterError
from speedmeter.ports import NullProgressListener
from speedmeter.runner import DEFAULT_ATTEMPTS, SpeedTestRunner

if TYPE_CHECKING:
    from collections.abc import Sequence

    from speedmeter.domain import SpeedReport
    from speedmeter.ports import ProgressListener, Reporter

EXIT_SUCCESS: Final = 0
"""At least one attempt produced a measurement."""

EXIT_NO_MEASUREMENT: Final = 1
"""Every attempt failed, so there is no speed to report.

Distinct from a usage error: the command was correct and the connection was
not, which is a result a monitoring job wants to tell apart from a typo.
"""

EXIT_USAGE_ERROR: Final = 2
"""The command itself was wrong. Matches what argparse returns for bad flags."""

EXIT_INTERRUPTED: Final = 130
"""Interrupted with Ctrl-C. 128 plus SIGINT, as a shell expects."""

DESCRIPTION: Final = """\
Measure download speed by fetching one address repeatedly.

Sends a number of sequential requests, reads each response to the end, and
reports the average request time, the volume downloaded and the resulting
speed. Point it at something large -- a heavy image or a test file -- since a
small file finishes before the connection reaches full speed.
"""

EPILOG: Final = """\
examples:
  speedmeter https://speed.hetzner.de/100MB.bin
  speedmeter -n 20 --warmup 1 https://example.com/heavy.jpg
  speedmeter --json https://example.com/heavy.jpg > result.json

Progress is written to standard error and the report to standard output, so
redirecting the report leaves progress on the terminal and the file clean.

exit codes:
  0  a speed was measured
  1  every attempt failed
  2  the command was used incorrectly
"""


def _positive_int(raw: str) -> int:
    """Parse a command line argument that must be a positive whole number.

    Args:
        raw: The text as typed.

    Returns:
        The parsed value.

    Raises:
        ArgumentTypeError: If the text is not a positive integer.
    """
    try:
        value = int(raw)
    except ValueError:
        message = f"expected a whole number, got {raw!r}"
        raise argparse.ArgumentTypeError(message) from None
    if value < 1:
        message = f"expected a positive number, got {value}"
        raise argparse.ArgumentTypeError(message)
    return value


def _non_negative_int(raw: str) -> int:
    """Parse a command line argument that must not be negative.

    Args:
        raw: The text as typed.

    Returns:
        The parsed value.

    Raises:
        ArgumentTypeError: If the text is not a whole number of zero or more.
    """
    try:
        value = int(raw)
    except ValueError:
        message = f"expected a whole number, got {raw!r}"
        raise argparse.ArgumentTypeError(message) from None
    if value < 0:
        message = f"expected zero or more, got {value}"
        raise argparse.ArgumentTypeError(message)
    return value


def _positive_float(raw: str) -> float:
    """Parse a command line argument that must be a positive number of seconds.

    Args:
        raw: The text as typed.

    Returns:
        The parsed value.

    Raises:
        ArgumentTypeError: If the text is not a positive number.
    """
    try:
        value = float(raw)
    except ValueError:
        message = f"expected a number, got {raw!r}"
        raise argparse.ArgumentTypeError(message) from None
    if value <= 0:
        message = f"expected a positive number, got {value}"
        raise argparse.ArgumentTypeError(message)
    return value


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser.

    Returns:
        A parser for the command line interface.
    """
    parser = argparse.ArgumentParser(
        prog="speedmeter",
        description=DESCRIPTION,
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("url", help="address to download, e.g. a large image")
    parser.add_argument(
        "-n",
        "--attempts",
        type=_positive_int,
        default=DEFAULT_ATTEMPTS,
        metavar="COUNT",
        help=f"how many sequential requests to send (default: {DEFAULT_ATTEMPTS})",
    )
    parser.add_argument(
        "-t",
        "--timeout",
        type=_positive_float,
        default=DEFAULT_TIMEOUT_SECONDS,
        metavar="SECONDS",
        help=f"per-attempt socket timeout (default: {DEFAULT_TIMEOUT_SECONDS:g})",
    )
    parser.add_argument(
        "--warmup",
        type=_non_negative_int,
        default=0,
        metavar="COUNT",
        help=(
            "extra requests to send and discard first, absorbing DNS lookup "
            "and the TLS handshake (default: 0)"
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="write the report as JSON instead of text",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="do not announce each attempt while the run is going",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"speedmeter {__version__}",
    )
    return parser


def _build_progress(args: argparse.Namespace) -> ProgressListener:
    """Choose how progress is announced.

    Progress is not suppressed by ``--json``: it goes to standard error while
    the document goes to standard output, so the two do not collide. Only
    ``--quiet`` turns it off.

    Args:
        args: Parsed arguments.

    Returns:
        The listener the runner should notify.
    """
    if args.quiet:
        return NullProgressListener()
    return ConsoleProgressListener()


def _build_reporter(args: argparse.Namespace) -> Reporter:
    """Choose how the finished report is presented.

    Args:
        args: Parsed arguments.

    Returns:
        The reporter to hand the result to.
    """
    if args.json:
        return JsonReporter()
    return ConsoleReporter()


def _measure(args: argparse.Namespace) -> SpeedReport:
    """Assemble the object graph and perform the run.

    Args:
        args: Parsed arguments.

    Returns:
        The aggregated result of the run.
    """
    runner = SpeedTestRunner(
        HttpDownloader(timeout_seconds=args.timeout),
        progress=_build_progress(args),
        warmup_attempts=args.warmup,
    )
    return runner.run(args.url, attempts=args.attempts)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line interface.

    Args:
        argv: Arguments to parse. Defaults to the real command line; a test
            passes its own so it never has to touch ``sys.argv``.

    Returns:
        The process exit code.
    """
    args = build_parser().parse_args(argv)

    try:
        report = _measure(args)
    except SpeedMeterError as error:
        # Every deliberate failure lands here as a message rather than a
        # traceback. A stack trace tells the user about our call stack when
        # what they need to know is that the address was wrong.
        print(f"speedmeter: {error}", file=sys.stderr)
        return EXIT_USAGE_ERROR
    except KeyboardInterrupt:
        print("speedmeter: interrupted", file=sys.stderr)
        return EXIT_INTERRUPTED

    _build_reporter(args).report(report)
    return EXIT_SUCCESS if report.has_measurements else EXIT_NO_MEASUREMENT
