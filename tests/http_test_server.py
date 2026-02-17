from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable, Dict, Tuple


RouteFn = Callable[[BaseHTTPRequestHandler, bytes], Tuple[int, Dict[str, str], bytes]]


def make_handler(routes: Dict[Tuple[str, str], RouteFn]):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self._handle("GET")

        def do_POST(self):  # noqa: N802
            self._handle("POST")

        def _handle(self, method: str) -> None:
            path = self.path.split("?", 1)[0]
            fn = routes.get((method.upper(), path))
            if fn is None:
                self.send_response(404)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"not found")
                return

            length = int(self.headers.get("Content-Length") or "0")
            body = self.rfile.read(length) if length > 0 else b""

            status, headers, resp_body = fn(self, body)

            self.send_response(status)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(resp_body or b"")

        def log_message(self, fmt: str, *args) -> None:  # noqa: A003
            # Silence default HTTP server logs during tests.
            return

    return Handler


def start_test_server(routes: Dict[Tuple[str, str], RouteFn]) -> tuple[str, HTTPServer]:
    handler_cls = make_handler(routes)
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    host, port = server.server_address
    base_url = f"http://{host}:{port}"
    return base_url, server
