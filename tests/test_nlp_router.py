import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


from plugins.nlp_router import route_message


class _Handler(BaseHTTPRequestHandler):
    response_obj = None

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_response(404)
            self.end_headers()
            return
        _ = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(self.response_obj).encode("utf-8"))

    def log_message(self, *_args, **_kwargs):
        # Silence test server logs
        return


def _run_server(resp_obj):
    _Handler.response_obj = resp_obj
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


def test_router_selects_allowed_rule():
    resp = {
        "choices": [{"message": {"content": json.dumps({"rule": "bedroom_on", "confidence": 0.92, "reason": "ok"})}}]
    }
    srv = _run_server(resp)
    try:
        globals_raw = {
            "nlp": {
                "enabled": True,
                "base_url": f"http://127.0.0.1:{srv.server_port}/v1",
                "model": "test",
                "timeout_sec": 3,
            }
        }
        candidates = [{"name": "bedroom_on", "description": "", "phrases": []}]
        decision = route_message(globals_raw, "turn on bedroom", candidates, sender="+15551234567")
        assert decision is not None
        assert decision.rule == "bedroom_on"
        assert decision.confidence > 0.9
    finally:
        srv.shutdown()


def test_router_handles_no_match():
    resp = {
        "choices": [{"message": {"content": json.dumps({"rule": "no_match", "confidence": 0.2, "reason": "no"})}}]
    }
    srv = _run_server(resp)
    try:
        globals_raw = {
            "nlp": {
                "enabled": True,
                "base_url": f"http://127.0.0.1:{srv.server_port}/v1",
                "model": "test",
                "timeout_sec": 3,
            }
        }
        candidates = [{"name": "bedroom_on", "description": "", "phrases": []}]
        decision = route_message(globals_raw, "hello", candidates)
        assert decision is not None
        assert decision.rule == "no_match"
    finally:
        srv.shutdown()
