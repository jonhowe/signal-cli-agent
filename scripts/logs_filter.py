#!/usr/bin/env python3
"""Filter noisy signal-cli journal output.

This script is intended to be used by `make logs`.

Behavior:
  - Pass through all non-signal-cli lines unchanged (eg. signal-agent logs).
  - For signal-cli lines, group them into "message blocks" that begin with
    an "Envelope from:" line.
  - Only print blocks that look like actionable messages:
      * must contain a "Body:" line (receipts/sync/status messages are skipped)
      * if globals.command_prefix is set (non-empty), the body must start with
        that prefix (optionally after stripping leading whitespace)

The goal is to keep `make logs` readable while still showing the full block
for messages that the agent is likely to act on.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple


@dataclass
class PrefixConfig:
    prefix: str = ""
    case_sensitive: bool = False
    strip_whitespace: bool = True


def _strip_quotes(s: str) -> str:
    s = s.strip()
    if (len(s) >= 2) and ((s[0] == s[-1]) and s[0] in ('"', "'")):
        return s[1:-1]
    return s


def load_prefix_config_from_rules_yaml(path: Path) -> PrefixConfig:
    """Best-effort parse of globals.command_prefix settings.

    Avoids requiring PyYAML; we only need a few scalar values.
    """
    cfg = PrefixConfig()
    try:
        text = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return cfg

    in_globals = False
    globals_indent: Optional[int] = None

    for line in text:
        # Remove comments, but keep quoted '#'
        if "#" in line:
            # crude but effective: split on first unquoted '#'
            out = []
            in_sq = in_dq = False
            for ch in line:
                if ch == "'" and not in_dq:
                    in_sq = not in_sq
                elif ch == '"' and not in_sq:
                    in_dq = not in_dq
                if ch == "#" and not in_sq and not in_dq:
                    break
                out.append(ch)
            line = "".join(out)
        if not line.strip():
            continue

        m = re.match(r"^(\s*)globals\s*:\s*$", line)
        if m:
            in_globals = True
            globals_indent = len(m.group(1))
            continue

        if in_globals:
            # Leave globals section when indentation decreases
            indent = len(line) - len(line.lstrip(" "))
            if globals_indent is not None and indent <= globals_indent and not re.match(r"^\s*#", line):
                in_globals = False
                globals_indent = None
                continue

            kv = re.match(r"^\s*([A-Za-z0-9_]+)\s*:\s*(.*?)\s*$", line)
            if not kv:
                continue
            key, val = kv.group(1), kv.group(2)
            val = _strip_quotes(val)

            if key == "command_prefix":
                cfg.prefix = val
            elif key == "command_prefix_case_sensitive":
                cfg.case_sensitive = val.lower() in ("true", "yes", "1", "on")
            elif key == "command_prefix_strip_whitespace":
                cfg.strip_whitespace = val.lower() in ("true", "yes", "1", "on")

    return cfg


def body_matches_prefix(body: str, cfg: PrefixConfig) -> bool:
    if not cfg.prefix:
        return True
    b = body
    if cfg.strip_whitespace:
        b = b.lstrip()
    p = cfg.prefix
    if cfg.case_sensitive:
        return b.startswith(p)
    return b.lower().startswith(p.lower())


def extract_body_from_block(block: List[str]) -> Optional[str]:
    # Look for a line like: "...:   Body: <text>"
    for ln in block:
        if " Body:" in ln or ln.rstrip().endswith("Body:"):
            # Match 'Body:' anywhere after journal prefix.
            m = re.search(r"\bBody:\s*(.*)$", ln)
            if m:
                return m.group(1)
            return ""
    return None


def is_signal_cli_line(line: str) -> bool:
    # journalctl short format: "... host signal-cli[pid]: message"
    return ":" in line and " signal-cli[" in line


def is_new_envelope_line(line: str) -> bool:
    return is_signal_cli_line(line) and ("Envelope from:" in line)


def flush_block(block: List[str], cfg: PrefixConfig) -> None:
    if not block:
        return
    body = extract_body_from_block(block)
    if body is None:
        return  # not actionable (receipts/sync/status)
    if not body_matches_prefix(body, cfg):
        return
    for ln in block:
        sys.stdout.write(ln)


def run(cfg: PrefixConfig) -> int:
    block: List[str] = []
    in_block = False

    for line in sys.stdin:
        # Preserve original newlines.
        if is_new_envelope_line(line):
            flush_block(block, cfg)
            block = [line]
            in_block = True
            continue

        if is_signal_cli_line(line):
            if in_block:
                block.append(line)
            # If we see signal-cli noise before first envelope, drop it.
            continue

        # Non signal-cli line: first flush any pending block.
        flush_block(block, cfg)
        block = []
        in_block = False
        sys.stdout.write(line)

    flush_block(block, cfg)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Filter signal-cli noise from journal output")
    ap.add_argument(
        "--rules",
        default="./rules.yaml",
        help="Path to generated rules.yaml (used to read globals.command_prefix)",
    )
    ap.add_argument(
        "--prefix",
        default=None,
        help="Override prefix (otherwise read from rules.yaml). Use empty string to disable.",
    )
    ap.add_argument(
        "--case-sensitive",
        action="store_true",
        help="Override command_prefix_case_sensitive (only when --prefix is provided)",
    )
    ap.add_argument(
        "--no-strip-ws",
        action="store_true",
        help="Override command_prefix_strip_whitespace=false (only when --prefix is provided)",
    )
    args = ap.parse_args()

    cfg = load_prefix_config_from_rules_yaml(Path(args.rules))
    if args.prefix is not None:
        cfg.prefix = args.prefix
        cfg.case_sensitive = bool(args.case_sensitive)
        cfg.strip_whitespace = not bool(args.no_strip_ws)

    return run(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
