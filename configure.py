#!/usr/bin/env python3
"""
configure.py

Render environment-specific files from shipped *.in templates.

This repo supports **two operational styles**:

1) **Container-first (recommended)**
   - You run signal-cli + signal-cli-agent inside Docker.
   - Host only needs Docker (no host signal-cli install required).
   - Config lives in a host directory mounted to /config in the container.

2) **Non-container / systemd user services (legacy / optional)**
   - You run signal-cli + the agent directly on the host.
   - systemd --user units are generated from templates and can be installed.

This script can generate files for either style via --mode.

Generated templates:
  - rules.yaml.in                       -> rules.yaml
  - templates/rules/*.yaml.in           -> rules.d/*.yaml        (shipped examples only)
  - templates/systemd/*.service.in      -> systemd/*.service     (systemd mode only)

Safety model:
- rules.d/ is your "production rules" directory.
- This script ONLY renders shipped templates from templates/rules/*.yaml.in into rules.d/*.yaml.
- It will not touch other rules in rules.d/ (e.g. my-custom-rule.yaml).
- If a shipped template's output filename matches an existing file, it WILL overwrite it (because that file
  is considered "owned" by the shipped template). Use --force to overwrite in non-interactive mode.

Usage examples:

  # Container-first: generate ./config/rules.yaml + ./config/rules.d/*
  python3 configure.py --mode container --phone +15551234567

  # Container-first but write elsewhere (e.g. an absolute path)
  python3 configure.py --mode container --config-dir /path/to/config --phone +15551234567

  # systemd user services: generate repo-root rules.yaml, systemd/*, rules.d/*
  python3 configure.py --mode systemd --phone +15551234567

  # Non-interactive (CI/provisioning)
  python3 configure.py --mode container --phone +15551234567 --non-interactive --force
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Tuple

E164_RE = re.compile(r"^\+\d{7,15}$")
MANIFEST = ".generated-manifest.json"


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def write_text(p: Path, s: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8")


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


def template_needs_phone(template_text: str) -> bool:
    return "@PHONE@" in template_text


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


def check_linger(user: str) -> str:
    """Return 'yes', 'no', or 'unknown'."""
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


def maybe_enable_linger(user: str, non_interactive: bool) -> None:
    linger = check_linger(user)
    if linger == "yes":
        print(f"Linger is already enabled for {user} (Linger=yes).")
        return

    print("\nUser services normally start only when you log in.")
    print("Enabling 'linger' allows systemd --user services to start at boot and keep running after logout.")
    print(f"Current status for {user}: Linger={linger}")

    if non_interactive:
        print("Non-interactive mode: skipping linger prompt.")
        return

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


def _resolve_config_dir(repo_root: Path, config_dir_arg: str) -> Path:
    p = Path(os.path.expanduser(config_dir_arg.strip() or "./config"))
    if not p.is_absolute():
        p = (repo_root / p).resolve()
    else:
        p = p.resolve()
    return p


def iter_template_files(repo_root: Path, mode: str, config_dir: Path) -> List[Tuple[Path, Path]]:
    """
    Return list of (template_path, output_path) for the selected mode.

    container mode:
      - rules.yaml.in -> <config_dir>/rules.yaml
      - templates/rules/*.yaml.in -> <config_dir>/rules.d/*.yaml

    systemd mode:
      - rules.yaml.in -> <repo_root>/rules.yaml
      - templates/systemd/*.service.in -> <repo_root>/systemd/*.service
      - templates/rules/*.yaml.in -> <repo_root>/rules.d/*.yaml
    """
    if mode not in ("container", "systemd", "both"):
        raise ValueError("mode must be one of: container, systemd, both")

    pairs: List[Tuple[Path, Path]] = []

    # Base templates
    root_rules_in = repo_root / "rules.yaml.in"
    if not root_rules_in.exists():
        raise FileNotFoundError(f"Missing required template: {root_rules_in}")

    tpl_rules_dir = repo_root / "templates" / "rules"
    if not tpl_rules_dir.exists():
        raise FileNotFoundError(f"Missing templates directory: {tpl_rules_dir}")

    tpl_systemd_dir = repo_root / "templates" / "systemd"
    if (mode in ("systemd", "both")) and (not tpl_systemd_dir.exists()):
        raise FileNotFoundError(f"Missing templates directory: {tpl_systemd_dir}")

    # Container outputs
    if mode in ("container", "both"):
        pairs.append((root_rules_in, config_dir / "rules.yaml"))
        out_rules_dir = config_dir / "rules.d"
        for p in sorted(tpl_rules_dir.glob("*.yaml.in")):
            out_name = p.name[:-3]  # drop trailing ".in"
            pairs.append((p, out_rules_dir / out_name))

    # systemd/host outputs
    if mode in ("systemd", "both"):
        pairs.append((root_rules_in, repo_root / "rules.yaml"))

        out_systemd_dir = repo_root / "systemd"
        for p in sorted(tpl_systemd_dir.glob("*.service.in")):
            out_name = p.name[:-3]  # drop trailing ".in"
            pairs.append((p, out_systemd_dir / out_name))

        out_rules_dir = repo_root / "rules.d"
        for p in sorted(tpl_rules_dir.glob("*.yaml.in")):
            out_name = p.name[:-3]  # drop trailing ".in"
            pairs.append((p, out_rules_dir / out_name))

    return pairs


def write_manifest(repo_root: Path, mode: str, config_dir: Path, outputs: List[Path]) -> None:
    manifest_path = repo_root / MANIFEST
    payload = {
        "mode": mode,
        "config_dir": str(config_dir),
        "generated": [str(p.resolve()) for p in outputs],
    }
    write_text(manifest_path, json.dumps(payload, indent=2))
    print(f"  {manifest_path}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Render shipped templates into environment-specific files.")
    ap.add_argument(
        "--mode",
        choices=["container", "systemd", "both"],
        default="container",
        help="What to generate: container config (default), systemd units, or both.",
    )
    ap.add_argument(
        "--config-dir",
        default="./config",
        help="Where to write container config (rules.yaml + rules.d/). Relative paths are resolved from the repo root.",
    )
    ap.add_argument("--phone", default="", help="Signal phone number in E.164 format (e.g. +15551234567)")
    ap.add_argument("--non-interactive", action="store_true", help="Do not prompt; fail if required values missing")
    ap.add_argument("--force", action="store_true", help="Allow overwriting owned outputs in non-interactive mode")
    ap.add_argument("--signal-cli-path", default="", help="Override path to signal-cli (systemd mode only)")
    args = ap.parse_args()

    repo_root = Path(__file__).resolve().parent
    config_dir = _resolve_config_dir(repo_root, args.config_dir)
    user = getpass.getuser()

    # Discover paths (systemd mode only requires signal-cli on the host)
    python_path = sys.executable or find_required_binary("python3")
    need_signal_cli = args.mode in ("systemd", "both")
    if need_signal_cli:
        signal_cli_path = args.signal_cli_path.strip() or find_required_binary("signal-cli")
    else:
        # Container mode doesn't need signal-cli on the host; keep a harmless default.
        signal_cli_path = args.signal_cli_path.strip() or "signal-cli"

    agent_script = repo_root / "signal-agent.py"
    rules_yaml_repo = repo_root / "rules.yaml"

    if not agent_script.exists():
        eprint(f"Expected main script at: {agent_script}")
        return 3

    try:
        pairs = iter_template_files(repo_root, args.mode, config_dir)
    except Exception as e:
        eprint(f"Template discovery error: {e}")
        return 2

    # Determine if any selected template requires @PHONE@
    needs_phone = False
    for tpl_path, _out_path in pairs:
        try:
            if template_needs_phone(read_text(tpl_path)):
                needs_phone = True
                break
        except Exception:
            # If we can't read, let later step fail with a better error.
            pass

    phone = args.phone.strip()
    if needs_phone and not phone:
        if args.non_interactive:
            eprint("ERROR: templates require @PHONE@ but --phone was not provided (non-interactive mode).")
            return 2
        phone = prompt_phone()

    if phone and not E164_RE.match(phone):
        eprint("ERROR: --phone must be E.164 like +15551234567 (7–15 digits).")
        return 2

    vars: Dict[str, str] = {
        "PHONE": phone,
        "REPO": str(repo_root),
        "PYTHON": python_path,
        "SIGNAL_CLI": signal_cli_path,
        "AGENT_SCRIPT": str(agent_script),
        "RULES_YAML": str(rules_yaml_repo),
        "USER": user,
    }

    generated: List[Path] = []

    for tpl_path, out_path in pairs:
        tpl = read_text(tpl_path)
        rendered = render_template(tpl, vars)

        # In non-interactive mode, avoid overwriting existing owned outputs unless --force.
        if args.non_interactive and out_path.exists() and not args.force:
            eprint(f"ERROR: would overwrite existing file: {out_path} (use --force or run interactively)")
            return 2

        write_text(out_path, rendered)
        generated.append(out_path)

    print("\nGenerated:")
    for p in generated:
        print(f"  {p}")

    print("\nGenerated manifest:")
    write_manifest(repo_root, args.mode, config_dir, generated)

    if args.mode in ("systemd", "both"):
        maybe_enable_linger(user, non_interactive=args.non_interactive)

    print("\nNext steps:")

    if args.mode in ("container", "both"):
        print("  # Container-first") 
        print(f"  - Your Docker config is under: {config_dir}")
        print("  - Ensure you have at least:")
        print("      config/rules.yaml")
        print("      config/rules.d/*.yaml  (optional, but recommended)")
        print("  - Then follow docs/DOCKER.md to link + start the container.")

    if args.mode in ("systemd", "both"):
        print("\n  # Non-container (systemd --user)")
        print("  make install     # runs configure + installs user units + daemon-reload")
        print("  make start       # enable + start services")
        print("  make status      # show service status")
        print("  make logs        # tail logs")
        print("  make uninstall   # remove installed services (does not touch rules.d/)")
        print("  make clean       # remove repo-generated files (keeps your custom rules)\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
