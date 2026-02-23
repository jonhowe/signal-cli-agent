#!/usr/bin/env python3
"""
signal-agent.py

DBus listener for signal-cli + YAML rule engine.

Features:
- Session bus connection to org.asamk.Signal (normal mode)
- Test mode: --test (no DBus, no Signal sends)
- Rules loaded from:
    - root rules file (rules.yaml)
    - optional rules_dir: (rules.d/*.yaml)
- Sender matching supports:
    - sender: "+1555..."
    - sender: ["+1555...", "+1666..."]
- Matching: exact | contains | startswith | regex
- Commands: command (argv list) or command_template + args validation
- Plugins: rule 'type' dispatch (Phase 0–1: built-in plugins like home_assistant, http_get)
- Rate limiting: cooldown_sec + max_runs_per_hour per sender+rule
- Redaction patterns
- Reply formatting modes:
    - full   : [rule] exit=0 + $ cmd + output
    - output : [rule] + output
    - bare   : output only
- Output chunking:
    - split_reply + chunk_size
    - numbered_chunks adds "[rule message i/n]" prefixes only when 2+ chunks
- Structured logging:
    - text/json, level, optional log file
- Dry run:
    - global (globals.dry_run or --dry-run)
    - per rule (rule.dry_run)
    - In dry-run, commands still execute, but sends are suppressed
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shlex
import signal as pysignal
import subprocess
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

# Plugin support (Phase 0–1)
from plugins.registry import get_plugin
from plugins.nlp_router import route_message

# Optional imports: only needed in non-test mode
try:
    from pydbus import SessionBus  # type: ignore
    from gi.repository import GLib  # type: ignore
except Exception:
    SessionBus = None  # type: ignore
    GLib = None  # type: ignore


# -----------------------------
# Time / formatting
# -----------------------------

def now_ts() -> float:
    return time.time()


def fmt_ts(ts: Optional[float] = None) -> str:
    dt = datetime.fromtimestamp(ts or now_ts())
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# -----------------------------
# Logging
# -----------------------------

logger = logging.getLogger("signal-agent")


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": fmt_ts(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str = "INFO", logfile: str = "", log_format: str = "text") -> None:
    levelno = getattr(logging, level.upper(), logging.INFO)

    if logfile:
        handler: logging.Handler = logging.FileHandler(logfile)
    else:
        handler = logging.StreamHandler(sys.stdout)

    if (log_format or "text").lower() == "json":
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))

    root = logging.getLogger()
    root.setLevel(levelno)

    # Clear existing handlers (avoid duplicates)
    for h in list(root.handlers):
        root.removeHandler(h)

    root.addHandler(handler)


def log_info(msg: str) -> None:
    logger.info(msg)


def log_warn(msg: str) -> None:
    logger.warning(msg)


def log_err(msg: str) -> None:
    logger.error(msg)


# -----------------------------
# Utilities
# -----------------------------

def safe_int(v: Any, default: int) -> int:
    try:
        return int(v)
    except Exception:
        return default


def looks_like_group_id(group_id: Any) -> bool:
    if group_id is None:
        return False
    if isinstance(group_id, (bytes, bytearray)):
        return len(group_id) > 0
    if isinstance(group_id, (list, tuple)):
        return len(group_id) > 0
    return False


def shorten(s: str, max_chars: int) -> str:
    if max_chars <= 0:
        return s
    if len(s) <= max_chars:
        return s
    return s[: max_chars - 20] + "\n... (truncated)\n"


def apply_redactions(s: str, patterns: List[str]) -> str:
    out = s
    for pat in patterns:
        try:
            out = re.sub(pat, "[REDACTED]", out)
        except re.error:
            continue
    return out


def split_chunks_by_lines(s: str, chunk_size: int) -> List[str]:
    """
    Split a string into chunks on newline boundaries (avoid breaking lines).
    """
    if chunk_size <= 0:
        return [s]

    lines = s.splitlines(keepends=True)
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0

    for line in lines:
        if current and (current_len + len(line) > chunk_size):
            chunks.append("".join(current))
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += len(line)

    if current:
        chunks.append("".join(current))

    return chunks or [""]


def normalize_text(s: str, case_sensitive: bool) -> str:
    return s if case_sensitive else s.lower()


def apply_command_prefix(raw: str, prefix: str, case_sensitive: bool, strip_ws: bool) -> Optional[str]:
    """If prefix is set, require it and strip it.

    Returns:
      - stripped message if prefix matched
      - original message if prefix is empty
      - None if prefix is set and message does not start with it
    """
    msg = raw if raw is not None else ""
    pref = (prefix or "")
    if not pref:
        return msg

    hay = msg if case_sensitive else msg.lower()
    needle = pref if case_sensitive else pref.lower()
    if not hay.startswith(needle):
        return None

    out = msg[len(pref):]
    return out.lstrip() if strip_ws else out


def match_trigger(message: str, trigger: str, mode: str, case_sensitive: bool) -> Tuple[bool, Optional[re.Match]]:
    mode = (mode or "exact").strip().lower()
    mtxt = normalize_text(message, case_sensitive)
    trg = normalize_text(trigger, case_sensitive)

    if mode == "exact":
        return (mtxt == trg, None)
    if mode == "contains":
        return (trg in mtxt, None)
    if mode == "startswith":
        return (mtxt.startswith(trg), None)
    if mode == "regex":
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            m = re.search(trigger, message, flags=flags)
            return (m is not None, m)
        except re.error:
            return (False, None)

    return (mtxt == trg, None)


def render_command_template(template: List[str], values: Dict[str, Any]) -> List[str]:
    return [tok.format(**values) for tok in template]


def validate_args_from_match(args_schema: Dict[str, Any], match_obj: re.Match) -> Dict[str, Any]:
    """
    Maps regex capture groups to args keys in the order declared in args_schema.
    Supports int bounds validation.
    """
    keys = list(args_schema.keys())
    groups = list(match_obj.groups())

    values: Dict[str, Any] = {}
    for idx, key in enumerate(keys):
        if idx >= len(groups):
            raise ValueError(f"Missing regex capture group for arg '{key}'")
        raw = groups[idx]

        schema = args_schema.get(key, {}) or {}
        typ = str(schema.get("type", "str")).lower()

        if typ == "int":
            v = int(raw)
            min_v = schema.get("min")
            max_v = schema.get("max")
            if min_v is not None and v < int(min_v):
                raise ValueError(f"Arg '{key}' must be >= {min_v}")
            if max_v is not None and v > int(max_v):
                raise ValueError(f"Arg '{key}' must be <= {max_v}")
            values[key] = v
        else:
            values[key] = raw

    return values


# -----------------------------
# Rate limiting
# -----------------------------

class RateLimiter:
    """
    Rate limits per (sender, rule_name):
      - cooldown_sec: minimum seconds between runs
      - max_runs_per_hour: max runs in rolling 3600s window
    """
    def __init__(self) -> None:
        self.last_run: Dict[Tuple[str, str], float] = {}
        self.runs: Dict[Tuple[str, str], deque] = defaultdict(deque)

    def allow(self, sender: str, rule_name: str, cooldown_sec: int, max_runs_per_hour: int) -> Tuple[bool, str]:
        key = (sender, rule_name)
        t = now_ts()

        last = self.last_run.get(key)
        if last is not None and cooldown_sec > 0 and (t - last) < cooldown_sec:
            wait = cooldown_sec - (t - last)
            return (False, f"cooldown active (wait {wait:.1f}s)")

        if max_runs_per_hour > 0:
            q = self.runs[key]
            cutoff = t - 3600
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= max_runs_per_hour:
                return (False, f"rate limit exceeded ({len(q)}/{max_runs_per_hour} per hour)")
            q.append(t)

        self.last_run[key] = t
        return (True, "ok")


# -----------------------------
# DBus send wrapper
# -----------------------------

def send_signal_message(signal_obj: Any, recipient: str, message: str) -> None:
    """
    signal-cli DBus signatures vary by version. Try several common forms.
    """
    if not isinstance(message, str):
        message = str(message)

    attempts = [
        lambda: signal_obj.sendMessage(message, [], [recipient]),
        lambda: signal_obj.sendMessage(message, [recipient]),
        lambda: signal_obj.sendMessage(message, [], recipient),
        lambda: signal_obj.sendMessage(message, recipient),
    ]
    last_err: Optional[Exception] = None
    for fn in attempts:
        try:
            fn()
            return
        except Exception as e:
            last_err = e
    raise RuntimeError(f"Failed to send reply via DBus: {last_err}")


# -----------------------------
# YAML config
# -----------------------------

@dataclass
class GlobalsCfg:
    deny_groups: bool = True

    default_timeout_sec: int = 5
    default_max_reply_chars: int = 3500
    default_split_reply: bool = False
    default_chunk_size: int = 1500
    numbered_chunks: bool = False

    default_match: str = "exact"
    default_case_sensitive: bool = False

    default_cooldown_sec: int = 2
    default_max_runs_per_hour: int = 30

    reply_prefix: str = ""
    reply_suffix: str = ""
    default_reply_to: str = "sender"
    admin: str = ""

    redact_regex: List[str] = None  # type: ignore

    # Logging & dry-run defaults (can be overridden by CLI)
    log_level: str = "INFO"
    log_format: str = "text"   # text|json
    log_file: str = ""         # empty => stdout
    dry_run: bool = False      # suppress sends globally

    # NLP router config is stored in globals_raw under "nlp".
    # This flag is mirrored here for quick access.
    nlp_enabled: bool = False

    # Optional command prefix gate. If set, only messages starting with this prefix
    # are treated as commands. The prefix is stripped before matching/NLP.
    command_prefix: str = ""
    command_prefix_case_sensitive: bool = False
    command_prefix_strip_whitespace: bool = True

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "GlobalsCfg":
        return GlobalsCfg(
            deny_groups=bool(d.get("deny_groups", True)),

            default_timeout_sec=safe_int(d.get("default_timeout_sec", 5), 5),
            default_max_reply_chars=safe_int(d.get("default_max_reply_chars", 3500), 3500),
            default_split_reply=bool(d.get("default_split_reply", False)),
            default_chunk_size=safe_int(d.get("default_chunk_size", 1500), 1500),
            numbered_chunks=bool(d.get("numbered_chunks", False)),

            default_match=str(d.get("default_match", "exact")).strip(),
            default_case_sensitive=bool(d.get("default_case_sensitive", False)),

            default_cooldown_sec=safe_int(d.get("default_cooldown_sec", 2), 2),
            default_max_runs_per_hour=safe_int(d.get("default_max_runs_per_hour", 30), 30),

            reply_prefix=str(d.get("reply_prefix", "")),
            reply_suffix=str(d.get("reply_suffix", "")),
            default_reply_to=str(d.get("default_reply_to", "sender")),
            admin=str(d.get("admin", "")),

            redact_regex=list(d.get("redact_regex", []) or []),

            log_level=str(d.get("log_level", "INFO")),
            log_format=str(d.get("log_format", "text")),
            log_file=str(d.get("log_file", "")),
            dry_run=bool(d.get("dry_run", False)),

            nlp_enabled=bool((d.get("nlp") or {}).get("enabled", False)),

            command_prefix=str(d.get("command_prefix", "")),
            command_prefix_case_sensitive=bool(d.get("command_prefix_case_sensitive", False)),
            command_prefix_strip_whitespace=bool(d.get("command_prefix_strip_whitespace", True)),
        )


# -----------------------------
# Config loader (rules.yaml + rules.d/*.yaml)
# -----------------------------

class ConfigLoader:
    """
    Loads a root rules file that can contain:
      - globals: {...}
      - rules_dir: /path/to/rules.d   (optional; defaults to <rules.yaml dir>/rules.d)
      - rules: [...]                 (optional; inline rules)

    Then loads rules from each *.yaml/*.yml file in rules_dir.
    """
    def __init__(self, root_path: Path) -> None:
        self.root_path = root_path
        self._mtimes: Dict[Path, float] = {}

        self.globals_cfg: GlobalsCfg = GlobalsCfg()
        self.globals_raw: Dict[str, Any] = {}   # NEW: raw globals dict for plugins

        self.rules_dir: Optional[Path] = None
        self.rules: List[Dict[str, Any]] = []

    def _list_rule_files(self, rules_dir: Path) -> List[Path]:
        if not rules_dir.exists():
            return []
        return sorted([p for p in rules_dir.iterdir() if p.is_file() and p.suffix in (".yml", ".yaml")])

    def _load_yaml_file(self, p: Path) -> Any:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    def load_if_changed(self) -> None:
        if not self.root_path.exists():
            raise FileNotFoundError(f"Rules file not found: {self.root_path}")

        root_data = self._load_yaml_file(self.root_path)

        globals_dict = root_data.get("globals", {}) or {}
        self.globals_raw = dict(globals_dict)  # NEW: store raw for plugins
        self.globals_cfg = GlobalsCfg.from_dict(globals_dict)

        rules_dir_val = root_data.get("rules_dir")
        if rules_dir_val:
            rules_dir = Path(os.path.expanduser(str(rules_dir_val))).resolve()
        else:
            rules_dir = (self.root_path.parent / "rules.d").resolve()

        rule_files = self._list_rule_files(rules_dir)

        files_to_check = [self.root_path] + rule_files
        changed = False

        for p in files_to_check:
            try:
                m = p.stat().st_mtime
            except FileNotFoundError:
                m = -1.0
            if self._mtimes.get(p) != m:
                changed = True
                break

        if not changed and self.rules_dir == rules_dir:
            return

        merged_rules: List[Dict[str, Any]] = []

        root_rules = root_data.get("rules")
        if isinstance(root_rules, list):
            merged_rules.extend(root_rules)

        for rf in rule_files:
            data = self._load_yaml_file(rf)
            if isinstance(data, dict) and "rules" in data and isinstance(data["rules"], list):
                merged_rules.extend(data["rules"])
            elif isinstance(data, list):
                merged_rules.extend(data)
            elif isinstance(data, dict) and "name" in data:
                merged_rules.append(data)

        self._mtimes = {}
        for p in files_to_check:
            try:
                self._mtimes[p] = p.stat().st_mtime
            except FileNotFoundError:
                self._mtimes[p] = -1.0

        self.rules_dir = rules_dir
        self.rules = merged_rules
        log_info(f"Reloaded rules: {self.root_path} + {rules_dir} (files={len(rule_files)}, rules={len(self.rules)})")


# -----------------------------
# Reply composition
# -----------------------------

def build_reply(
    rule_name: str,
    reply_mode: str,
    exit_code: int,
    display_cmd: str,
    out: str,
    reply_prefix: str,
    reply_suffix: str,
) -> str:
    """
    reply_mode:
      - full:   [rule] exit=0 + $ cmd + out
      - output: [rule] + out
      - bare:   out
    """
    rm = (reply_mode or "full").strip().lower()
    if rm == "output":
        body = f"[{rule_name}]\n{out}"
    elif rm == "bare":
        body = out
    else:
        body = f"[{rule_name}] exit={exit_code}\n$ {display_cmd}\n{out}"
    return (reply_prefix or "") + body + (reply_suffix or "")


def sender_allowed(rule_sender: Any, actual_sender: str) -> bool:
    if rule_sender is None:
        return True
    if isinstance(rule_sender, (list, tuple)):
        allowed = [str(s).strip() for s in rule_sender if s is not None and str(s).strip()]
        if not allowed:
            return True
        return actual_sender in allowed
    s = str(rule_sender).strip()
    if not s:
        return True
    return s == actual_sender


# -----------------------------
# Agent (normal mode)
# -----------------------------

class SignalAgent:
    def __init__(self, rules_path: Path, global_dry_run: bool = False) -> None:
        if SessionBus is None or GLib is None:
            raise RuntimeError("pydbus/GLib not available. Install dependencies or run in --test mode.")

        self.rules_loader = ConfigLoader(rules_path)
        self.rate_limiter = RateLimiter()
        self.global_dry_run = global_dry_run

        # De-dupe cache for DBus callbacks. Some signal-cli versions emit both
        # V1 and V2 callbacks for the same message (e.g. sync + syncv2), which
        # can cause double logs / unintended cooldown blocks.
        self._recent_events: deque[Tuple[int, str, str, str]] = deque(maxlen=200)

        self.bus = SessionBus()
        self.signal = self.bus.get("org.asamk.Signal")

        log_info("✓ Connected to org.asamk.Signal on the session bus")
        log_info(f"Listening… (root rules: {rules_path})")

        self.rules_loader.load_if_changed()

        self.signal.onMessageReceived = self.on_message_received
        self.signal.onMessageReceivedV2 = self.on_message_received_v2
        self.signal.onSyncMessageReceived = self.on_sync_message_received
        self.signal.onSyncMessageReceivedV2 = self.on_sync_message_received_v2

    def on_message_received(self, timestamp: int, sender: str, group_id: Any, message: str, attachments: Any) -> None:
        self.handle_incoming("msg", timestamp, sender, None, group_id, message)

    def on_message_received_v2(self, timestamp: int, sender: str, group_id: Any, message: str, attachments: Any, options: Any = None) -> None:
        self.handle_incoming("msgv2", timestamp, sender, None, group_id, message)

    def on_sync_message_received(self, timestamp: int, sender: str, destination: str, group_id: Any, message: str, attachments: Any) -> None:
        self.handle_incoming("sync", timestamp, sender, destination, group_id, message)

    def on_sync_message_received_v2(self, timestamp: int, sender: str, destination: str, group_id: Any, message: str, attachments: Any, options: Any = None) -> None:
        self.handle_incoming("syncv2", timestamp, sender, destination, group_id, message)

    def maybe_send(self, target: str, payload: str, dry_run: bool) -> None:
        if dry_run or self.global_dry_run:
            log_info(f"[DRY-RUN] Would send to {target}: {payload[:200].replace(chr(10), ' ')}")
            return
        send_signal_message(self.signal, target, payload)

    def handle_incoming(self, kind: str, timestamp_ms: int, sender: str, destination: Optional[str], group_id: Any, message: str) -> None:
        try:
            self.rules_loader.load_if_changed()
        except Exception as e:
            log_err(f"ERROR reloading rules: {e}")
            return

        # De-dupe: key by (timestamp, sender, destination, message).
        # Keep only a short rolling window.
        dst_key = destination or "-"
        msg_key = message or ""
        key = (int(timestamp_ms), str(sender), str(dst_key), str(msg_key))
        if key in self._recent_events:
            return
        self._recent_events.append(key)

        g = self.rules_loader.globals_cfg
        globals_raw = self.rules_loader.globals_raw  # NEW

        is_group = looks_like_group_id(group_id)
        if is_group and g.deny_groups:
            log_info(f"(group ignored) {sender}: {message}")
            return

        # Optional command prefix gate (applies to all matching + NLP).
        # If enabled, ignore messages that don't start with the prefix.
        filtered = apply_command_prefix(
            message,
            g.command_prefix,
            g.command_prefix_case_sensitive,
            g.command_prefix_strip_whitespace,
        )
        if filtered is None:
            return
        message = filtered

        dst = destination or "-"
        log_info(f"{sender} [{kind} -> {dst}]: {message}")

        # NLP fallback candidates (LiteLLM). Collected during normal scan.
        nlp_candidates: List[Dict[str, Any]] = []

        for rule in self.rules_loader.rules:
            try:
                rule_name = str(rule.get("name", "unnamed"))

                if not sender_allowed(rule.get("sender"), sender):
                    continue

                # Collect AI-router candidates (opt-in per rule).
                nlp_block = rule.get("nlp") or {}
                if isinstance(nlp_block, dict) and bool(nlp_block.get("enabled", False)):
                    nlp_candidates.append(
                        {
                            "name": rule_name,
                            "description": str(rule.get("description", "")),
                            "phrases": list(nlp_block.get("phrases") or []),
                        }
                    )

                match_mode = str(rule.get("match", g.default_match))
                case_sensitive = bool(rule.get("case_sensitive", g.default_case_sensitive))
                trigger = str(rule.get("trigger", "")).strip()
                if not trigger:
                    continue

                matched, mobj = match_trigger(message, trigger, match_mode, case_sensitive)
                if not matched:
                    continue

                cooldown_sec = safe_int(rule.get("cooldown_sec", g.default_cooldown_sec), g.default_cooldown_sec)
                max_runs_per_hour = safe_int(rule.get("max_runs_per_hour", g.default_max_runs_per_hour), g.default_max_runs_per_hour)
                ok, why = self.rate_limiter.allow(sender, rule_name, cooldown_sec, max_runs_per_hour)
                if not ok:
                    log_warn(f"↳ rule matched but blocked: {rule_name} ({why})")
                    continue

                # Reply routing
                reply_to = str(rule.get("reply_to", g.default_reply_to)).strip().lower()
                if reply_to == "sender":
                    target = sender
                elif reply_to == "admin":
                    target = g.admin or sender
                elif reply_to == "number":
                    target = str(rule.get("reply_number", "")).strip() or sender
                else:
                    target = sender

                # Dry-run
                rule_dry_run = bool(rule.get("dry_run", False))
                effective_dry_run = rule_dry_run or self.global_dry_run or g.dry_run

                # Reply formatting controls
                reply_prefix = str(rule.get("reply_prefix", g.reply_prefix))
                reply_suffix = str(rule.get("reply_suffix", g.reply_suffix))
                reply_mode = str(rule.get("reply_mode", "full")).strip().lower()

                # Split behavior
                split_reply = bool(rule.get("split_reply", g.default_split_reply))
                chunk_size = safe_int(rule.get("chunk_size", g.default_chunk_size), g.default_chunk_size)
                numbered_chunks = bool(rule.get("numbered_chunks", g.numbered_chunks))

                # ---- NEW: Plugin dispatch (Phase 0) ----
                plugin_type = str(rule.get("type", "")).strip().lower()
                if plugin_type:
                    plugin = get_plugin(plugin_type)
                    if not plugin:
                        log_err(f"↳ rule matched but unknown plugin type: {rule_name} type={plugin_type}")
                        return

                    # Validate plugin config (fail fast)
                    try:
                        plugin.validate(rule, globals_raw)
                    except Exception as ve:
                        log_err(f"↳ plugin config invalid for rule {rule_name}: {ve}")
                        return

                    ctx = {
                        "kind": kind,
                        "timestamp_ms": timestamp_ms,
                        "sender": sender,
                        "destination": destination,
                        "message": message,
                        "rule_name": rule_name,
                        "match_obj": mobj,
                    }

                    t0 = now_ts()
                    result = plugin.run(rule, globals_raw, ctx)
                    dt = now_ts() - t0

                    # Best-effort: if the plugin config includes an "action" field (like home_assistant),
                    # include it in the display string.
                    action = ""
                    try:
                        block = rule.get(plugin_type) or {}
                        if isinstance(block, dict):
                            action = str(block.get("action", "")).strip()
                    except Exception:
                        action = ""

                    display_cmd = f"plugin:{plugin_type}" + (f".{action}" if action else "")
                    exit_code = int(getattr(result, "exit_code", 0) or 0)
                    out = str(getattr(result, "body", "") or "")

                    log_info(f"↳ rule matched: {rule_name} -> {display_cmd} (t={dt:.2f}s exit={exit_code})")

                else:
                    # ---- Existing: command / command_template ----
                    if "command" in rule:
                        cmd = list(rule.get("command") or [])
                    else:
                        template = list(rule.get("command_template") or [])
                        if not template:
                            continue
                        args_schema = dict(rule.get("args") or {})
                        values: Dict[str, Any] = {}
                        if args_schema:
                            if not mobj:
                                raise ValueError("args schema provided but regex match object missing")
                            values = validate_args_from_match(args_schema, mobj)
                        cmd = render_command_template(template, values)

                    display_cmd = " ".join(shlex.quote(c) for c in cmd)
                    timeout_sec = safe_int(rule.get("timeout_sec", g.default_timeout_sec), g.default_timeout_sec)

                    log_info(f"↳ rule matched: {rule_name} -> running: {display_cmd}")

                    completed = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        timeout=timeout_sec,
                        check=False,
                    )

                    stdout = completed.stdout or ""
                    stderr = completed.stderr or ""
                    exit_code = completed.returncode

                    out = stdout
                    if stderr.strip():
                        out = out.rstrip("\n") + ("\n\n[stderr]\n" + stderr)

                # Common post-processing: redact + truncate
                out = apply_redactions(out, g.redact_regex)
                out = shorten(out, safe_int(rule.get("max_reply_chars", g.default_max_reply_chars), g.default_max_reply_chars))

                reply = build_reply(
                    rule_name=rule_name,
                    reply_mode=reply_mode,
                    exit_code=exit_code,
                    display_cmd=display_cmd,
                    out=out,
                    reply_prefix=reply_prefix,
                    reply_suffix=reply_suffix,
                )

                # Send reply (with chunking if configured)
                try:
                    if split_reply and chunk_size > 0 and len(reply) > chunk_size:
                        parts = [p for p in split_chunks_by_lines(reply, chunk_size) if p]
                        total = len(parts)

                        for idx, part in enumerate(parts, start=1):
                            if numbered_chunks and total > 1:
                                if reply_mode == "bare":
                                    prefix = f"[message {idx}/{total}]\n"
                                else:
                                    prefix = f"[{rule_name} message {idx}/{total}]\n"
                                payload = prefix + part
                            else:
                                payload = part

                            self.maybe_send(target, payload, effective_dry_run)
                            time.sleep(0.2)
                    else:
                        self.maybe_send(target, reply, effective_dry_run)

                    log_info("↳ reply handled")
                except Exception as e:
                    log_err(f"↳ FAILED to send reply: {e}")

                return  # Stop after first matched rule

            except Exception as e:
                log_err(f"↳ ERROR processing rule: {e}")
                continue

        # ----- NLP fallback (LiteLLM) -----
        # Only runs if enabled in globals and at least one rule has nlp.enabled: true.
        try:
            if g.nlp_enabled and nlp_candidates:
                decision = route_message(globals_raw, message, nlp_candidates, sender=sender)
                if not decision:
                    return

                chosen = (decision.rule or "").strip()
                if not chosen or chosen == "no_match":
                    return

                nlp_conf = float(decision.confidence or 0.0)
                min_conf = float((globals_raw.get("nlp") or {}).get("min_confidence", 0.85))
                if nlp_conf < min_conf:
                    log_info(f"(nlp) no action: confidence {nlp_conf:.2f} < {min_conf:.2f}")
                    return

                # Find chosen rule by name.
                chosen_rule: Optional[Dict[str, Any]] = None
                for r in self.rules_loader.rules:
                    if str(r.get("name", "")) == chosen:
                        chosen_rule = r
                        break
                if not chosen_rule:
                    log_err(f"(nlp) model selected unknown rule: {chosen}")
                    return

                # Only allow AI to invoke nlp-enabled rules.
                nlp_block = chosen_rule.get("nlp") or {}
                if not (isinstance(nlp_block, dict) and bool(nlp_block.get("enabled", False))):
                    log_err(f"(nlp) selected rule is not nlp-enabled: {chosen}")
                    return

                # Sender restrictions still apply.
                if not sender_allowed(chosen_rule.get("sender"), sender):
                    log_warn(f"(nlp) selected rule not allowed for sender: {chosen}")
                    return

                log_info(f"(nlp) routed '{message}' -> {chosen} (conf={nlp_conf:.2f})")

                # Execute with the same logic as a normal match.
                # We obtain match_obj if the chosen rule's trigger also matches (useful for regex args).
                match_mode = str(chosen_rule.get("match", g.default_match))
                case_sensitive = bool(chosen_rule.get("case_sensitive", g.default_case_sensitive))
                trigger = str(chosen_rule.get("trigger", "")).strip()
                _matched, mobj = match_trigger(message, trigger, match_mode, case_sensitive)
                if not _matched:
                    mobj = None

                # Reuse the existing executor by invoking the same code path: we do a local inline
                # execution that mirrors the matched section.
                rule = chosen_rule
                rule_name = str(rule.get("name", "unnamed"))

                cooldown_sec = safe_int(rule.get("cooldown_sec", g.default_cooldown_sec), g.default_cooldown_sec)
                max_runs_per_hour = safe_int(rule.get("max_runs_per_hour", g.default_max_runs_per_hour), g.default_max_runs_per_hour)
                ok, why = self.rate_limiter.allow(sender, rule_name, cooldown_sec, max_runs_per_hour)
                if not ok:
                    log_warn(f"↳ rule matched but blocked: {rule_name} ({why})")
                    return

                # Reply routing
                reply_to = str(rule.get("reply_to", g.default_reply_to)).strip().lower()
                if reply_to == "sender":
                    target = sender
                elif reply_to == "admin":
                    target = g.admin or sender
                elif reply_to == "number":
                    target = str(rule.get("reply_number", "")).strip() or sender
                else:
                    target = sender

                rule_dry_run = bool(rule.get("dry_run", False))
                effective_dry_run = rule_dry_run or self.global_dry_run or g.dry_run

                reply_prefix = str(rule.get("reply_prefix", g.reply_prefix))
                reply_suffix = str(rule.get("reply_suffix", g.reply_suffix))
                reply_mode = str(rule.get("reply_mode", "full")).strip().lower()

                split_reply = bool(rule.get("split_reply", g.default_split_reply))
                chunk_size = safe_int(rule.get("chunk_size", g.default_chunk_size), g.default_chunk_size)
                numbered_chunks = bool(rule.get("numbered_chunks", g.numbered_chunks))

                plugin_type = str(rule.get("type", "")).strip().lower()
                if plugin_type:
                    plugin = get_plugin(plugin_type)
                    if not plugin:
                        log_err(f"↳ rule matched but unknown plugin type: {rule_name} type={plugin_type}")
                        return
                    plugin.validate(rule, globals_raw)
                    ctx = {
                        "kind": kind,
                        "timestamp_ms": timestamp_ms,
                        "sender": sender,
                        "destination": destination,
                        "message": message,
                        "rule_name": rule_name,
                        "match_obj": mobj,
                    }
                    result = plugin.run(rule, globals_raw, ctx)
                    action = ""
                    block = rule.get(plugin_type) or {}
                    if isinstance(block, dict):
                        action = str(block.get("action", "")).strip()
                    display_cmd = f"plugin:{plugin_type}" + (f".{action}" if action else "")
                    exit_code = int(getattr(result, "exit_code", 0) or 0)
                    out = str(getattr(result, "body", "") or "")
                else:
                    if "command" in rule:
                        cmd = list(rule.get("command") or [])
                    else:
                        template = list(rule.get("command_template") or [])
                        if not template:
                            return
                        args_schema = dict(rule.get("args") or {})
                        values: Dict[str, Any] = {}
                        if args_schema:
                            if not mobj:
                                raise ValueError("args schema provided but regex match object missing")
                            values = validate_args_from_match(args_schema, mobj)
                        cmd = render_command_template(template, values)

                    display_cmd = " ".join(shlex.quote(c) for c in cmd)
                    timeout_sec = safe_int(rule.get("timeout_sec", g.default_timeout_sec), g.default_timeout_sec)
                    completed = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec, check=False)
                    stdout = completed.stdout or ""
                    stderr = completed.stderr or ""
                    exit_code = completed.returncode
                    out = stdout
                    if stderr.strip():
                        out = out.rstrip("\n") + ("\n\n[stderr]\n" + stderr)

                out = apply_redactions(out, g.redact_regex)
                out = shorten(out, safe_int(rule.get("max_reply_chars", g.default_max_reply_chars), g.default_max_reply_chars))

                reply = build_reply(
                    rule_name=rule_name,
                    reply_mode=reply_mode,
                    exit_code=exit_code,
                    display_cmd=display_cmd,
                    out=out,
                    reply_prefix=reply_prefix,
                    reply_suffix=reply_suffix,
                )

                if split_reply and chunk_size > 0 and len(reply) > chunk_size:
                    parts = [p for p in split_chunks_by_lines(reply, chunk_size) if p]
                    total = len(parts)
                    for idx, part in enumerate(parts, start=1):
                        if numbered_chunks and total > 1:
                            if reply_mode == "bare":
                                prefix = f"[message {idx}/{total}]\n"
                            else:
                                prefix = f"[{rule_name} message {idx}/{total}]\n"
                            payload = prefix + part
                        else:
                            payload = part
                        self.maybe_send(target, payload, effective_dry_run)
                        time.sleep(0.2)
                else:
                    self.maybe_send(target, reply, effective_dry_run)

        except Exception as e:
            log_err(f"(nlp) routing error: {e}")


# -----------------------------
# Test mode runner (no DBus)
# -----------------------------

def run_test_mode(rules_path: Path, sender: str, message: str, dry_run: bool) -> int:
    loader = ConfigLoader(rules_path)
    loader.load_if_changed()
    g = loader.globals_cfg
    globals_raw = loader.globals_raw
    rate_limiter = RateLimiter()

    log_info(f"[TEST] rules={rules_path}")
    log_info(f"[TEST] sender={sender} message={message!r}")

    filtered = apply_command_prefix(
        message,
        g.command_prefix,
        g.command_prefix_case_sensitive,
        g.command_prefix_strip_whitespace,
    )
    if filtered is None:
        log_warn("[TEST] message ignored (missing required command_prefix)")
        return 1
    message = filtered

    nlp_candidates: List[Dict[str, Any]] = []

    for rule in loader.rules:
        try:
            rule_name = str(rule.get("name", "unnamed"))
            if not sender_allowed(rule.get("sender"), sender):
                continue

            nlp_block = rule.get("nlp") or {}
            if isinstance(nlp_block, dict) and bool(nlp_block.get("enabled", False)):
                nlp_candidates.append(
                    {
                        "name": rule_name,
                        "description": str(rule.get("description", "")),
                        "phrases": list(nlp_block.get("phrases") or []),
                    }
                )

            match_mode = str(rule.get("match", g.default_match))
            case_sensitive = bool(rule.get("case_sensitive", g.default_case_sensitive))
            trigger = str(rule.get("trigger", "")).strip()
            if not trigger:
                continue

            matched, mobj = match_trigger(message, trigger, match_mode, case_sensitive)
            if not matched:
                continue

            cooldown_sec = safe_int(rule.get("cooldown_sec", g.default_cooldown_sec), g.default_cooldown_sec)
            max_runs_per_hour = safe_int(rule.get("max_runs_per_hour", g.default_max_runs_per_hour), g.default_max_runs_per_hour)
            ok, why = rate_limiter.allow(sender, rule_name, cooldown_sec, max_runs_per_hour)
            if not ok:
                log_warn(f"[TEST] matched but rate-limited: {rule_name} ({why})")
                continue

            plugin_type = str(rule.get("type", "")).strip().lower()
            if plugin_type:
                plugin = get_plugin(plugin_type)
                if not plugin:
                    log_err(f"[TEST] unknown plugin type: {plugin_type}")
                    continue
                plugin.validate(rule, globals_raw)

                ctx = {
                    "kind": "test",
                    "timestamp_ms": int(now_ts() * 1000),
                    "sender": sender,
                    "destination": None,
                    "message": message,
                    "rule_name": rule_name,
                    "match_obj": mobj,
                }

                result = plugin.run(rule, globals_raw, ctx)
                exit_code = int(getattr(result, "exit_code", 0) or 0)
                out = str(getattr(result, "body", "") or "")

                action = ""
                try:
                    block = rule.get(plugin_type) or {}
                    if isinstance(block, dict):
                        action = str(block.get("action", "")).strip()
                except Exception:
                    action = ""

                display_cmd = f"plugin:{plugin_type}" + (f".{action}" if action else "")
                log_info(f"[TEST] matched plugin rule: {rule_name} -> {display_cmd}")

            else:
                if "command" in rule:
                    cmd = list(rule.get("command") or [])
                else:
                    template = list(rule.get("command_template") or [])
                    if not template:
                        continue
                    args_schema = dict(rule.get("args") or {})
                    values: Dict[str, Any] = {}
                    if args_schema:
                        if not mobj:
                            raise ValueError("args schema provided but regex match object missing")
                        values = validate_args_from_match(args_schema, mobj)
                    cmd = render_command_template(template, values)

                display_cmd = " ".join(shlex.quote(c) for c in cmd)
                timeout_sec = safe_int(rule.get("timeout_sec", g.default_timeout_sec), g.default_timeout_sec)

                log_info(f"[TEST] matched rule: {rule_name}")
                log_info(f"[TEST] would run: {display_cmd}")

                completed = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeout_sec,
                    check=False,
                )

                stdout = completed.stdout or ""
                stderr = completed.stderr or ""
                exit_code = completed.returncode

                out = stdout
                if stderr.strip():
                    out = out.rstrip("\n") + ("\n\n[stderr]\n" + stderr)

            out = apply_redactions(out, g.redact_regex)
            out = shorten(out, safe_int(rule.get("max_reply_chars", g.default_max_reply_chars), g.default_max_reply_chars))

            reply_prefix = str(rule.get("reply_prefix", g.reply_prefix))
            reply_suffix = str(rule.get("reply_suffix", g.reply_suffix))
            reply_mode = str(rule.get("reply_mode", "full")).strip().lower()

            reply = build_reply(
                rule_name=rule_name,
                reply_mode=reply_mode,
                exit_code=exit_code,
                display_cmd=display_cmd,
                out=out,
                reply_prefix=reply_prefix,
                reply_suffix=reply_suffix,
            )

            split_reply = bool(rule.get("split_reply", g.default_split_reply))
            chunk_size = safe_int(rule.get("chunk_size", g.default_chunk_size), g.default_chunk_size)
            numbered_chunks = bool(rule.get("numbered_chunks", g.numbered_chunks))

            effective_dry_run = dry_run or g.dry_run or bool(rule.get("dry_run", False))

            print("\n===== TEST RESULT =====")
            print(f"rule: {rule_name}")
            print(f"exit: {exit_code}")
            print(f"dry_run: {effective_dry_run}")
            print("reply:")
            if split_reply and chunk_size > 0 and len(reply) > chunk_size:
                parts = [p for p in split_chunks_by_lines(reply, chunk_size) if p]
                total = len(parts)
                for idx, part in enumerate(parts, start=1):
                    if numbered_chunks and total > 1:
                        if reply_mode == "bare":
                            prefix = f"[message {idx}/{total}]\n"
                        else:
                            prefix = f"[{rule_name} message {idx}/{total}]\n"
                        print(prefix + part)
                    else:
                        print(part)
                    print("-----")
            else:
                print(reply)
            print("=======================\n")

            return 0

        except Exception as e:
            log_err(f"[TEST] error processing rule: {e}")
            continue

    log_warn("[TEST] no matching rule found")

    # NLP fallback in test mode
    try:
        if g.nlp_enabled and nlp_candidates:
            decision = route_message(globals_raw, message, nlp_candidates, sender=sender)
            if decision and decision.rule and decision.rule != "no_match":
                nlp_conf = float(decision.confidence or 0.0)
                min_conf = float((globals_raw.get("nlp") or {}).get("min_confidence", 0.85))
                if nlp_conf < min_conf:
                    log_warn(f"[TEST] NLP confidence too low: {nlp_conf:.2f} < {min_conf:.2f}")
                    return 1
                print(f"\n[TEST] NLP routed to rule: {decision.rule} (conf={nlp_conf:.2f})\n")
                # Re-run test mode by forcing the message to be the chosen rule's trigger.
                # Easiest: run the test loop again but treat any match as acceptable.
                for rule in loader.rules:
                    if str(rule.get("name", "")) != decision.rule:
                        continue
                    # Force execution without requiring match.
                    # For regex-arg rules, matching may still be required.
                    return run_test_mode(rules_path, sender, str(rule.get("trigger", message)), dry_run=dry_run)
    except Exception as e:
        log_err(f"[TEST] NLP routing error: {e}")

    return 1


# -----------------------------
# Main
# -----------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Signal CLI Agent (DBus-driven rule engine)")
    parser.add_argument("rules_path", nargs="?", default="./rules.yaml", help="Path to root rules.yaml")

    parser.add_argument("--test", action="store_true", help="Test mode: evaluate one message and exit (no DBus)")
    parser.add_argument("--sender", type=str, default="", help="Sender number for --test")
    parser.add_argument("--message", type=str, default="", help="Message text for --test")

    parser.add_argument("--dry-run", action="store_true", help="Global dry-run: suppress sending replies")

    parser.add_argument("--log-level", type=str, default="", help="Override log level (INFO, DEBUG, ...)")
    parser.add_argument("--log-format", type=str, default="", help="Override log format (text|json)")
    parser.add_argument("--log-file", type=str, default="", help="Write logs to this file instead of stdout")

    args = parser.parse_args()

    rules_path = Path(args.rules_path).resolve()
    if not rules_path.exists():
        print(f"ERROR: rules file not found: {rules_path}", file=sys.stderr)
        return 2

    loader = ConfigLoader(rules_path)
    loader.load_if_changed()
    g = loader.globals_cfg

    level = args.log_level or g.log_level
    fmt = args.log_format or g.log_format
    logfile = args.log_file or g.log_file
    setup_logging(level=level, logfile=logfile, log_format=fmt)

    def _sigint(*_args: Any) -> None:
        log_info("Stopping…")
        raise KeyboardInterrupt

    pysignal.signal(pysignal.SIGINT, _sigint)

    if args.test:
        if not args.sender or not args.message:
            print("ERROR: --test requires --sender and --message", file=sys.stderr)
            return 2
        return run_test_mode(rules_path, args.sender, args.message, dry_run=args.dry_run)

    global_dry_run = bool(args.dry_run or g.dry_run)

    agent = SignalAgent(rules_path, global_dry_run=global_dry_run)
    if GLib is None:
        raise RuntimeError("GLib not available; cannot run main loop")

    GLib.MainLoop().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())