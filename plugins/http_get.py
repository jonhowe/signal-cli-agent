# plugins/http_get.py

from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any, Dict

from .base import BasePlugin, PluginResult
from .http_common import HttpCommonError, extract_dot_path, http_request, parse_and_validate_url, try_parse_json


_JSON_PATH_RE = re.compile(r"^[A-Za-z0-9_\-]+(\.[A-Za-z0-9_\-]+)*$")


def _norm_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, str):
        return [v.strip()] if v.strip() else []
    s = str(v).strip()
    return [s] if s else []


class HttpGetPlugin(BasePlugin):
    """Generic read-only HTTP GET plugin.

    Phase 1 security/robustness:
    - URL validation (scheme + optional host allowlist)
    - Optional redirect control
    - Max response size limit (max_response_bytes)
    """

    name = "http_get"

    def validate(self, rule: Dict[str, Any], globals_raw: Dict[str, Any]) -> None:
        cfg = dict((globals_raw.get("http_get") or {}))
        cfg.update(rule.get("http_get") or {})

        url = str(cfg.get("url", "")).strip()
        if not url:
            raise ValueError("http_get.url is required")

        # Shared HTTP defaults (can be overridden in http_get: block)
        http_defaults = dict((globals_raw.get("plugin_http") or {}))
        allowed_schemes = _norm_list(cfg.get("allowed_schemes", http_defaults.get("allowed_schemes", ["http", "https"])))
        allowed_hosts = _norm_list(cfg.get("allowed_hosts", http_defaults.get("allowed_hosts", [])))
        follow_redirects = cfg.get("follow_redirects", http_defaults.get("follow_redirects", True))
        max_response_bytes = cfg.get("max_response_bytes", http_defaults.get("max_response_bytes", 262_144))

        # Validate URL (scheme + host allowlist)
        try:
            parse_and_validate_url(url, allowed_schemes=allowed_schemes, allowed_hosts=allowed_hosts)
        except HttpCommonError as e:
            raise ValueError(f"http_get.url invalid: {e}")

        timeout_sec = cfg.get("timeout_sec", cfg.get("timeout", 4))
        try:
            timeout_sec = int(timeout_sec)
        except Exception:
            raise ValueError("http_get.timeout_sec must be an int")
        if timeout_sec < 1 or timeout_sec > 30:
            raise ValueError("http_get.timeout_sec must be between 1 and 30")

        try:
            max_response_bytes = int(max_response_bytes)
        except Exception:
            raise ValueError("http_get.max_response_bytes must be an int")
        if max_response_bytes < 1 or max_response_bytes > 10_000_000:
            raise ValueError("http_get.max_response_bytes must be between 1 and 10,000,000")

        headers = cfg.get("headers")
        if headers is not None and not isinstance(headers, dict):
            raise ValueError("http_get.headers must be a mapping")

        params = cfg.get("params")
        if params is not None and not isinstance(params, dict):
            raise ValueError("http_get.params must be a mapping")

        json_path = str(cfg.get("json_path", "")).strip()
        if json_path:
            if len(json_path) > 256:
                raise ValueError("http_get.json_path too long (max 256)")
            if not _JSON_PATH_RE.match(json_path):
                raise ValueError("http_get.json_path must be dot notation like data.value")

        label = str(cfg.get("label", "")).strip()
        if label and len(label) > 128:
            raise ValueError("http_get.label too long (max 128)")

    def run(self, rule: Dict[str, Any], globals_raw: Dict[str, Any], context: Dict[str, Any]) -> PluginResult:
        try:
            cfg = dict((globals_raw.get("http_get") or {}))
            cfg.update(rule.get("http_get") or {})

            http_defaults = dict((globals_raw.get("plugin_http") or {}))
            allowed_schemes = _norm_list(cfg.get("allowed_schemes", http_defaults.get("allowed_schemes", ["http", "https"])))
            allowed_hosts = _norm_list(cfg.get("allowed_hosts", http_defaults.get("allowed_hosts", [])))
            follow_redirects = bool(cfg.get("follow_redirects", http_defaults.get("follow_redirects", True)))
            max_response_bytes = int(cfg.get("max_response_bytes", http_defaults.get("max_response_bytes", 262_144)))

            url = str(cfg.get("url", "")).strip()
            timeout_sec = int(cfg.get("timeout_sec", cfg.get("timeout", 4)))

            # Headers
            headers_raw = cfg.get("headers") or {}
            headers = {str(k): str(v) for k, v in headers_raw.items()} if isinstance(headers_raw, dict) else {}
            headers.setdefault("Accept", "application/json")

            # Params
            params_raw = cfg.get("params") or {}
            if isinstance(params_raw, dict) and params_raw:
                u = urllib.parse.urlparse(url)
                q = urllib.parse.parse_qs(u.query, keep_blank_values=True)
                for k, v in params_raw.items():
                    q[str(k)] = [str(v)]
                new_q = urllib.parse.urlencode(q, doseq=True)
                url = urllib.parse.urlunparse((u.scheme, u.netloc, u.path, u.params, new_q, u.fragment))

            label = str(cfg.get("label", "")).strip()
            strip = bool(cfg.get("strip", True))
            empty_as = str(cfg.get("empty_as", "")).strip()
            json_path = str(cfg.get("json_path", "")).strip()

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
                    body=f"http_get failed: response exceeded max_response_bytes ({max_response_bytes}).",
                    meta={"url": url, "max_response_bytes": max_response_bytes},
                )

            if status != 200:
                detail = (text or "")[:600].strip()
                msg = f"http_get failed (status={status})."
                if detail:
                    msg += f"\n{detail}"
                return PluginResult(status="error", exit_code=2, body=msg, meta={"url": url})

            value: Any
            if json_path:
                parsed_json = try_parse_json(text)
                if parsed_json is None:
                    return PluginResult(
                        status="error",
                        exit_code=3,
                        body="http_get.json_path requested but response was not valid JSON",
                        meta={"url": url, "content_type": ctype},
                    )
                try:
                    value = extract_dot_path(parsed_json, json_path)
                except Exception:
                    return PluginResult(
                        status="error",
                        exit_code=3,
                        body=f"http_get.json_path not found: {json_path}",
                        meta={"url": url},
                    )
            else:
                value = text

            if value is None:
                out = ""
            elif isinstance(value, str):
                out = value
            else:
                out = json.dumps(value, ensure_ascii=False)

            if strip:
                out = out.strip()
            if not out and empty_as:
                out = empty_as

            body = f"{label}: {out}" if label else out
            return PluginResult(status="ok", exit_code=0, body=body, meta={"url": url, "json_path": json_path})

        except Exception as e:
            return PluginResult(status="error", exit_code=10, body=f"http_get plugin error: {e}")
