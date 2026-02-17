# plugins/home_assistant.py

from __future__ import annotations

import json
import os
import re
import urllib.request
import urllib.error
from typing import Any, Dict, Optional

from .base import BasePlugin, PluginResult


_ENTITY_ID_RE = re.compile(r"^[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+$")


def _read_token_file(path: str) -> str:
    p = os.path.expanduser(path)
    with open(p, "r", encoding="utf-8") as f:
        token = f.read().strip()
    if not token:
        raise ValueError(f"Home Assistant token file is empty: {path}")
    return token


def _http_json(
    method: str,
    url: str,
    token: str,
    timeout_sec: int,
    body_obj: Optional[Dict[str, Any]] = None,
) -> tuple[int, str, Optional[Any]]:
    """
    Returns: (status_code, response_text, parsed_json_or_none)
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    data = None
    if body_obj is not None:
        data = json.dumps(body_obj).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url=url, data=data, method=method.upper(), headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            status = resp.status
            raw = resp.read()
            text = raw.decode("utf-8", errors="replace")

            # Try JSON parse if response claims JSON
            parsed = None
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if "application/json" in ctype:
                try:
                    parsed = json.loads(text)
                except Exception:
                    parsed = None

            return status, text, parsed

    except urllib.error.HTTPError as e:
        raw = e.read()
        text = raw.decode("utf-8", errors="replace")
        return e.code, text, None
    except urllib.error.URLError as e:
        return 0, f"URL error: {e}", None


class HomeAssistantPlugin(BasePlugin):
    name = "home_assistant"

    def validate(self, rule: Dict[str, Any], globals_raw: Dict[str, Any]) -> None:
        cfg = dict((globals_raw.get("home_assistant") or {}))
        cfg.update(rule.get("home_assistant") or {})

        action = str(cfg.get("action", "")).strip().lower()
        if action not in ("get_state", "template"):
            raise ValueError("home_assistant.action must be one of: get_state, template")

        url = str(cfg.get("url", "")).strip()
        if not url:
            raise ValueError("Home Assistant plugin requires globals.home_assistant.url or rule.home_assistant.url")

        token_file = str(cfg.get("token_file", "")).strip()
        if not token_file:
            raise ValueError("Home Assistant plugin requires globals.home_assistant.token_file or rule.home_assistant.token_file")

        timeout_sec = cfg.get("timeout_sec", 4)
        try:
            timeout_sec = int(timeout_sec)
        except Exception:
            raise ValueError("home_assistant.timeout_sec must be an int")
        if timeout_sec < 1 or timeout_sec > 30:
            raise ValueError("home_assistant.timeout_sec must be between 1 and 30")

        if action == "get_state":
            entity_id = str(cfg.get("entity_id", "")).strip()
            if not entity_id:
                raise ValueError("home_assistant.action=get_state requires home_assistant.entity_id")
            if not _ENTITY_ID_RE.match(entity_id):
                raise ValueError(f"Invalid entity_id format: {entity_id} (expected domain.object)")
        elif action == "template":
            template = cfg.get("template")
            if not isinstance(template, str) or not template.strip():
                raise ValueError("home_assistant.action=template requires home_assistant.template (non-empty string)")
            if len(template) > 4096:
                raise ValueError("home_assistant.template is too long (max 4096 chars)")

    def run(self, rule: Dict[str, Any], globals_raw: Dict[str, Any], context: Dict[str, Any]) -> PluginResult:
        try:
            cfg = dict((globals_raw.get("home_assistant") or {}))
            cfg.update(rule.get("home_assistant") or {})

            action = str(cfg.get("action", "")).strip().lower()
            url_base = str(cfg.get("url", "")).strip().rstrip("/")
            token_file = str(cfg.get("token_file", "")).strip()
            timeout_sec = int(cfg.get("timeout_sec", 4))

            label = str(cfg.get("label", "")).strip()
            strip = bool(cfg.get("strip", True))
            empty_as = str(cfg.get("empty_as", "")).strip()

            token = _read_token_file(token_file)

            if action == "get_state":
                entity_id = str(cfg.get("entity_id", "")).strip()
                url = f"{url_base}/api/states/{entity_id}"

                status, text, parsed = _http_json("GET", url, token, timeout_sec)

                if status != 200 or not isinstance(parsed, dict):
                    msg = f"Home Assistant get_state failed (status={status})."
                    detail = (text or "")[:600].strip()
                    if detail:
                        msg += f"\n{detail}"
                    return PluginResult(status="error", exit_code=2, body=msg)

                state = str(parsed.get("state", "")).strip()
                if strip:
                    state = state.strip()

                if not state and empty_as:
                    state = empty_as

                body = f"{label}: {state}" if label else state
                return PluginResult(status="ok", exit_code=0, body=body, meta={"entity_id": entity_id})

            if action == "template":
                template = str(cfg.get("template", ""))
                url = f"{url_base}/api/template"

                status, text, parsed = _http_json("POST", url, token, timeout_sec, body_obj={"template": template})

                # /api/template returns rendered text (often plain text), not always JSON
                if status != 200:
                    msg = f"Home Assistant template render failed (status={status})."
                    detail = (text or "")[:600].strip()
                    if detail:
                        msg += f"\n{detail}"
                    return PluginResult(status="error", exit_code=3, body=msg)

                rendered = (text or "")
                if strip:
                    rendered = rendered.strip()
                if not rendered and empty_as:
                    rendered = empty_as

                body = f"{label}: {rendered}" if label else rendered
                return PluginResult(status="ok", exit_code=0, body=body, meta={"action": "template"})

            return PluginResult(status="error", exit_code=1, body=f"Unknown action: {action}")

        except Exception as e:
            return PluginResult(status="error", exit_code=10, body=f"Home Assistant plugin error: {e}")