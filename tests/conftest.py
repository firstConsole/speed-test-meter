"""Shared pytest configuration.

Hypothesis aborts an example that exceeds a wall-clock deadline, 200 ms by
default. Every property here is arithmetic over at most twenty items, so a
deadline failure could only ever mean the machine paused the process -- which
a shared CI runner does routinely. Disabling it removes a class of failure
that would always be a false alarm, and costs nothing: a genuinely slow
property would show up in the suite's own runtime.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING

import pytest
from hypothesis import settings

from tests.helpers import SERVER_PAYLOAD, LocalServer

if TYPE_CHECKING:
    from collections.abc import Iterator
    from email.message import Message

settings.register_profile("default", deadline=None)
settings.load_profile("default")


@pytest.fixture(scope="module")
def server() -> Iterator[LocalServer]:
    """Serve a few canned responses on an ephemeral loopback port.

    Shared by the adapter tests and the end-to-end test, which need the same
    server for different reasons: one exercises the transport directly, the
    other checks that the assembled program reaches it.
    """
    recorded: list[Message] = []

    class Handler(BaseHTTPRequestHandler):
        # Speak HTTP/1.1 with keep-alive, as a real CDN would, so the adapter
        # is exercised against the connection handling it will actually meet.
        protocol_version = "HTTP/1.1"

        # The name is fixed by BaseHTTPRequestHandler's dispatch.
        def do_GET(self) -> None:
            recorded.append(self.headers)
            if self.path == "/missing":
                self.send_error(404)
                return
            if self.path == "/broken":
                self.send_error(500)
                return
            body = b"" if self.path == "/empty" else SERVER_PAYLOAD
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            """Keep the test output clean."""

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.daemon_threads = True
    host, port = httpd.server_address[:2]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield LocalServer(base_url=f"http://{host!s}:{port}", requests=recorded)
    finally:
        # Shut down deliberately: filterwarnings = ["error"] turns a leaked
        # socket into a failure attached to whatever test runs next.
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
