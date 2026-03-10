# plugins/services/rest_api.py

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import threading
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple

from ..http_common import check_private_file
from .base import BaseServicePlugin, ServiceContext


log = logging.getLogger("signal-agent.services.rest_api")

E164_RE = re.compile(r"^\+\d{7,15}$")


def _norm_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, str):
        return [v.strip()] if v.strip() else []
    s = str(v).strip()
    return [s] if s else []


def _split_chunks_by_lines(s: str, chunk_size: int) -> list[str]:
    """Split a string into chunks on newline boundaries (avoid breaking lines)."""
    if chunk_size <= 0:
        return [s]
    lines = s.splitlines(keepends=True)
    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for line in lines:
        if cur and (cur_len + len(line) > chunk_size):
            chunks.append("".join(cur))
            cur = [line]
            cur_len = len(line)
        else:
            cur.append(line)
            cur_len += len(line)
    if cur:
        chunks.append("".join(cur))
    return chunks or [""]


class _TokenCache:
    def __init__(self, path: str) -> None:
        self.path = path
        self._mtime: float = -1.0
        self._token: str = ""
        self._lock = threading.Lock()

    def read(self) -> str:
        p = os.path.expanduser(self.path)
        try:
            st = os.stat(p)
        except Exception as e:
            raise ValueError(f"Token file not readable: {self.path} ({e})")

        with self._lock:
            if st.st_mtime != self._mtime:
                with open(p, "r", encoding="utf-8") as f:
                    tok = f.read(8192).strip()
                if not tok:
                    raise ValueError(f"Token file is empty: {self.path}")
                self._token = tok
                self._mtime = st.st_mtime
            return self._token


class RestApiService(BaseServicePlugin):
    """External REST API service.

    Exposes an authenticated HTTP endpoint that can send Signal messages.

    Endpoints:
      - GET  /health
      - POST /api/v1/send     JSON: {"to": "+1555..." | ["+1555..."], "message": "..."}

    Security model:
      - Bearer token auth (token_file)
      - allowlist of destinations (allowed_destinations)
      - optional allowlist of client IPs (allowed_client_ips)
      - size limits + rate limiting
    """

    name = "rest_api"

    def __init__(self) -> None:
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._ctx: Optional[ServiceContext] = None
        self._cfg: Dict[str, Any] = {}
        self._token_cache: Optional[_TokenCache] = None

        # Rate limiting: per client IP
        self._rl: Dict[str, deque[float]] = defaultdict(deque)
        self._rl_lock = threading.Lock()

        # Warn-once set for non-private token files (if allowed)
        self._warned_token_files: set[str] = set()

    def validate(self, globals_raw: Dict[str, Any]) -> None:
        cfg = dict((globals_raw.get("rest_api") or {}))
        if not bool(cfg.get("enabled", False)):
            return

        bind_host = str(cfg.get("bind_host", "127.0.0.1")).strip() or "127.0.0.1"
        bind_port = cfg.get("bind_port", 8787)
        try:
            bind_port_i = int(bind_port)
        except Exception:
            raise ValueError("globals.rest_api.bind_port must be an int")
        if bind_port_i < 1 or bind_port_i > 65535:
            raise ValueError("globals.rest_api.bind_port must be between 1 and 65535")

        token_file = str(cfg.get("token_file", "")).strip()
        if not token_file:
            raise ValueError("globals.rest_api.token_file is required when rest_api.enabled=true")
        token_path = os.path.expanduser(token_file)
        if not os.path.isfile(token_path):
            raise ValueError(f"globals.rest_api.token_file not found: {token_file}")

        require_private = bool(cfg.get("require_private_token_file", True))
        ok, msg = check_private_file(token_file)
        if not ok and require_private:
            raise ValueError(f"REST API token file must be private (chmod 600): {msg}")

        allowed_dests = _norm_list(cfg.get("allowed_destinations"))
        # Empty allowed_destinations means "deny all" (safe by default).
        for d in allowed_dests:
            if not E164_RE.match(d):
                raise ValueError(f"globals.rest_api.allowed_destinations contains invalid E.164 number: {d}")

        allowed_client_ips = _norm_list(cfg.get("allowed_client_ips"))
        if bind_host not in ("127.0.0.1", "::1", "localhost") and not allowed_client_ips:
            log.warning(
                "REST API is bound to %s but allowed_client_ips is empty. Consider binding to 127.0.0.1 or setting an IP allowlist.",
                bind_host,
            )

        max_message_chars = int(cfg.get("max_message_chars", 3500))
        if max_message_chars < 1 or max_message_chars > 100_000:
            raise ValueError("globals.rest_api.max_message_chars must be between 1 and 100000")

        max_request_bytes = int(cfg.get("max_request_bytes", 32768))
        if max_request_bytes < 512 or max_request_bytes > 5_000_000:
            raise ValueError("globals.rest_api.max_request_bytes must be between 512 and 5000000")

        max_recipients = int(cfg.get("max_recipients_per_request", 1))
        if max_recipients < 1 or max_recipients > 50:
            raise ValueError("globals.rest_api.max_recipients_per_request must be between 1 and 50")

        # Optional chunking controls
        split_long = bool(cfg.get("split_long_messages", True))
        chunk_size = int(cfg.get("chunk_size", 1400))
        if chunk_size < 200 or chunk_size > 5000:
            raise ValueError("globals.rest_api.chunk_size must be between 200 and 5000")
        _ = split_long

        # Rate limit controls (0 => disabled)
        rpm = int(cfg.get("max_requests_per_minute", 60))
        if rpm < 0 or rpm > 10_000:
            raise ValueError("globals.rest_api.max_requests_per_minute must be between 0 and 10000")

    def start(self, globals_raw: Dict[str, Any], ctx: ServiceContext) -> None:
        cfg = dict((globals_raw.get("rest_api") or {}))
        if not bool(cfg.get("enabled", False)):
            return

        # Validate once at startup.
        self.validate(globals_raw)

        self._ctx = ctx
        self._cfg = cfg

        bind_host = str(cfg.get("bind_host", "127.0.0.1")).strip() or "127.0.0.1"
        bind_port = int(cfg.get("bind_port", 8787))

        self._token_cache = _TokenCache(str(cfg.get("token_file", "")).strip())

        handler_cls = self._make_handler()
        self._server = ThreadingHTTPServer((bind_host, bind_port), handler_cls)
        self._server.daemon_threads = True

        t = threading.Thread(target=self._server.serve_forever, name="rest-api", daemon=True)
        self._thread = t
        t.start()

        log.info("✓ REST API enabled: http://%s:%s (endpoints: GET /health, POST /api/v1/send)", bind_host, bind_port)

    def stop(self) -> None:
        srv = self._server
        if not srv:
            return
        try:
            srv.shutdown()
        except Exception:
            pass
        try:
            srv.server_close()
        except Exception:
            pass
        self._server = None

    # -------------------------
    # HTTP handler
    # -------------------------

    def _make_handler(self):
        service = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                service._handle_get(self)

            def do_POST(self):  # noqa: N802
                service._handle_post(self)

            def log_message(self, fmt: str, *args) -> None:  # noqa: A003
                # Route BaseHTTPRequestHandler logs into our logger.
                try:
                    msg = fmt % args
                except Exception:
                    msg = fmt
                log.info("REST %s - %s", self.address_string(), msg)

        return Handler

    def _handle_get(self, h: BaseHTTPRequestHandler) -> None:
        path = (h.path or "").split("?", 1)[0]
        if path == "/health":
            self._send_json(h, 200, {"status": "ok"})
            return
        self._send_json(h, 404, {"status": "error", "error": "not found"})

    def _handle_post(self, h: BaseHTTPRequestHandler) -> None:
        path = (h.path or "").split("?", 1)[0]
        if path != "/api/v1/send":
            self._send_json(h, 404, {"status": "error", "error": "not found"})
            return

        # Client IP allowlist
        client_ip = (h.client_address[0] if h.client_address else "")
        allowed_client_ips = _norm_list(self._cfg.get("allowed_client_ips"))
        if allowed_client_ips and client_ip not in allowed_client_ips:
            self._send_json(h, 403, {"status": "error", "error": "client IP not allowed"})
            return

        # Rate limiting
        if not self._rate_limit_ok(client_ip):
            self._send_json(h, 429, {"status": "error", "error": "rate limit exceeded"})
            return

        # Auth
        if not self._auth_ok(h):
            self._send_json(h, 401, {"status": "error", "error": "unauthorized"}, headers={"WWW-Authenticate": "Bearer"})
            return

        # Body size limit
        max_request_bytes = int(self._cfg.get("max_request_bytes", 32768))
        try:
            length = int(h.headers.get("Content-Length") or "0")
        except Exception:
            length = 0
        if length < 0:
            length = 0
        if length > max_request_bytes:
            self._send_json(h, 413, {"status": "error", "error": "request too large"})
            return

        body = b""
        if length > 0:
            try:
                body = h.rfile.read(length)
            except Exception:
                body = b""

        try:
            payload = json.loads((body or b"{}").decode("utf-8"))
        except Exception:
            self._send_json(h, 400, {"status": "error", "error": "invalid JSON"})
            return

        to_raw = payload.get("to")
        msg = payload.get("message")
        dry_run = bool(payload.get("dry_run", False))

        dests: list[str]
        if isinstance(to_raw, str):
            dests = [to_raw.strip()]
        elif isinstance(to_raw, (list, tuple)):
            dests = [str(x).strip() for x in to_raw if str(x).strip()]
        else:
            dests = []

        if not dests:
            self._send_json(h, 400, {"status": "error", "error": "missing 'to'"})
            return

        max_recipients = int(self._cfg.get("max_recipients_per_request", 1))
        if len(dests) > max_recipients:
            self._send_json(h, 400, {"status": "error", "error": f"too many recipients (max {max_recipients})"})
            return

        if not isinstance(msg, str) or not msg.strip():
            self._send_json(h, 400, {"status": "error", "error": "missing 'message'"})
            return

        msg = msg

        max_message_chars = int(self._cfg.get("max_message_chars", 3500))
        if len(msg) > max_message_chars:
            self._send_json(h, 400, {"status": "error", "error": f"message too long (max {max_message_chars} chars)"})
            return

        allowed_dests = set(d.lower() for d in _norm_list(self._cfg.get("allowed_destinations")))
        for d in dests:
            if not E164_RE.match(d):
                self._send_json(h, 400, {"status": "error", "error": f"invalid destination (expected E.164): {d}"})
                return
            if allowed_dests and d.lower() not in allowed_dests:
                self._send_json(h, 403, {"status": "error", "error": f"destination not allowed: {d}"})
                return
            if not allowed_dests:
                # Explicit deny-by-default.
                self._send_json(h, 403, {"status": "error", "error": "no allowed_destinations configured"})
                return

        # Chunking behavior (optional)
        split_long = bool(self._cfg.get("split_long_messages", True))
        chunk_size = int(self._cfg.get("chunk_size", 1400))
        numbered = bool(self._cfg.get("numbered_chunks", True))

        parts = [msg]
        if split_long and chunk_size > 0 and len(msg) > chunk_size:
            parts = [p for p in _split_chunks_by_lines(msg, chunk_size) if p]

        # Send
        try:
            sent_count = 0
            for d in dests:
                for idx, part in enumerate(parts, start=1):
                    payload_txt = part
                    if numbered and len(parts) > 1:
                        payload_txt = f"[message {idx}/{len(parts)}]\n" + part
                    if dry_run or (self._ctx and self._ctx.dry_run):
                        log.info("[DRY-RUN] REST would send to %s: %s", d, payload_txt[:200].replace("\n", " "))
                    else:
                        if not self._ctx:
                            raise RuntimeError("REST API service not initialized")
                        self._ctx.send_message(d, payload_txt)
                    sent_count += 1
                    time.sleep(0.05)

            self._send_json(
                h,
                200,
                {
                    "status": "ok",
                    "recipients": dests,
                    "parts": len(parts),
                    "messages_sent": sent_count,
                    "dry_run": bool(dry_run or (self._ctx and self._ctx.dry_run)),
                },
            )
        except Exception as e:
            log.exception("REST send failed")
            self._send_json(h, 500, {"status": "error", "error": f"send failed: {e}"})

    def _auth_ok(self, h: BaseHTTPRequestHandler) -> bool:
        # Bearer token auth.
        auth = str(h.headers.get("Authorization") or "").strip()
        if not auth.lower().startswith("bearer "):
            return False
        presented = auth.split(" ", 1)[1].strip()
        if not presented:
            return False

        if not self._token_cache:
            return False

        tok_path = str(self._cfg.get("token_file", "")).strip()
        require_private = bool(self._cfg.get("require_private_token_file", True))
        ok, msg = check_private_file(tok_path)
        if not ok and require_private:
            # Fail closed.
            log.error("REST API auth file permissions error: %s", msg)
            return False
        if not ok and not require_private:
            key = os.path.expanduser(tok_path)
            if key not in self._warned_token_files:
                self._warned_token_files.add(key)
                log.warning("REST API auth file permissions issue: %s", msg)

        expected = self._token_cache.read()
        return hmac.compare_digest(expected, presented)

    def _rate_limit_ok(self, client_ip: str) -> bool:
        rpm = int(self._cfg.get("max_requests_per_minute", 60))
        if rpm <= 0:
            return True
        now = time.time()
        cutoff = now - 60.0
        ip = client_ip or "-"
        with self._rl_lock:
            q = self._rl[ip]
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= rpm:
                return False
            q.append(now)
        return True

    def _send_json(
        self,
        h: BaseHTTPRequestHandler,
        status: int,
        obj: Dict[str, Any],
        *,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        h.send_response(int(status))
        h.send_header("Content-Type", "application/json; charset=utf-8")
        h.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            h.send_header(k, v)
        h.end_headers()
        h.wfile.write(body)
