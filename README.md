# Signal CLI Agent

A YAML-driven automation framework that connects to
[`signal-cli`](https://github.com/AsamK/signal-cli) via DBus and maps
trusted Signal messages to controlled actions on your host system.

It is designed to be **extensible**:
- Add rules without modifying the core agent
- Store rules in separate files under `rules.d/`
- Use external helper scripts for API integrations
- Support multiple reply formats
- Run manually or as persistent systemd user services

---

## 🚀 Quick Start (Service Mode – Recommended)

From the repo root:

```
make quickstart
```

This will:

- Prompt for your Signal phone number
- Generate configuration from templates
- Install systemd user services
- Enable and start both services

Then check:

```
make status
make logs
```

---

## Table of Contents

- [Overview](#overview)
- [Repo Structure](#repo-structure)
- [Prerequisites](#prerequisites)
- [Service Mode (Recommended)](#service-mode-recommended)
- [Manual Mode](#manual-mode)
- [Extending with Rules](#extending-with-rules)
- [Reply Modes](#reply-modes)
- [Rules Directory (rules.d)](#rules-directory-rulesd)
- [Uninstalling](#uninstalling)
- [Security Considerations](#security-considerations)

---

## Overview

Signal CLI Agent listens for Signal messages via `signal-cli` DBus and evaluates
incoming messages against YAML-defined rules.

Each rule can:

- Match a sender
- Match a message (exact, contains, startswith, or regex)
- Execute a command or command template
- Rate-limit execution
- Format replies (full, output-only, or bare)
- Split long output into numbered chunks

Rules are modular and stored under `rules.d/`, allowing you to extend functionality without modifying the core script.

---

## Repo Structure

```
.
├── Makefile
├── configure.py
├── signal-agent.py
├── rules.yaml.in
├── rules.yaml                 # generated
├── rules.d/
│   ├── example-rules.yaml.in
│   └── more-rules.yaml.in
└── systemd/
    ├── signal-agent.service.in
    └── signal-cli-dbus.service.in
```

---

## Prerequisites

### Install `signal-cli`

Official repository:  
https://github.com/AsamK/signal-cli

Follow installation instructions in the official README.

You must register and verify your Signal number before using the agent.

---

### Python Requirements

- Python 3.10+
- `pydbus`
- `PyYAML`
- `python3-gi`

Install:

```
pip install pydbus pyyaml
sudo apt install python3-gi
```

---

## Service Mode (Recommended)

### Install and Start

```
make quickstart
```

This:

- Runs `configure.py`
- Renders templates
- Installs systemd user services
- Enables and starts services

### Manage Services

```
make status
make logs
make restart
make stop
```

---

### Boot-time Startup (Linger)

User services normally start only when you log in.

To start them automatically at boot:

```
sudo loginctl enable-linger <your-username>
```

Verify:

```
loginctl show-user <your-username> | grep Linger
```

---

## Manual Mode

For development or testing:

### Start DBus daemon

```
signal-cli -a +15551234567 daemon --dbus
```

### Run the agent

```
python3 signal-agent.py ./rules.yaml
```

Optional: use `screen` or `tmux` to keep processes running.

---

## Extending with Rules

All rules are stored in:

```
rules.d/
```

You can create additional YAML files there without touching the main configuration.

Each file may contain:

```
rules:
  - name: my_rule
    sender: "+15551234567"
    trigger: "hello"
    match: exact
    command: ["echo", "Hello world"]
```

The agent automatically loads all `.yaml` files in `rules.d/`.

---

## Example Rules

### Basic Command Rule

```
rules:
  - name: disk_usage
    sender: "+15551234567"
    trigger: "disk?"
    match: exact
    command: ["df", "-h"]
```

---

### Regex Rule with Argument Validation

```
rules:
  - name: tail_logs
    sender: "+15551234567"
    match: regex
    trigger: "^tail (\\d+)$"
    command_template: ["journalctl", "-n", "{n}", "--no-pager"]
    args:
      n:
        type: int
        min: 1
        max: 200
```

---

### External API / Helper Script Rule

You can call external scripts for API integrations:

```
rules:
  - name: custom_status
    sender: "+15551234567"
    trigger: "status?"
    match: exact
    command:
      - "/usr/bin/python3"
      - "/path/to/helper_script.py"
    reply_mode: output
```

This allows unlimited extensibility without modifying `signal-agent.py`.

---

## Reply Modes

Each rule can specify:

| reply_mode | Behavior |
|------------|----------|
| `full`     | `[rule] exit=0` + command + output (default) |
| `output`   | `[rule]` + output only |
| `bare`     | output only |

Example:

```
reply_mode: output
```

---

## Chunked Output

For large outputs:

```
split_reply: true
chunk_size: 1400
numbered_chunks: true
```

Produces:

```
[rule message 1/2]
...
[rule message 2/2]
...
```

Numbering only appears when output splits into multiple messages.

---

## Rules Directory (`rules.d`)

The root `rules.yaml` file contains:

- `globals`
- `rules_dir`

All rules are automatically loaded from:

```
rules_dir: "<repo>/rules.d"
```

You can:

- Add new YAML files
- Remove old ones
- Modify rules without restarting (auto-reload supported)

---

## Uninstalling

Remove services:

```
make uninstall
```

Remove generated files:

```
make clean
```

Disable boot auto-start:

```
sudo loginctl disable-linger <your-username>
```

---

## Security Considerations

This agent executes host commands triggered by Signal messages.

You must:

- Restrict `sender` to trusted numbers
- Use strict matching
- Validate regex arguments
- Enforce rate limits
- Avoid shell=True
- Store API tokens securely (not in repo)

Do not expose this system to untrusted contacts.

---

## Design Philosophy

This project is intentionally:

- Minimal
- Extensible
- Integration-friendly
- API-agnostic
- Safe by default

You are expected to build custom integrations (e.g. Home Assistant, monitoring systems, etc.) as separate helper scripts called by rules.

The core agent remains stable and generic.