# Signal CLI Agent

## Overview

Signal CLI Agent is a lightweight automation engine that connects to `signal-cli` via DBus and executes local actions in response to trusted Signal messages.

It allows you to:

- Send a message to your server via Signal
- Match that message against a rule
- Execute a command safely
- Receive the result back via Signal

Rules are defined in YAML files and can be extended without modifying the core code.

---

## Table of Contents

- [Overview](#overview)
- [Why Create This?](#why-create-this)
- [Architecture (Mental Model)](#architecture-mental-model)
- [Prerequisites](#prerequisites)
  - [Install `signal-cli`](#install-signal-cli)
  - [Install Python Dependencies](#install-python-dependencies)
- [Install Process](#install-process)
- [Sample Usage](#sample-usage)
- [Reply Modes](#reply-modes)
- [Dry Run & Test Mode](#dry-run--test-mode)
- [Troubleshooting](#troubleshooting)
- [FAQ](#faq)
- [Roadmap](#roadmap)
- [Security Considerations](#security-considerations)

---

## Why Create This?

Sometimes you want to:

- Check system status remotely
- Run safe diagnostic commands
- Query APIs (e.g., monitoring systems)
- Trigger small automation tasks

Instead of exposing SSH or building a web interface, you can use Signal as a secure, authenticated control channel.

This project gives you:

- Structured rule-based command execution
- Rate limiting
- Safe argument validation
- Configurable output formatting
- Separation between templates and production rules
- A clean systemd deployment model

It is intentionally generic so you can extend it however you like.

---

## Architecture (Mental Model)

```mermaid
flowchart TD
  A[Your Phone] --> B[Signal Message]
  B --> C[signal-cli<br/>(DBus daemon)]
  C --> D[signal-agent.py]
  D --> E[Load rules.yaml]
  D --> F[Load rules.d/*.yaml]
  D --> G[Match sender + trigger]
  D --> H[Validate arguments]
  D --> I[Execute command]
  D --> J[Apply redaction]
  D --> K[Format reply]
  K --> L[Response sent via Signal]
```

Key points:

- `signal-cli` handles Signal communication.
- `signal-agent.py` handles rule evaluation and execution.
- `rules.d/` contains your production rules.
- `templates/` contains shipped templates.
- No web server is required.

---

## Prerequisites

### Install `signal-cli`

Official repository:

https://github.com/AsamK/signal-cli

Follow their installation instructions and register your Signal number.

---

### Install Python Dependencies

```sh
pip install pydbus pyyaml
sudo apt install python3-gi
```

Requirements:

- Python 3.10+
- pydbus
- PyYAML
- python3-gi

---

## Install Process

From the project root:

```sh
make quickstart
```

This:

1. Runs `configure.py`
2. Prompts for your Signal number
3. Renders configuration templates
4. Installs systemd user services
5. Starts both services

Verify:

```sh
make status
make logs
```

To start automatically at boot:

```sh
sudo loginctl enable-linger <your-username>
```

---

## Sample Usage

### Disk Usage Example

```yaml
- name: disk_usage
  sender: "+15551234567"
  trigger: "disk?"
  match: exact
  command: ["df", "-h"]
  reply_mode: output
```

Send:

```
disk?
```

Agent replies with disk usage output.

---

## Reply Modes

- `full` → rule name + exit code + command + output  
- `output` → rule name + output  
- `bare` → output only  

---

## Dry Run & Test Mode

Dry run (no replies sent):

```sh
python3 signal-agent.py ./rules.yaml --dry-run
```

Test mode (simulate message locally):

```sh
python3 signal-agent.py ./rules.yaml --test \
  --sender "+15551234567" \
  --message "disk?"
```

---

## Troubleshooting

### Service not starting

```sh
make status
journalctl --user -u signal-agent.service
```

### No replies sent

- Check sender matches exactly.
- Confirm `signal-cli` daemon is running.
- Ensure `dry_run` is not enabled.
- Review logs:

```sh
make logs
```

### Regex not matching

- Escape correctly in YAML (`\\d+` not `\d+`).
- Test with:

```sh
make test TEST_MESSAGE="your message"
```

---

## FAQ

### Q: Do I need to restart the service after adding a rule?

No. The agent automatically reloads rule files when they change.

---

### Q: Can I have multiple rules with the same trigger?

Yes, but the agent stops at the **first matching rule**. Order matters.

---

### Q: Can I allow multiple senders per rule?

Yes.

```yaml
sender:
  - "+15551234567"
  - "+15557654321"
```

---

### Q: What happens if a command fails?

The exit code is included in `reply_mode: full`.  
If using `reply_mode: output`, only the output is returned.

---

### Q: Can I call APIs instead of local commands?

Yes. Create a helper script and reference it in `command:`:

```yaml
command: ["/usr/bin/python3", "/path/to/script.py"]
```

---

### Q: Are my custom rules deleted during uninstall?

No.

- `make uninstall` only removes installed systemd units.
- `make clean` removes generated files.
- Your custom rules in `rules.d/` remain untouched.

---

### Q: Is this secure?

It depends on how you configure it.

You should:

- Restrict allowed senders
- Use strict matching
- Validate regex inputs
- Limit execution frequency
- Avoid committing secrets
- Run as a non-root user

---

## Roadmap

Planned and potential future improvements:

### 🔌 Plugin Architecture

Introduce a formal plugin system that allows rule types such as:

- `type: http_get`
- `type: home_assistant`
- `type: systemd_status`

Instead of relying solely on shell commands or helper scripts, plugins would provide structured integrations with safer validation and reusable logic.

---

### 📦 Docker Deployment Option

Provide an official Dockerfile for easier deployment on servers and NAS systems.

---

### 🔐 Role-Based Access Control

Allow defining roles (e.g., `admin`, `readonly`) and require specific roles per rule.

---

### 📊 Enhanced Logging & Metrics

- Structured JSON logs by default
- Optional metrics export (Prometheus-style endpoint)

---

### 🧪 Rule Validation Tooling

Add a lint / validate command to check rules before deployment.

---

## Security Considerations

This system executes local commands triggered by Signal messages.

Treat it as controlled automation infrastructure.

- Restrict senders.
- Avoid permissive regex rules.
- Validate arguments.
- Store API tokens securely (`chmod 600`).
- Use rate limits.
- Run as a non-root account.