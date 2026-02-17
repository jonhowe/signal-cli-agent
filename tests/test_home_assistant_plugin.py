from __future__ import annotations

import json
import os
import tempfile

import pytest

from plugins.home_assistant import HomeAssistantPlugin

from .http_test_server import start_test_server


def test_home_assistant_get_state_and_value_paths():
    TOKEN = "TEST_TOKEN"

    def state_route(req, _body):
        auth = req.headers.get("Authorization")
        if auth != f"Bearer {TOKEN}":
            return 401, {"Content-Type": "text/plain"}, b"unauthorized"

        payload = {
            "state": "Week B",
            "attributes": {
                "friendly_name": "Example Week Sensor",
                "unit_of_measurement": "",
            },
        }
        return 200, {"Content-Type": "application/json"}, json.dumps(payload).encode("utf-8")

    base_url, server = start_test_server({("GET", "/api/states/sensor.example_week"): state_route})

    with tempfile.TemporaryDirectory() as td:
        token_file = os.path.join(td, "ha_token")
        with open(token_file, "w", encoding="utf-8") as f:
            f.write(TOKEN)
        os.chmod(token_file, 0o600)

        plugin = HomeAssistantPlugin()
        globals_raw = {
            "plugin_http": {"allowed_hosts": ["127.0.0.1"], "max_response_bytes": 10000},
            "home_assistant": {
                "url": base_url,
                "token_file": token_file,
                "timeout_sec": 4,
            },
        }

        # Default value path => state
        rule1 = {
            "home_assistant": {
                "action": "get_state",
                "entity_id": "sensor.example_week",
                "label": "Week",
            }
        }
        plugin.validate(rule1, globals_raw)
        res1 = plugin.run(rule1, globals_raw, context={})
        assert res1.status == "ok"
        assert res1.body.strip() == "Week: Week B"

        # Value path => attributes.friendly_name
        rule2 = {
            "home_assistant": {
                "action": "get_state",
                "entity_id": "sensor.example_week",
                "value": "attributes.friendly_name",
            }
        }
        plugin.validate(rule2, globals_raw)
        res2 = plugin.run(rule2, globals_raw, context={})
        assert res2.status == "ok"
        assert res2.body.strip() == "Example Week Sensor"

    server.shutdown()
    server.server_close()


def test_home_assistant_append_unit():
    TOKEN = "TEST_TOKEN"

    def state_route(req, _body):
        auth = req.headers.get("Authorization")
        if auth != f"Bearer {TOKEN}":
            return 401, {"Content-Type": "text/plain"}, b"unauthorized"

        payload = {
            "state": "72",
            "attributes": {
                "unit_of_measurement": "°F",
            },
        }
        return 200, {"Content-Type": "application/json"}, json.dumps(payload).encode("utf-8")

    base_url, server = start_test_server({("GET", "/api/states/sensor.temp"): state_route})

    with tempfile.TemporaryDirectory() as td:
        token_file = os.path.join(td, "ha_token")
        with open(token_file, "w", encoding="utf-8") as f:
            f.write(TOKEN)
        os.chmod(token_file, 0o600)

        plugin = HomeAssistantPlugin()
        globals_raw = {
            "plugin_http": {"allowed_hosts": ["127.0.0.1"], "max_response_bytes": 10000},
            "home_assistant": {
                "url": base_url,
                "token_file": token_file,
            },
        }

        rule = {
            "home_assistant": {
                "action": "get_state",
                "entity_id": "sensor.temp",
                "append_unit": True,
            }
        }

        plugin.validate(rule, globals_raw)
        res = plugin.run(rule, globals_raw, context={})
        assert res.status == "ok"
        assert res.body.strip() == "72 °F"

    server.shutdown()
    server.server_close()


def test_home_assistant_token_file_permissions_enforced():
    TOKEN = "TEST_TOKEN"

    def state_route(req, _body):
        # If we got here, auth is fine.
        return 200, {"Content-Type": "application/json"}, b"{}"

    base_url, server = start_test_server({("GET", "/api/states/sensor.any"): state_route})

    with tempfile.TemporaryDirectory() as td:
        token_file = os.path.join(td, "ha_token")
        with open(token_file, "w", encoding="utf-8") as f:
            f.write(TOKEN)

        # Insecure perms
        os.chmod(token_file, 0o644)

        plugin = HomeAssistantPlugin()
        globals_raw = {
            "plugin_http": {"allowed_hosts": ["127.0.0.1"]},
            "home_assistant": {
                "url": base_url,
                "token_file": token_file,
                "require_private_token_file": True,
            },
        }

        rule = {
            "home_assistant": {
                "action": "get_state",
                "entity_id": "sensor.any",
            }
        }

        with pytest.raises(ValueError):
            plugin.validate(rule, globals_raw)

    server.shutdown()
    server.server_close()
