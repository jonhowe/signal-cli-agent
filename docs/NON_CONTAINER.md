# Non-container operation (systemd / bare-metal)

This document describes running **signal-cli-agent directly on a Linux host** (no Docker).

⚠️ This mode is optional / legacy.  
The recommended deployment is Docker (see the repository root `README.md` and **[DOCKER.md](DOCKER.md)**).

---

## When should you use this mode?

Only use non-container mode if:

- You intentionally do not want Docker
- You need tight integration with host-level systemd services
- You are operating in an environment where containers are not allowed

Otherwise, use the containerized setup.

---

# Installation

## Prerequisites

You must install and configure everything directly on the host.

### 1) Install `signal-cli`

Official repository:  
https://github.com/AsamK/signal-cli  

Official installation documentation:  
https://github.com/AsamK/signal-cli#installation

After installing, you must:

- Register your phone number
- Verify it via SMS or voice
- Ensure `signal-cli` works from the CLI

Example:

```bash
signal-cli -a +15551234567 register
signal-cli -a +15551234567 verify 123456
```

(Optional helper script from this repo):

```bash
./scripts/update-signal-cli.sh
```

---

### 2) Install Python dependencies

Minimum requirements:

- Python 3.10+
- `pydbus`
- `PyYAML`
- `python3-gi` (GLib bindings)

Example (Debian/Ubuntu):

```bash
sudo apt-get update
sudo apt-get install -y python3-gi gir1.2-glib-2.0
pip3 install pydbus pyyaml
```

---

# Quick Start (systemd — recommended for non-container mode)

From the repository root:

```bash
make quickstart
```

This will:

1) Run `configure.py` in **systemd mode**
2) Render configuration templates into repo files
3) Install systemd **user** services
4) Start:
   - signal-cli DBus daemon
   - signal-cli-agent

---

## Manual setup (step-by-step)

If you prefer manual setup:

```bash
python3 ./configure.py --mode systemd --phone +1XXXXXXXXXX
make install
make start
```

Verify status:

```bash
make status
make logs
```

---

# How configuration works (non-container mode)

In non-container mode, files are generated inside the repository.

Generated files:

- `./rules.yaml` (rendered from `rules.yaml.in`)
- `./rules.d/*.yaml` (rendered examples + your custom rules)
- `./systemd/*.service` (rendered from `templates/systemd/*.service.in`)

The agent loads:

- `./rules.yaml`
- `./rules.d/*.yaml` (by default)

Unlike Docker mode, this setup relies on:

- Host DBus session
- Host-installed `signal-cli`
- systemd user services

---

# Writing Rules (non-container)

You edit:

- `rules.yaml` (main configuration)
- `rules.d/*.yaml` (rule definitions)

Authoritative documentation:

- **[docs/RULES.md](RULES.md)**
- **[docs/PLUGINS.md](PLUGINS.md)**
- **[docs/NLP.md](NLP.md)** (optional NLP routing)
- **[docs/REST_API.md](REST_API.md)** (optional outbound REST API)

⚠️ In non-container mode, `configure.py` renders from template files:

- `rules.yaml.in`
- `templates/rules/*.yaml.in`

These are processed into actual runtime files. Docker mode does not use this template rendering pattern in the same way.

---

# Operating Modes (non-container only)

## Service Mode (recommended for non-container)

Runs as systemd **user** services:

- signal-cli DBus daemon
- signal-cli-agent

This is the preferred non-container approach.

---

## Manual Mode (no systemd)

Start DBus daemon:

```bash
signal-cli -a +15551234567 daemon --dbus
```

Run agent:

```bash
python3 signal-agent.py ./rules.yaml
```

---

## Test Mode (no DBus, no Signal send)

Simulate a message locally:

```bash
python3 signal-agent.py ./rules.yaml \
  --test \
  --sender "+15551234567" \
  --message "! disk?"
```

Note:  
If `globals.command_prefix` is enabled, your test message must include the prefix.

---

# Boot-Time Startup (optional)

Systemd **user** services normally start only when you log in.

To allow services to:

- Start at boot
- Continue running after logout

Enable lingering:

```bash
sudo loginctl enable-linger <your-username>
```

Verify:

```bash
loginctl show-user <your-username> | grep Linger
```

---

# Maintenance

## Uninstall services

```bash
make uninstall
```

Removes installed systemd **user** units only.

---

## Remove generated files

```bash
make clean
```

Removes:

- Rendered configuration files
- Rendered systemd unit files
- Shipped example templates (rendered)

Your custom rules inside `rules.d/` remain untouched.

---

# Remove everything (full cleanup)

To completely remove:

1. Uninstall services:

```bash
make uninstall
```

2. Remove generated files:

```bash
make clean
```

3. Optionally remove `signal-cli` from the host if no longer needed.

---

# Summary

Non-container mode requires:

- Host-installed `signal-cli`
- Host DBus session
- Python dependencies
- systemd user services (recommended)

If you do not explicitly need this architecture, use the Docker deployment instead.