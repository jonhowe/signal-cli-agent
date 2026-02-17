# plugins/home_assistant.py

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, Optional

from .base import BasePlugin, PluginResult
from .http_common import (
    HttpCommonError,
    check_private_file,
    extract_dot_path,
    http_request,
    parse_and_validate_url,
    try_parse_json,
)


log = logging.getLogger("signal-agent.plugins.home_assistant")

_ENTITY_ID_RE = re.compile(r"^[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+$")
_DOT_PATH_RE = re.compile(r"^[A-Za-z0-9_\-]+(\.[A-Za-z0-9_\-]+)*$")

_WARNED_TOKEN_FILES: set[str] = set()


def _norm_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, str):
        return [v.strip()] if v.strip() else []
    s = str(v).strip()
    return [s] if s else []


def _read_token_file(path: str) -> str:
    p = os.path.expanduser(path)
    with open(p, "r", encoding="utf-8") as f:
        token = f.read(8192).strip()
    if not token:
        raise ValueError(f"Home Assistant token file is empty: {path}")
    return token


class HomeAssistantPlugin(BasePlugin):
    """Home Assistant plugin (Phase 1): read-only interactions.

    Supported actions:
    - get_state  (GET /api/states/<entity_id>)
    - template   (POST /api/template)  [read-only template rendering]

    Security/robustness:
    - Optional host allowlist via globals.plugin_http.allowed_hosts
    - Max response size via globals.plugin_http.max_response_bytes
    - Token file permission checks (require_private_token_file)
    """

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
            raise ValueError(
                "Home Assistant plugin requires globals.home_assistant.token_file or rule.home_assistant.token_file"
            )

        timeout_sec = cfg.get("timeout_sec", 4)
        try:
            timeout_sec = int(timeout_sec)
        except Exception:
            raise ValueError("home_assistant.timeout_sec must be an int")
        if timeout_sec < 1 or timeout_sec > 30:
            raise ValueError("home_assistant.timeout_sec must be between 1 and 30")

        # Shared HTTP defaults (can be overridden in home_assistant: block)
        http_defaults = dict((globals_raw.get("plugin_http") or {}))
        allowed_schemes = _norm_list(cfg.get("allowed_schemes", http_defaults.get("allowed_schemes", ["http", "https"])))
        allowed_hosts = _norm_list(cfg.get("allowed_hosts", http_defaults.get("allowed_hosts", [])))
        follow_redirects = bool(cfg.get("follow_redirects", http_defaults.get("follow_redirects", True)))
        max_response_bytes = cfg.get("max_response_bytes", http_defaults.get("max_response_bytes", 262_144))

        try:
            max_response_bytes = int(max_response_bytes)
        except Exception:
            raise ValueError("home_assistant.max_response_bytes must be an int")
        if max_response_bytes < 1 or max_response_bytes > 10_000_000:
            raise ValueError("home_assistant.max_response_bytes must be between 1 and 10,000,000")

        try:
            parse_and_validate_url(url, allowed_schemes=allowed_schemes, allowed_hosts=allowed_hosts)
        except HttpCommonError as e:
            raise ValueError(f"home_assistant.url invalid: {e}")

        # Token file checks
        require_private = bool(cfg.get("require_private_token_file", False))
        token_path = os.path.expanduser(token_file)
        if not os.path.isfile(token_path):
            raise ValueError(f"Home Assistant token_file not found: {token_file}")

        ok, msg = check_private_file(token_file)
        if not ok:
            if require_private:
                raise ValueError(msg)
            # Warn once per token path to avoid log spam.
            key = os.path.expanduser(token_file)
            if key not in _WARNED_TOKEN_FILES:
                _WARNED_TOKEN_FILES.add(key)
                log.warning("Home Assistant token file permissions warning: %s", msg)

        if action == "get_state":
            entity_id = str(cfg.get("entity_id", "")).strip()
            if not entity_id:
                raise ValueError("home_assistant.action=get_state requires home_assistant.entity_id")
            if not _ENTITY_ID_RE.match(entity_id):
                raise ValueError(f"Invalid entity_id format: {entity_id} (expected domain.object)")

            value_path = str(cfg.get("value", cfg.get("json_path", "state"))).strip()
            if not value_path:
                raise ValueError("home_assistant.value/json_path must be non-empty")
            if len(value_path) > 256:
                raise ValueError("home_assistant.value/json_path too long (max 256)")
            if not _DOT_PATH_RE.match(value_path):
                raise ValueError("home_assistant.value/json_path must be dot notation like state or attributes.foo")

        elif action == "template":
            template = cfg.get("template")
            if not isinstance(template, str) or not template.strip():
                raise ValueError("home_assistant.action=template requires home_assistant.template (non-empty string)")
            if len(template) > 4096:
                raise ValueError("home_assistant.template is too long (max 4096 chars)")

        # follow_redirects is intentionally not restricted; safe redirect handling is in http_common.
        _ = follow_redirects

    def run(self, rule: Dict[str, Any], globals_raw: Dict[str, Any], context: Dict[str, Any]) -> PluginResult:
        try:
            cfg = dict((globals_raw.get("home_assistant") or {}))
            cfg.update(rule.get("home_assistant") or {})

            http_defaults = dict((globals_raw.get("plugin_http") or {}))
            allowed_schemes = _norm_list(cfg.get("allowed_schemes", http_defaults.get("allowed_schemes", ["http", "https"])))
            allowed_hosts = _norm_list(cfg.get("allowed_hosts", http_defaults.get("allowed_hosts", [])))
            follow_redirects = bool(cfg.get("follow_redirects", http_defaults.get("follow_redirects", True)))
            max_response_bytes = int(cfg.get("max_response_bytes", http_defaults.get("max_response_bytes", 262_144)))

            action = str(cfg.get("action", "")).strip().lower()
            url_base = str(cfg.get("url", "")).strip().rstrip("/")
            token_file = str(cfg.get("token_file", "")).strip()
            timeout_sec = int(cfg.get("timeout_sec", 4))

            label = str(cfg.get("label", "")).strip()
            strip = bool(cfg.get("strip", True))
            empty_as = str(cfg.get("empty_as", "")).strip()

            token = _read_token_file(token_file)

            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            }

            if action == "get_state":
                entity_id = str(cfg.get("entity_id", "")).strip()
                url = f"{url_base}/api/states/{entity_id}"

                status, text, ctype, truncated = http_request(
                    "GET",
                    url,
                    headers=headers,
                    timeout_sec=timeout_sec,
                    follow_redirects=follow_redirects,
                    max_response_bytes=max_response_bytes,
                    allowed_schemes=allowed_schemes,
                    allowed_hosts=allowed_hosts,
                )

                if truncated:
                    return PluginResult(
                        status="error",
                        exit_code=4,
                        body=f"Home Assistant get_state failed: response exceeded max_response_bytes ({max_response_bytes}).",
                        meta={"entity_id": entity_id, "max_response_bytes": max_response_bytes},
                    )

                parsed = try_parse_json(text) if "application/json" in (ctype or "").lower() else try_parse_json(text)

                if status != 200 or not isinstance(parsed, dict):
                    msg = f"Home Assistant get_state failed (status={status})."
                    detail = (text or "")[:600].strip()
                    if detail:
                        msg += f"\n{detail}"
                    return PluginResult(status="error", exit_code=2, body=msg)

                value_path = str(cfg.get("value", cfg.get("json_path", "state"))).strip() or "state"

                try:
                    value = extract_dot_path(parsed, value_path)
                except Exception:
                    return PluginResult(
                        status="error",
                        exit_code=3,
                        body=f"Home Assistant value path not found: {value_path}",
                        meta={"entity_id": entity_id, "value": value_path},
                    )

                out: str
                if value is None:
                    out = ""
                elif isinstance(value, str):
                    out = value
                else:
                    out = json.dumps(value, ensure_ascii=False)

                if strip:
                    out = out.strip()

                append_unit = bool(cfg.get("append_unit", False))
                if append_unit and value_path == "state" and isinstance(out, str) and out:
                    try:
                        unit = extract_dot_path(parsed, "attributes.unit_of_measurement")
                    except Exception:
                        unit = ""
                    if isinstance(unit, str):
                        unit = unit.strip()
                    else:
                        unit = ""
                    if unit:
                        out = f"{out} {unit}"

                if not out and empty_as:
                    out = empty_as

                body = f"{label}: {out}" if label else out
                return PluginResult(status="ok", exit_code=0, body=body, meta={"entity_id": entity_id, "value": value_path})

            if action == "template":
                template = str(cfg.get("template", ""))
                url = f"{url_base}/api/template"

                payload = json.dumps({"template": template}).encode("utf-8")
                headers_t = dict(headers)
                headers_t["Content-Type"] = "application/json"

                status, text, ctype, truncated = http_request(
                    "POST",
                    url,
                    headers=headers_t,
                    data=payload,
                    timeout_sec=timeout_sec,
                    follow_redirects=follow_redirects,
                    max_response_bytes=max_response_bytes,
                    allowed_schemes=allowed_schemes,
                    allowed_hosts=allowed_hosts,
                )

                if truncated:
                    return PluginResult(
                        status="error",
                        exit_code=4,
                        body=f"Home Assistant template render failed: response exceeded max_response_bytes ({max_response_bytes}).",
                    )

                if status != 200:
                    msg = f"Home Assistant template render failed (status={status})."
                    detail = (text or "")[:600].strip()
                    if detail:
                        msg += f"\n{detail}"
                    return PluginResult(status="error", exit_code=3, body=msg)

                rendered = text or ""
                if strip:
                    rendered = rendered.strip()
                if not rendered and empty_as:
                    rendered = empty_as

                body = f"{label}: {rendered}" if label else rendered
                return PluginResult(status="ok", exit_code=0, body=body, meta={"action": "template"})

            return PluginResult(status="error", exit_code=1, body=f"Unknown action: {action}")

        except Exception as e:
            return PluginResult(status="error", exit_code=10, body=f"Home Assistant plugin error: {e}")
