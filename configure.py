#!/usr/bin/env python3
"""
configure.py

Generates environment-specific files from .in templates:

  - rules.yaml                 (from rules.yaml.in)
  - systemd/*.service          (from templates/systemd/*.service.in)
  - rules.d/*.yaml             (from templates/rules/*.yaml.in)   [SHIPPED TEMPLATES ONLY]

Prompts for a Signal phone number, validates it, confirms it, and injects it.

IMPORTANT SAFETY RULE:
- rules.d/ is your "production rules" directory.
- This script ONLY renders shipped templates from templates/rules/*.yaml.in into rules.d/*.yaml.
- It will not touch other rules in rules.d/ (e.g. custody-week.yaml) unless a shipped template has the same output filename.

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
from typing import Dict, List, Tuple


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
        phone = input("Enter Signal phone number in E.164 format (e.g. +15551234567): ").strip()
        if not E164_RE.match(phone):
            eprint("Invalid format. Must be E.164 like +15551234567 (7–15 digits).")
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
      - templates/systemd/*.service.in -> systemd/*.service
      - templates/rules/*.yaml.in -> rules.d/*.yaml   (SHIPPED TEMPLATES ONLY)

    NOTE:
      - rules.d/ may also contain user rules (hand-authored). This generator does not scan rules.d/*.in anymore.
      - Only templates under templates/ are considered "owned outputs".
    """
    pairs: List[Tuple[Path, Path]] = []

    # rules.yaml.in
    root_rules_in = repo_root / "rules.yaml.in"
    if root_rules_in.exists():
        pairs.append((root_rules_in, repo_root / "rules.yaml"))
    else:
        raise FileNotFoundError(f"Missing required template: {root_rules_in}")

    # templates/systemd/*.service.in -> systemd/*.service
    tpl_systemd_dir = repo_root / "templates" / "systemd"
    out_systemd_dir = repo_root / "systemd"
    if not tpl_systemd_dir.exists():
        raise FileNotFoundError(f"Missing templates directory: {tpl_systemd_dir}")

    for p in sorted(tpl_systemd_dir.glob("*.service.in")):
        out_name = p.name[:-3]  # drop trailing ".in"
        out_path = out_systemd_dir / out_name
        pairs.append((p, out_path))

    # templates/rules/*.yaml.in -> rules.d/*.yaml
    tpl_rules_dir = repo_root / "templates" / "rules"
    out_rules_dir = repo_root / "rules.d"
    if not tpl_rules_dir.exists():
        raise FileNotFoundError(f"Missing templates directory: {tpl_rules_dir}")

    for p in sorted(tpl_rules_dir.glob("*.yaml.in")):
        out_name = p.name[:-3]  # drop trailing ".in"
        out_path = out_rules_dir / out_name
        pairs.append((p, out_path))

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

    try:
        pairs = iter_template_files(repo_root)
    except Exception as e:
        eprint(f"Template discovery error: {e}")
        return 2

    generated: List[Path] = []
    for tpl_path, out_path in pairs:
        tpl = read_text(tpl_path)
        rendered = render_template(tpl, vars)

        # Safety: only overwrite if the output corresponds to a shipped template
        # (which is true for every out_path derived from iter_template_files).
        write_text(out_path, rendered)
        generated.append(out_path)

    print("\nGenerated:")
    for p in generated:
        print(f"  {p}")

    maybe_enable_linger(user)

    print("\nNext steps:")
    print("  make install     # runs configure, installs user units, daemon-reload - only required on first setup or when templates change")
    print("  make start       # enable + start services")
    print("  make status      # show service status")
    print("  make logs        # tail logs")
    print("  make uninstall   # remove installed services (does not touch rules.d/)\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())