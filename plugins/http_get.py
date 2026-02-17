# plugins/http_get.py

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple

from .base import BasePlugin, PluginResult


_JSON_PATH_RE = re.compile(r"^[A-Za-z0-9_\-]+(\.[A-Za-z0-9_\-]+)*$")


def _http_get(
    url: str,
    timeout_sec: int,
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[int, str, Optional[Any]]:
    """Returns: (status_code, response_text, parsed_json_or_none)."""

    req = urllib.request.Request(url=url, method="GET")
    req.add_header("Accept", "application/json")
    for k, v in (headers or {}).items():
        if k and v is not None:
            req.add_header(str(k), str(v))

    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            status = getattr(resp, "status", 200)
            raw = resp.read()
            text = raw.decode("utf-8", errors="replace")

            parsed = None
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if "application/json" in ctype:
                try:
                    parsed = json.loads(text)
                except Exception:
                    parsed = None
            return int(status), text, parsed

    except urllib.error.HTTPError as e:
        raw = e.read()
        text = raw.decode("utf-8", errors="replace")
        return int(getattr(e, "code", 0) or 0), text, None
    except urllib.error.URLError as e:
        return 0, f"URL error: {e}", None


def _extract_json_path(obj: Any, json_path: str) -> Any:
    """Extract dot-path from nested dict/list.

    Supports:
      - dict keys: "a.b.c"
      - list indices if a segment is digits: "items.0.name"
    """

    cur: Any = obj
    for seg in json_path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(seg)
        elif isinstance(cur, list) and seg.isdigit():
            idx = int(seg)
            if idx < 0 or idx >= len(cur):
                return None
            cur = cur[idx]
        else:
            return None
    return cur


class HttpGetPlugin(BasePlugin):
    """Generic read-only HTTP GET plugin."""

    name = "http_get"

    def validate(self, rule: Dict[str, Any], globals_raw: Dict[str, Any]) -> None:
        cfg = dict((globals_raw.get("http_get") or {}))
        cfg.update(rule.get("http_get") or {})

        url = str(cfg.get("url", "")).strip()
        if not url:
            raise ValueError("http_get.url is required")

        parsed = urllib.parse.urlparse(url)
        if parsed.scheme.lower() not in ("http", "https"):
            raise ValueError("http_get.url must start with http:// or https://")

        timeout_sec = cfg.get("timeout_sec", cfg.get("timeout", 4))
        try:
            timeout_sec = int(timeout_sec)
        except Exception:
            raise ValueError("http_get.timeout_sec must be an int")
        if timeout_sec < 1 or timeout_sec > 30:
            raise ValueError("http_get.timeout_sec must be between 1 and 30")

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

            url = str(cfg.get("url", "")).strip()
            timeout_sec = int(cfg.get("timeout_sec", cfg.get("timeout", 4)))

            headers_raw = cfg.get("headers") or {}
            headers = {str(k): str(v) for k, v in headers_raw.items()} if isinstance(headers_raw, dict) else {}

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

            status, text, parsed_json = _http_get(url=url, timeout_sec=timeout_sec, headers=headers)

            if status != 200:
                detail = (text or "")[:600].strip()
                msg = f"http_get failed (status={status})."
                if detail:
                    msg += f"\n{detail}"
                return PluginResult(status="error", exit_code=2, body=msg, meta={"url": url})

            value: Any
            if json_path:
                if parsed_json is None:
                    try:
                        parsed_json = json.loads(text)
                    except Exception:
                        return PluginResult(
                            status="error",
                            exit_code=3,
                            body="http_get.json_path requested but response was not valid JSON",
                            meta={"url": url},
                        )
                value = _extract_json_path(parsed_json, json_path)
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
