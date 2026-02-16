#!/usr/bin/env python3
"""
signal-agent.py

DBus listener for signal-cli + YAML rule engine.

NEW:
- rules_dir: load rules from a directory of YAML files (rules.d/*.yaml)
- reply_mode: full | output | bare
- numbered_chunks: prefix split chunks with: "[<rule> message i/n]" (only when 2+ chunks)

Usage:
  python3 signal-agent.py ./rules.yaml
"""

from __future__ import annotations

import os
import re
import sys
import time
import yaml
import shlex
import signal as pysignal
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from collections import defaultdict, deque

from pydbus import SessionBus
from gi.repository import GLib


# -----------------------------
# Utilities
# -----------------------------

def now_ts() -> float:
    return time.time()


def fmt_ts(ts: Optional[float] = None) -> str:
    dt = datetime.fromtimestamp(ts or now_ts())
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str) -> None:
    print(f"[{fmt_ts()}] {msg}", flush=True)


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
            pass
    return out


def split_chunks_by_lines(s: str, chunk_size: int) -> List[str]:
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
        )


# -----------------------------
# Matching & command building
# -----------------------------

def normalize_text(s: str, case_sensitive: bool) -> str:
    return s if case_sensitive else s.lower()


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
# Config loader (rules.yaml + rules.d/*.yaml)
# -----------------------------

class ConfigLoader:
    def __init__(self, root_path: Path) -> None:
        self.root_path = root_path
        self._mtimes: Dict[Path, float] = {}
        self.globals_cfg: GlobalsCfg = GlobalsCfg()
        self.rules_dir: Optional[Path] = None
        self.rules: List[Dict[str, Any]] = []

    def _list_rule_files(self, rules_dir: Path) -> List[Path]:
        if not rules_dir.exists():
            return []
        return sorted([p for p in rules_dir.iterdir() if p.is_file() and p.suffix in (".yml", ".yaml")])

    def _load_yaml_file(self, p: Path) -> Any:
        txt = p.read_text(encoding="utf-8")
        return yaml.safe_load(txt) or {}

    def load_if_changed(self) -> None:
        if not self.root_path.exists():
            raise FileNotFoundError(f"Rules file not found: {self.root_path}")

        # Determine which files we should consider for reload (root + rule files)
        root_data = self._load_yaml_file(self.root_path)
        globals_dict = root_data.get("globals", {}) or {}
        self.globals_cfg = GlobalsCfg.from_dict(globals_dict)

        rules_dir_val = root_data.get("rules_dir")
        if rules_dir_val:
            rules_dir = Path(os.path.expanduser(str(rules_dir_val))).resolve()
        else:
            # default: rules.d next to rules.yaml
            rules_dir = (self.root_path.parent / "rules.d").resolve()

        rule_files = self._list_rule_files(rules_dir)

        # Build file list to check mtimes
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

        # Reload everything
        merged_rules: List[Dict[str, Any]] = []

        # rules inline in root file (optional)
        root_rules = root_data.get("rules")
        if isinstance(root_rules, list):
            merged_rules.extend(root_rules)

        # rules from rules.d/*.yaml
        for rf in rule_files:
            data = self._load_yaml_file(rf)

            # Allow file formats:
            # 1) {rules: [ ... ]}
            # 2) [ ... ]  (list of rules)
            # 3) {name: ..., trigger: ...} (single rule)
            if isinstance(data, dict) and "rules" in data and isinstance(data["rules"], list):
                merged_rules.extend(data["rules"])
            elif isinstance(data, list):
                merged_rules.extend(data)
            elif isinstance(data, dict) and "name" in data:
                merged_rules.append(data)

        # Update mtimes cache
        self._mtimes = {}
        for p in files_to_check:
            try:
                self._mtimes[p] = p.stat().st_mtime
            except FileNotFoundError:
                self._mtimes[p] = -1.0

        self.rules_dir = rules_dir
        self.rules = merged_rules

        log(f"Reloaded rules: {self.root_path} + {rules_dir} (files={len(rule_files)}, rules={len(self.rules)})")


# -----------------------------
# Agent
# -----------------------------

class SignalAgent:
    def __init__(self, rules_path: Path) -> None:
        self.rules_loader = ConfigLoader(rules_path)
        self.rate_limiter = RateLimiter()

        self.bus = SessionBus()
        self.signal = self.bus.get("org.asamk.Signal")

        log("✓ Connected to org.asamk.Signal on the session bus")
        log(f"Listening… (root rules: {rules_path})")

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

    def handle_incoming(self, kind: str, timestamp_ms: int, sender: str, destination: Optional[str], group_id: Any, message: str) -> None:
        try:
            self.rules_loader.load_if_changed()
        except Exception as e:
            log(f"ERROR reloading rules: {e}")
            return

        g = self.rules_loader.globals_cfg
        is_group = looks_like_group_id(group_id)
        if is_group and g.deny_groups:
            log(f"(group ignored) {sender}: {message}")
            return

        src = kind
        dst = destination or "-"
        log(f"{sender} [{src} -> {dst}]: {message}")

        for rule in self.rules_loader.rules:
            try:
                rule_name = str(rule.get("name", "unnamed"))
                rule_sender = str(rule.get("sender", "")).strip()
                if rule_sender and rule_sender != sender:
                    continue

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
                    log(f"↳ rule matched but blocked: {rule_name} ({why})")
                    continue

                # Build command
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

                log(f"↳ rule matched: {rule_name} -> running: {display_cmd}")
                completed = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec, check=False)

                stdout = completed.stdout or ""
                stderr = completed.stderr or ""
                exit_code = completed.returncode

                out = stdout
                if stderr.strip():
                    out = out.rstrip("\n") + ("\n\n[stderr]\n" + stderr)

                out = apply_redactions(out, g.redact_regex)
                out = shorten(out, safe_int(rule.get("max_reply_chars", g.default_max_reply_chars), g.default_max_reply_chars))

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

                # Reply format
                reply_prefix = str(rule.get("reply_prefix", g.reply_prefix))
                reply_suffix = str(rule.get("reply_suffix", g.reply_suffix))
                reply_mode = str(rule.get("reply_mode", "full")).strip().lower()

                if reply_mode == "output":
                    reply_body = f"[{rule_name}]\n" + out
                elif reply_mode == "bare":
                    reply_body = out
                else:
                    reply_body = f"[{rule_name}] exit={exit_code}\n$ {display_cmd}\n" + out

                reply = reply_prefix + reply_body + reply_suffix

                # Split behavior
                split_reply = bool(rule.get("split_reply", g.default_split_reply))
                chunk_size = safe_int(rule.get("chunk_size", g.default_chunk_size), g.default_chunk_size)
                numbered_chunks = bool(rule.get("numbered_chunks", g.numbered_chunks))

                try:
                    if split_reply and chunk_size > 0 and len(reply) > chunk_size:
                        parts = [p for p in split_chunks_by_lines(reply, chunk_size) if p]
                        total = len(parts)

                        for idx, part in enumerate(parts, start=1):
                            if numbered_chunks and total > 1:
                                # If reply_mode is bare, rule name isn't present; keep a generic prefix.
                                if reply_mode == "bare":
                                    prefix = f"[message {idx}/{total}]\n"
                                else:
                                    prefix = f"[{rule_name} message {idx}/{total}]\n"
                                payload = prefix + part
                            else:
                                payload = part

                            send_signal_message(self.signal, target, payload)
                            time.sleep(0.2)
                    else:
                        send_signal_message(self.signal, target, reply)

                    log("↳ reply sent")
                except Exception as e:
                    log(f"↳ FAILED to send reply: {e}")

                return  # stop after first matched rule

            except Exception as e:
                log(f"↳ ERROR processing rule: {e}")
                continue


def main() -> int:
    rules_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("./rules.yaml")
    if not rules_path.exists():
        log(f"ERROR: rules file not found: {rules_path}")
        return 2

    def _sigint(*_args: Any) -> None:
        log("Stopping…")
        raise KeyboardInterrupt

    pysignal.signal(pysignal.SIGINT, _sigint)

    SignalAgent(rules_path)
    GLib.MainLoop().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())