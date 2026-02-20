# tests/test_home_assistant_service_plugin.py

from __future__ import annotations

import json
import os
import tempfile

from plugins.home_assistant_service import HomeAssistantServicePlugin
from tests.http_test_server import start_test_server


def test_home_assistant_service_scene_turn_on() -> None:
    plugin = HomeAssistantServicePlugin()

    with tempfile.NamedTemporaryFile(delete=False, mode="w", encoding="utf-8") as tf:
        tf.write("fake-token")
        token_path = tf.name

    os.chmod(token_path, 0o600)

    try:
        def route(handler, body: bytes):
            payload = json.loads((body or b"{}").decode("utf-8"))
            assert payload.get("entity_id") == "scene.bedroom_on"
            resp = [{"entity_id": "scene.bedroom_on", "state": "scening"}]
            return 200, {"Content-Type": "application/json"}, json.dumps(resp).encode("utf-8")

        base_url, server = start_test_server({("POST", "/api/services/scene/turn_on"): route})
        try:
            globals_raw = {
                "plugin_http": {"allowed_hosts": ["127.0.0.1"]},
                "home_assistant_service": {
                    "url": base_url,
                    "token_file": token_path,
                    "require_private_token_file": True,
                },
            }

            rule = {
                "name": "bedroom_on",
                "type": "home_assistant_service",
                "home_assistant_service": {
                    "domain": "scene",
                    "service": "turn_on",
                    "entity_id": "scene.bedroom_on",
                    "label": "Bedroom",
                },
            }

            plugin.validate(rule, globals_raw)
            res = plugin.run(rule, globals_raw, context={})
            assert res.status == "ok"
            assert res.exit_code == 0
            assert "Bedroom:" in res.body
        finally:
            server.shutdown()
    finally:
        os.unlink(token_path)
