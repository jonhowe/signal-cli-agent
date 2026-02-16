# Signal CLI Agent

Signal CLI Agent is a rule-driven automation engine that connects to `signal-cli` via DBus and executes local actions in response to trusted Signal messages.

It allows you to securely trigger commands on a host using Signal as the control channel.

---

# Table of Contents

- [1. Overview](#1-overview)
- [2. Why This Exists](#2-why-this-exists)
- [3. Architecture](#3-architecture)
- [4. Installation](#4-installation)
- [5. Project Structure](#5-project-structure)
- [6. Writing Rules](#6-writing-rules)
- [7. Output & Formatting](#7-output--formatting)
- [8. Operating Modes](#8-operating-modes)
- [9. Maintenance](#9-maintenance)
- [10. Troubleshooting](#10-troubleshooting)
- [11. FAQ](#11-faq)
- [12. Roadmap](#12-roadmap)
- [13. Appendix: Rule Parameter Reference](#13-appendix-rule-parameter-reference)
- [14. Security Considerations](#14-security-considerations)

---

# 1. Overview

Signal CLI Agent:

- Listens for Signal messages
- Matches messages against YAML-defined rules
- Executes local commands safely
- Sends results back via Signal

It is intentionally minimal, explicit, and extensible.

---

# 2. Why This Exists

Instead of exposing:

- SSH access
- A web interface
- A public REST API

You can use Signal as a secure, authenticated command interface.

Typical use cases:

- System diagnostics
- Log inspection
- Service health checks
- API status queries
- Lightweight automation

---

# 3. Architecture

## 3.1 Mental Model

```mermaid
flowchart TD
  A[Your Phone] --> B[Signal Message]
  B --> C[signal-cli (DBus daemon)]
  C --> D[signal-agent.py]
  D --> E[Load rules.yaml]
  D --> F[Load rules.d/*.yaml]
  D --> G[Match sender + trigger]
  D --> H[Validate arguments]
  D --> I[Execute command]
  D --> J[Format + redact output]
  J --> K[Send response via Signal]
```

## 3.2 Key Properties

- No shell string execution (`shell=True` is not used)
- Explicit argv-style commands
- Per-rule rate limiting
- Safe regex argument validation
- Automatic rule reloading

---

# 4. Installation

## 4.1 Prerequisites

### Install `signal-cli`

Official repository:

https://github.com/AsamK/signal-cli

Register and verify your Signal number.

### Install Python dependencies

```sh
pip install pydbus pyyaml
sudo apt install python3-gi
```

---

## 4.2 Quick Setup

```sh
make quickstart
```

This:

1. Runs `configure.py`
2. Renders templates
3. Installs systemd user services
4. Starts the services

Verify:

```sh
make status
make logs
```

---

## 4.3 Boot-Time Startup (Optional)

```sh
sudo loginctl enable-linger <your-username>
```

---

# 5. Project Structure

```
templates/        → shipped templates (source-of-truth)
rules.d/          → production rules (user-managed)
systemd/          → rendered service files
rules.yaml        → generated root config
```

- `templates/` contains files the project ships.
- `rules.d/` is your working rule directory.
- `make clean` never deletes custom rules.

---

# 6. Writing Rules

## 6.1 Minimal Example

```yaml
- name: disk_usage
  sender: "+15551234567"
  trigger: "disk?"
  match: exact
  command: ["df", "-h"]
  reply_mode: output
```

## 6.2 Regex + Argument Validation

```yaml
- name: tail_logs
  sender: "+15551234567"
  trigger: "^tail (\\d+)$"
  match: regex
  command_template: ["journalctl", "-n", "{n}"]
  args:
    n:
      type: int
      min: 1
      max: 200
```

---

# 7. Output & Formatting

## 7.1 Reply Modes

| Mode     | Behavior |
|----------|----------|
| full     | `[rule] exit=0` + command + output |
| output   | `[rule]` + output |
| bare     | output only |

## 7.2 Chunking Large Output

```yaml
split_reply: true
chunk_size: 1400
numbered_chunks: true
```

---

# 8. Operating Modes

## 8.1 Service Mode

```sh
make quickstart
```

Manage:

```sh
make status
make logs
make restart
make stop
```

## 8.2 Manual Mode

```sh
signal-cli -a +15551234567 daemon --dbus
python3 signal-agent.py ./rules.yaml
```

## 8.3 Dry Run

```sh
python3 signal-agent.py ./rules.yaml --dry-run
```

## 8.4 Test Mode

```sh
python3 signal-agent.py ./rules.yaml --test \
  --sender "+15551234567" \
  --message "disk?"
```

---

# 9. Maintenance

## Remove Installed Services

```sh
make uninstall
```

## Remove Generated Files

```sh
make clean
```

Custom rules remain untouched.

---

# 10. Troubleshooting

### Service Not Starting

```sh
make status
journalctl --user -u signal-agent.service
```

### No Replies

- Verify sender
- Ensure `signal-cli` is running
- Check `dry_run`
- Review logs

### Regex Not Matching

Escape properly in YAML:

```
\\d+
```

---

# 11. FAQ

**Do I need to restart after editing rules?**  
No. Rules auto-reload.

**What if multiple rules match?**  
First match wins.

**Can I allow multiple senders?**  
Yes — use a list.

**Are custom rules deleted during uninstall?**  
No.

---

# 12. Roadmap

### Plugin Architecture
Introduce structured rule types like:

- `type: http_get`
- `type: home_assistant`
- `type: systemd_status`

### Docker Deployment
Provide containerized deployment.

### Role-Based Access Control
Define `admin`, `readonly`, etc.

### Metrics & Structured Logging
Optional Prometheus endpoint and JSON logging improvements.

---

# 13. Appendix: Rule Parameter Reference

This section documents all supported rule parameters.

---

## Core Fields

### `name`
Unique rule identifier.

### `sender`
Allowed sender(s).

String:

```yaml
sender: "+15551234567"
```

List:

```yaml
sender:
  - "+15551234567"
  - "+15557654321"
```

---

### `trigger`
Message text or regex pattern.

---

### `match`
Matching mode:

- exact
- contains
- startswith
- regex

---

### `command`
Direct command (argv list).

```yaml
command: ["ls", "-lah", "/home/user"]
```

---

### `command_template`
Parameterized command with placeholders.

```yaml
command_template: ["journalctl", "-n", "{n}"]
```

---

### `args`
Validation rules for regex capture groups.

```yaml
args:
  n:
    type: int
    min: 1
    max: 200
```

---

### `reply_mode`
- full
- output
- bare

---

### `reply_prefix` / `reply_suffix`
Custom wrapper text.

---

### `split_reply`
Boolean. Enable chunking.

---

### `chunk_size`
Maximum characters per chunk.

---

### `numbered_chunks`
Adds `[rule message i/n]` prefix if multiple chunks.

---

### `cooldown_sec`
Minimum time between executions.

---

### `max_runs_per_hour`
Rate limiting cap.

---

### `reply_to`
- sender
- admin

---

### `dry_run`
Suppress reply sending.

---

# 14. Security Considerations

This system executes local commands triggered by Signal messages.

Best practices:

- Restrict senders
- Use strict matching
- Validate inputs
- Limit execution frequency
- Store secrets securely
- Run as non-root

Treat it as controlled automation infrastructure.