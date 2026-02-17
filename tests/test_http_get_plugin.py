from __future__ import annotations

import json
import pytest

from plugins.http_get import HttpGetPlugin

from .http_test_server import start_test_server


def test_http_get_json_path_ok():
    def status_route(_req, _body):
        payload = {"data": {"value": 123}}
        return 200, {"Content-Type": "application/json"}, json.dumps(payload).encode("utf-8")

    base_url, server = start_test_server({("GET", "/status"): status_route})
    try:
        plugin = HttpGetPlugin()
        globals_raw = {
            "plugin_http": {
                "allowed_hosts": ["127.0.0.1"],
                "max_response_bytes": 10000,
            }
        }
        rule = {
            "http_get": {
                "url": f"{base_url}/status",
                "json_path": "data.value",
                "label": "val",
            }
        }

        plugin.validate(rule, globals_raw)
        res = plugin.run(rule, globals_raw, context={})

        assert res.status == "ok"
        assert res.exit_code == 0
        assert res.body.strip() == "val: 123"

    finally:
        server.shutdown()
        server.server_close()


def test_http_get_allowed_hosts_rejects():
    def status_route(_req, _body):
        return 200, {"Content-Type": "text/plain"}, b"ok"

    base_url, server = start_test_server({("GET", "/status"): status_route})
    try:
        plugin = HttpGetPlugin()
        globals_raw = {
            "plugin_http": {
                "allowed_hosts": ["example.com"],
            }
        }
        rule = {"http_get": {"url": f"{base_url}/status"}}

        with pytest.raises(ValueError):
            plugin.validate(rule, globals_raw)

    finally:
        server.shutdown()
        server.server_close()


def test_http_get_max_response_bytes_errors_when_exceeded():
    big = b"x" * 5000

    def big_route(_req, _body):
        return 200, {"Content-Type": "text/plain"}, big

    base_url, server = start_test_server({("GET", "/big"): big_route})
    try:
        plugin = HttpGetPlugin()
        globals_raw = {
            "plugin_http": {
                "allowed_hosts": ["127.0.0.1"],
                "max_response_bytes": 1000,
            }
        }
        rule = {"http_get": {"url": f"{base_url}/big"}}

        plugin.validate(rule, globals_raw)
        res = plugin.run(rule, globals_raw, context={})

        assert res.status == "error"
        assert res.exit_code == 4

    finally:
        server.shutdown()
        server.server_close()


def test_http_get_redirect_behavior():
    def status_route(_req, _body):
        return 200, {"Content-Type": "text/plain"}, b"ok"

    def redir_route(_req, _body):
        # 302 redirect to /status (relative Location)
        return 302, {"Location": "/status"}, b""

    base_url, server = start_test_server({("GET", "/status"): status_route, ("GET", "/redir"): redir_route})
    try:
        plugin = HttpGetPlugin()
        rule = {"http_get": {"url": f"{base_url}/redir"}}

        # Redirects disabled => error
        globals_raw = {
            "plugin_http": {
                "allowed_hosts": ["127.0.0.1"],
                "follow_redirects": False,
                "max_response_bytes": 10000,
            }
        }
        plugin.validate(rule, globals_raw)
        res = plugin.run(rule, globals_raw, context={})
        assert res.status == "error"

        # Redirects enabled => ok
        globals_raw2 = {
            "plugin_http": {
                "allowed_hosts": ["127.0.0.1"],
                "follow_redirects": True,
                "max_response_bytes": 10000,
            }
        }
        plugin.validate(rule, globals_raw2)
        res2 = plugin.run(rule, globals_raw2, context={})
        assert res2.status == "ok"
        assert res2.body.strip() == "ok"

    finally:
        server.shutdown()
        server.server_close()
