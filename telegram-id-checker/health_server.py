"""Small HTTP health endpoint for Render and uptime monitors."""

from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Callable

LOGGER = logging.getLogger(__name__)


class _HealthHandler(BaseHTTPRequestHandler):
    health_callback: Callable[[], dict[str, str]]

    def _serve_health(self, *, include_body: bool) -> None:
        if self.path not in {"/", "/healthz"}:
            self.send_error(404)
            return

        body = json.dumps(self.health_callback()).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if include_body:
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        self._serve_health(include_body=True)

    def do_HEAD(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        self._serve_health(include_body=False)

    def log_message(self, format: str, *args: object) -> None:
        LOGGER.info("health request: %s", format % args)


class HealthServer:
    def __init__(self, host: str, port: int, callback: Callable[[], dict[str, str]]) -> None:
        handler = type("HealthHandler", (_HealthHandler,), {"health_callback": staticmethod(callback)})
        self.server = ThreadingHTTPServer((host, port), handler)
        self.thread = Thread(target=self.server.serve_forever, name="health-server", daemon=True)

    def start(self) -> None:
        self.thread.start()
        LOGGER.info("Health endpoint listening on port %s", self.server.server_port)

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)