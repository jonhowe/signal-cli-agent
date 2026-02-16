#!/usr/bin/env python3
"""
configure.py

Generates environment-specific files from .in templates:
  - rules.yaml                 (from rules.yaml.in)
  - systemd/*.service          (from systemd/*.service.in)
  - rules.d/*.yaml             (from rules.d/*.yaml.in)   

Prompts for a Signal phone number, validates it, confirms it, and injects it.

Also offers to enable systemd user lingering so user services can start at boot:
  sudo loginctl enable-linger <user>

Usage:
  python3 configure.py
"""

from __future__ import annotations

import getpass
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Tuple


E164_RE = re.compile(r"^\+\d{7,15}$")


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def write_text(p: Path, s: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8")


def prompt_phone() -> str:
    while True:
        phone = input("Enter Signal phone number in E.164 format (e.g. +13213213210): ").strip()
        if not E164_RE.match(phone):
            eprint("Invalid format. Must be E.164 like +13213213210 (7–15 digits).")
            continue

        confirm = input(f"You entered {phone}. Is that correct? [y/N]: ").strip().lower()
        if confirm in ("y", "yes"):
            return phone

        print("Okay — let's try again.\n")


def find_required_binary(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise FileNotFoundError(f"Could not find '{name}' in PATH.")
    return path


def render_template(template: str, vars: Dict[str, str]) -> str:
    out = template
    for k, v in vars.items():
        out = out.replace(f"@{k}@", v)
    return out


def check_linger(user: str) -> str:
    # Returns "yes", "no", or "unknown"
    try:
        proc = subprocess.run(
            ["loginctl", "show-user", user],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            return "unknown"
        for line in proc.stdout.splitlines():
            if line.strip().startswith("Linger="):
                return line.split("=", 1)[1].strip().lower()
    except Exception:
        pass
    return "unknown"


def maybe_enable_linger(user: str) -> None:
    linger = check_linger(user)
    if linger == "yes":
        print(f"Linger is already enabled for {user} (Linger=yes).")
        return

    print("\nUser services normally start only when you log in.")
    print("Enabling 'linger' allows systemd --user services to start at boot and keep running after logout.")
    print(f"Current status for {user}: Linger={linger}")

    ans = input(
        f"Enable linger for {user} now? This will run: sudo loginctl enable-linger {user}  [y/N]: "
    ).strip().lower()
    if ans not in ("y", "yes"):
        print("Skipping linger enable.")
        return

    try:
        subprocess.run(["sudo", "loginctl", "enable-linger", user], check=False)
        new_status = check_linger(user)
        print(f"Updated status: Linger={new_status}")
    except Exception as e:
        eprint(f"Failed to enable linger: {e}")


def iter_template_files(repo_root: Path) -> List[Tuple[Path, Path]]:
    """
    Returns list of (template_path, output_path) for supported templates.

    Supported:
      - rules.yaml.in -> rules.yaml
      - systemd/*.in -> systemd/* (same filename without .in)
      - rules.d/*.yaml.in -> rules.d/*.yaml
    """
    pairs: List[Tuple[Path, Path]] = []

    # rules.yaml.in
    root_rules_in = repo_root / "rules.yaml.in"
    if root_rules_in.exists():
        pairs.append((root_rules_in, repo_root / "rules.yaml"))

    # systemd/*.in
    systemd_dir = repo_root / "systemd"
    if systemd_dir.exists():
        for p in sorted(systemd_dir.glob("*.in")):
            out = p.with_suffix("")  # removes ".in"
            pairs.append((p, out))

    # rules.d/*.yaml.in  <-- NEW
    rulesd_dir = repo_root / "rules.d"
    if rulesd_dir.exists():
        for p in sorted(rulesd_dir.glob("*.yaml.in")):
            # remove only the trailing ".in"
            out = Path(str(p)[:-3])
            pairs.append((p, out))

    return pairs


def main() -> int:
    repo_root = Path(__file__).resolve().parent
    user = getpass.getuser()

    phone = prompt_phone()

    # Discover paths
    python_path = sys.executable or find_required_binary("python3")
    signal_cli_path = find_required_binary("signal-cli")
    agent_script = repo_root / "signal-agent.py"
    rules_yaml = repo_root / "rules.yaml"

    if not agent_script.exists():
        eprint(f"Expected main script at: {agent_script}")
        return 3

    vars: Dict[str, str] = {
        "PHONE": phone,
        "REPO": str(repo_root),
        "PYTHON": python_path,
        "SIGNAL_CLI": signal_cli_path,
        "AGENT_SCRIPT": str(agent_script),
        "RULES_YAML": str(rules_yaml),
        "USER": user,
    }

    pairs = iter_template_files(repo_root)
    if not pairs:
        eprint("No templates found. Expected at least rules.yaml.in or systemd/*.in or rules.d/*.yaml.in")
        return 2

    generated: List[Path] = []
    for tpl_path, out_path in pairs:
        tpl = read_text(tpl_path)
        rendered = render_template(tpl, vars)
        write_text(out_path, rendered)
        generated.append(out_path)

    print("\nGenerated:")
    for p in generated:
        print(f"  {p}")

    maybe_enable_linger(user)

    print("\nNext steps:")
    print("  make install     # runs configure, installs + enables + starts services. Only needs to be run again if you change templates or phone number")
    print("  make status      # show service status")
    print("  make logs        # tail logs")
    print("  make uninstall   # remove services\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())