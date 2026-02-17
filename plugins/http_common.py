# plugins/http_common.py

"""Shared HTTP helpers for plugins.

The plugin architecture intentionally keeps network access narrow and predictable.
These helpers provide:

- URL validation (scheme, host allowlist, no userinfo)
- Optional redirect behavior (disabled or safely constrained)
- Response size limits (max_response_bytes)
- Simple JSON parsing helpers
- Dot-path extraction for JSON objects
- Basic secret-file permission checks

This module has **no dependency** on signal-agent internals.
"""

from __future__ import annotations

import json
import os
import stat
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Optional, Tuple


class HttpCommonError(ValueError):
    """Raised when common HTTP validation fails."""


def _normalize_list(v: Any) -> List[str]:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    return [str(v).strip()] if str(v).strip() else []


def parse_and_validate_url(
    url: str,
    *,
    allowed_schemes: Iterable[str] = ("http", "https"),
    allowed_hosts: Optional[Iterable[str]] = None,
) -> urllib.parse.ParseResult:
    """Validate URL and return urllib.parse result.

    Security properties:
    - Only allowed schemes (default: http/https)
    - Must include hostname
    - Disallows userinfo (user:pass@host)
    - Optional host allowlist (hostname or hostname:port)
    """

    if not isinstance(url, str) or not url.strip():
        raise HttpCommonError("URL must be a non-empty string")

    url = url.strip()
    parsed = urllib.parse.urlparse(url)

    schemes = {s.strip().lower() for s in allowed_schemes if str(s).strip()}
    if not schemes:
        schemes = {"http", "https"}

    scheme = (parsed.scheme or "").lower()
    if scheme not in schemes:
        raise HttpCommonError(f"URL scheme not allowed: {scheme!r} (allowed: {sorted(schemes)})")

    if parsed.username or parsed.password:
        raise HttpCommonError("URL must not include username/password components")

    if not parsed.hostname:
        raise HttpCommonError("URL must include a hostname")

    # Host allowlist (exact match on hostname or hostname:port)
    if allowed_hosts is not None:
        allowed = {h.strip().lower() for h in _normalize_list(allowed_hosts)}
        if allowed:
            hostname = (parsed.hostname or "").lower()
            hostport = hostname + (f":{parsed.port}" if parsed.port else "")
            if hostname not in allowed and hostport not in allowed:
                raise HttpCommonError(
                    f"URL host not in allowed_hosts: {hostname!r} (hostport={hostport!r})"
                )

    return parsed


def _build_opener(
    *,
    follow_redirects: bool,
    allowed_schemes: Iterable[str],
    allowed_hosts: Optional[Iterable[str]],
) -> urllib.request.OpenerDirector:
    """Build an opener that either disables redirects or constrains them."""

    if not follow_redirects:
        # Disable redirects entirely.
        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
                return None

        return urllib.request.build_opener(_NoRedirect)

    # Follow redirects, but validate each Location target.
    class _SafeRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
            # newurl can be relative
            try:
                abs_url = urllib.parse.urljoin(req.full_url, newurl)
                parse_and_validate_url(
                    abs_url,
                    allowed_schemes=allowed_schemes,
                    allowed_hosts=allowed_hosts,
                )
            except Exception:
                return None
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    return urllib.request.build_opener(_SafeRedirect)


def http_request(
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    data: Optional[bytes] = None,
    timeout_sec: int = 4,
    follow_redirects: bool = True,
    max_response_bytes: int = 262_144,
    allowed_schemes: Iterable[str] = ("http", "https"),
    allowed_hosts: Optional[Iterable[str]] = None,
) -> Tuple[int, str, str, bool]:
    """Perform an HTTP request and return: (status, text, content_type, truncated).

    - Enforces URL validation (scheme/host allowlist) for both initial URL and redirects.
    - Enforces a maximum response size to avoid unbounded memory usage.

    If a response exceeds max_response_bytes, truncated=True and the returned body is cut.
    """

    parsed = parse_and_validate_url(url, allowed_schemes=allowed_schemes, allowed_hosts=allowed_hosts)

    # Basic sanity bounds
    try:
        timeout_sec = int(timeout_sec)
    except Exception:
        timeout_sec = 4
    if timeout_sec < 1:
        timeout_sec = 1
    if timeout_sec > 60:
        timeout_sec = 60

    try:
        max_response_bytes = int(max_response_bytes)
    except Exception:
        max_response_bytes = 262_144
    if max_response_bytes < 1:
        max_response_bytes = 1
    if max_response_bytes > 10_000_000:
        max_response_bytes = 10_000_000

    req_headers = dict(headers or {})

    req = urllib.request.Request(url=parsed.geturl(), data=data, method=method.upper(), headers=req_headers)

    opener = _build_opener(
        follow_redirects=bool(follow_redirects),
        allowed_schemes=allowed_schemes,
        allowed_hosts=allowed_hosts,
    )

    def _read_limited(resp) -> Tuple[bytes, bool]:
        raw = resp.read(max_response_bytes + 1)
        if len(raw) > max_response_bytes:
            return raw[:max_response_bytes], True
        return raw, False

    try:
        with opener.open(req, timeout=timeout_sec) as resp:
            status = getattr(resp, "status", 200)
            ctype = (resp.headers.get("Content-Type") or "")
            raw, truncated = _read_limited(resp)
            text = raw.decode("utf-8", errors="replace")
            return int(status), text, ctype, truncated

    except urllib.error.HTTPError as e:
        ctype = (getattr(e, "headers", None) or {}).get("Content-Type") or ""
        try:
            raw = e.read(max_response_bytes + 1)
        except Exception:
            raw = b""
        truncated = len(raw) > max_response_bytes
        if truncated:
            raw = raw[:max_response_bytes]
        text = raw.decode("utf-8", errors="replace")
        return int(e.code), text, ctype, truncated

    except urllib.error.URLError as e:
        return 0, f"URL error: {e}", "", False


def try_parse_json(text: str) -> Optional[Any]:
    try:
        return json.loads(text)
    except Exception:
        return None


def extract_dot_path(obj: Any, path: str) -> Any:
    """Extract a value from a JSON-like object using dot notation.

    Rules:
    - Each segment selects a dict key (for dicts)
    - If current value is a list/tuple and segment is an int, selects that index

    Examples:
      obj={"a":{"b":[{"c":1}]}}
      path="a.b.0.c" => 1
    """

    if not isinstance(path, str) or not path.strip():
        raise ValueError("json_path/value must be a non-empty string")

    cur: Any = obj
    for seg in path.split("."):
        seg = seg.strip()
        if seg == "":
            raise ValueError("json_path/value contains an empty segment")

        if isinstance(cur, dict):
            if seg not in cur:
                raise KeyError(seg)
            cur = cur[seg]
            continue

        if isinstance(cur, (list, tuple)):
            if not seg.isdigit():
                raise KeyError(seg)
            idx = int(seg)
            if idx < 0 or idx >= len(cur):
                raise IndexError(seg)
            cur = cur[idx]
            continue

        raise TypeError(f"Cannot traverse into type: {type(cur).__name__}")

    return cur


def check_private_file(path: str) -> Tuple[bool, str]:
    """Check that a file exists and is not accessible by group/others.

    Returns: (ok, message)

    This is a **best-effort** permission check intended for secret files like API tokens.
    """
    p = os.path.expanduser(path)
    try:
        st = os.stat(p)
    except FileNotFoundError:
        return False, f"File not found: {path}"
    except Exception as e:
        return False, f"Unable to stat file {path}: {e}"

    if not stat.S_ISREG(st.st_mode):
        return False, f"Not a regular file: {path}"

    # Any group/other permission bits set => not private enough.
    if (st.st_mode & 0o077) != 0:
        mode = oct(st.st_mode & 0o777)
        return False, f"File permissions too open for secret material: {path} (mode {mode}, expected 0o600)"

    return True, "ok"
