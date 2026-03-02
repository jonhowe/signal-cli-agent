# plugins/home_assistant_service.py

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional, Tuple

from .base import BasePlugin, PluginResult
from .http_common import HttpCommonError, check_private_file, http_request, parse_and_validate_url


_DOMAIN_RE = re.compile(r"^[a-zA-Z0-9_]+$")
_SERVICE_RE = re.compile(r"^[a-zA-Z0-9_]+$")
_ENTITY_ID_RE = re.compile(r"^[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+$")


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
    p = path
    import os
    p = os.path.expanduser(p)
    with open(p, "r", encoding="utf-8") as f:
        token = f.read().strip()
    if not token:
        raise ValueError(f"Home Assistant token file is empty: {path}")
    return token


def _merge_cfg(globals_raw: Dict[str, Any], rule: Dict[str, Any]) -> Dict[str, Any]:
    cfg: Dict[str, Any] = dict(globals_raw.get("home_assistant_service") or {})
    cfg.update(rule.get("home_assistant_service") or {})
    return cfg


def _pick(cfg: Dict[str, Any], key: str, default: Any = None) -> Any:
    v = cfg.get(key)
    return default if v is None else v


def _normalize_entity_ids(v: Any) -> Tuple[Optional[str], Optional[list[str]]]:
    """Return (single_entity_id, entity_id_list) where only one may be non-None."""
    if v is None:
        return None, None
    if isinstance(v, str):
        s = v.strip()
        return (s or None), None
    if isinstance(v, (list, tuple)):
        out: list[str] = []
        for item in v:
            if item is None:
                continue
            s = str(item).strip()
            if s:
                out.append(s)
        return None, (out or None)
    return str(v).strip() or None, None


class HomeAssistantServicePlugin(BasePlugin):
    """Home Assistant service-call plugin (phase 1: controlled POST).

    Calls: POST {url}/api/services/{domain}/{service}

    Primary use: activate scenes, turn lights on/off, etc.

    Security posture:
      - Requires explicit domain/service
      - Validates formats
      - Uses token file on disk (must be chmod 600)
      - Uses the shared http_request allowlist + timeouts
    """

    name = "home_assistant_service"

    def validate(self, rule: Dict[str, Any], globals_raw: Dict[str, Any]) -> None:
        cfg = _merge_cfg(globals_raw, rule)

        url = str(_pick(cfg, "url", "")).strip().rstrip("/")
        if not url:
            raise ValueError("home_assistant_service.url is required (set in globals or per rule)")

        # Shared HTTP safety defaults (scheme + allowed_hosts allowlist)
        http_defaults = dict((globals_raw.get("plugin_http") or {}))
        allowed_schemes = _norm_list(cfg.get("allowed_schemes", http_defaults.get("allowed_schemes", ["http", "https"])))
        allowed_hosts = _norm_list(cfg.get("allowed_hosts", http_defaults.get("allowed_hosts", [])))
        try:
            parse_and_validate_url(url, allowed_schemes=allowed_schemes, allowed_hosts=allowed_hosts)
        except HttpCommonError as e:
            raise ValueError(f"home_assistant_service.url invalid: {e}")

        token_file = str(_pick(cfg, "token_file", "")).strip()
        if not token_file:
            raise ValueError("home_assistant_service.token_file is required (set in globals or per rule)")
        ok, msg = check_private_file(token_file)
        require_private = bool(_pick(cfg, "require_private_token_file", False))
        if require_private and not ok:
            raise ValueError(f"Token file must be private (chmod 600): {msg}")

        domain = str(_pick(cfg, "domain", "")).strip()
        service = str(_pick(cfg, "service", "")).strip()
        if not domain or not _DOMAIN_RE.match(domain):
            raise ValueError("home_assistant_service.domain must be a non-empty slug (letters, digits, underscore)")
        if not service or not _SERVICE_RE.match(service):
            raise ValueError("home_assistant_service.service must be a non-empty slug (letters, digits, underscore)")

        # Optional allowlist to restrict what can be called.
        allowed_services = _norm_list(cfg.get("allowed_services"))
        if allowed_services:
            key = f"{domain}.{service}".lower()
            allowed_lower = {s.lower() for s in allowed_services}
            if key not in allowed_lower:
                raise ValueError(
                    f"home_assistant_service {key} not in allowed_services allowlist (set globals.home_assistant_service.allowed_services)"
                )

        entity_id, entity_ids = _normalize_entity_ids(cfg.get("entity_id"))
        if entity_id and not _ENTITY_ID_RE.match(entity_id):
            raise ValueError(f"Invalid entity_id: {entity_id} (expected domain.object)")
        if entity_ids:
            bad = [e for e in entity_ids if not _ENTITY_ID_RE.match(e)]
            if bad:
                raise ValueError(f"Invalid entity_id(s): {', '.join(bad)} (expected domain.object)")

        service_data = cfg.get("service_data")
        if service_data is not None and not isinstance(service_data, dict):
            raise ValueError("home_assistant_service.service_data must be a mapping (YAML dict)")

        timeout_sec = int(_pick(cfg, "timeout_sec", 6))
        if timeout_sec < 1 or timeout_sec > 30:
            raise ValueError("home_assistant_service.timeout_sec must be between 1 and 30")

        # Optional safety caps
        max_body_chars = int(_pick(cfg, "max_body_chars", 4000))
        if max_body_chars < 200 or max_body_chars > 20000:
            raise ValueError("home_assistant_service.max_body_chars must be between 200 and 20000")

    def run(self, rule: Dict[str, Any], globals_raw: Dict[str, Any], context: Dict[str, Any]) -> PluginResult:
        try:
            cfg = _merge_cfg(globals_raw, rule)

            url_base = str(cfg.get("url", "")).strip().rstrip("/")
            token_file = str(cfg.get("token_file", "")).strip()
            token = _read_token_file(token_file)

            domain = str(cfg.get("domain", "")).strip()
            service = str(cfg.get("service", "")).strip()

            label = str(cfg.get("label", "")).strip()
            strip = bool(cfg.get("strip", True))
            empty_as = str(cfg.get("empty_as", "")).strip()

            timeout_sec = int(cfg.get("timeout_sec", 6))
            http_cfg = dict(globals_raw.get("plugin_http") or {})
            allowed_hosts = _norm_list(cfg.get("allowed_hosts", http_cfg.get("allowed_hosts", [])))
            allowed_schemes = _norm_list(cfg.get("allowed_schemes", http_cfg.get("allowed_schemes", ["http", "https"])))
            follow_redirects = bool(http_cfg.get("follow_redirects", False))
            max_response_bytes = int(http_cfg.get("max_response_bytes", 250000))
            max_body_chars = int(cfg.get("max_body_chars", 4000))

            entity_id, entity_ids = _normalize_entity_ids(cfg.get("entity_id"))
            service_data = cfg.get("service_data")
            if service_data is None:
                service_data = {}

            body: Dict[str, Any] = dict(service_data)
            if entity_id:
                body["entity_id"] = entity_id
            elif entity_ids:
                body["entity_id"] = entity_ids

            url = f"{url_base}/api/services/{domain}/{service}"
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }

            status, text, ctype, truncated = http_request(
                "POST",
                url,
                headers=headers,
                timeout_sec=timeout_sec,
                data=json.dumps(body).encode("utf-8"),
                follow_redirects=follow_redirects,
                max_response_bytes=max_response_bytes,
                allowed_schemes=allowed_schemes,
                allowed_hosts=allowed_hosts,
            )

            if truncated:
                return PluginResult(
                    status="error",
                    exit_code=4,
                    body=f"Home Assistant service call failed: response exceeded max_response_bytes ({max_response_bytes}).",
                    meta={"domain": domain, "service": service, "max_response_bytes": max_response_bytes},
                )

            parsed = None
            try:
                if text:
                    parsed = json.loads(text)
            except Exception:
                parsed = None

            if status < 200 or status >= 300:
                detail = (text or "")[:600].strip()
                msg = f"Home Assistant service call failed (status={status})."
                if detail:
                    msg += f"\n{detail}"
                return PluginResult(status="error", exit_code=2, body=msg, meta={"domain": domain, "service": service})

            # HA returns a list of updated states (JSON). Summarize safely.
            rendered = "OK"
            if isinstance(parsed, list) and parsed:
                # Try to extract entity_ids from result objects
                ids: list[str] = []
                for item in parsed[:10]:
                    if isinstance(item, dict):
                        eid = item.get("entity_id")
                        if isinstance(eid, str) and eid:
                            ids.append(eid)
                if ids:
                    rendered = "Updated: " + ", ".join(ids)

            if strip:
                rendered = rendered.strip()
            if not rendered and empty_as:
                rendered = empty_as

            body_txt = f"{label}: {rendered}" if label else rendered
            # Cap body (avoid huge JSON dumps)
            if max_body_chars > 0 and len(body_txt) > max_body_chars:
                body_txt = body_txt[: max_body_chars - 20] + "\n... (truncated)\n"

            return PluginResult(
                status="ok",
                exit_code=0,
                body=body_txt,
                meta={"domain": domain, "service": service, "entity_id": entity_id or entity_ids},
            )

        except Exception as e:
            return PluginResult(status="error", exit_code=10, body=f"Home Assistant service plugin error: {e}")
