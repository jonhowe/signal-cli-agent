# Signal CLI Agent

Signal CLI Agent is a rule-driven automation engine that connects to `signal-cli` via DBus and executes local actions in response to trusted Signal messages.

It allows you to securely trigger commands on a host using Signal as the control channel.

---

# Overview

Signal CLI Agent:

- Listens for Signal messages
- Matches messages against YAML-defined rules
- Executes local commands safely
- Sends results back via Signal

Rules are defined in YAML files and can be extended without modifying the core code.

For the full rule schema and configuration reference, see:

👉 **[docs/RULES.md](docs/RULES.md)**

---

# Why Use This?

Instead of exposing:

- SSH access
- A web server
- A public API

You can use Signal as a secure, authenticated automation interface.

Common use cases:

- System diagnostics
- Log inspection
- Service health checks
- Lightweight automation
- API status queries

---

# Architecture

```mermaid
flowchart TD
  A[Your Phone] --> B[Signal Message]
  B --> C[signal-cli (DBus daemon)]
  C --> D[signal-agent.py]
  D --> E[Load rules.yaml]
  D --> F[Load rules.d/*.yaml]
  D --> G[Match sender + trigger]
  D --> H[Execute command]
  D --> I[Send response via Signal]
```

---

# Installation

## Prerequisites

### Install `signal-cli`

Official repository:

https://github.com/AsamK/signal-cli

Register and verify your Signal number.

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

## Quick Start (Recommended)

From the repository root:

```sh
make quickstart
```

This will:

1. Run `configure.py`
2. Render configuration templates
3. Install systemd user services
4. Start both services

Verify:

```sh
make status
make logs
```

---

## Boot-Time Startup (Optional)

User services normally start when you log in.

To allow them to start at boot:

```sh
sudo loginctl enable-linger <your-username>
```

---

# Writing Rules

Production rules live in:

```
rules.d/
```

You may add your own `.yaml` files there.

Shipped example rules are rendered from:

```
templates/rules/
```

For detailed rule structure and all supported parameters:

👉 **See `docs/RULES.md`**

---

# Operating Modes

## Service Mode

Recommended for normal use:

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

---

## Manual Mode

Start DBus daemon:

```sh
signal-cli -a +15551234567 daemon --dbus
```

Run agent:

```sh
python3 signal-agent.py ./rules.yaml
```

---

## Test Mode

Simulate a message locally (no Signal send):

```sh
python3 signal-agent.py ./rules.yaml \
  --test \
  --sender "+15551234567" \
  --message "disk?"
```

---

# Maintenance

## Uninstall Services

```sh
make uninstall
```

Removes installed user units only.

## Remove Generated Files

```sh
make clean
```

Removes generated configuration and shipped example rule.

Custom rules in `rules.d/` remain untouched.

---

# FAQ

### Do I need to restart after editing rules?

No. Rules are automatically reloaded.

### Can I allow multiple senders?

Yes. See `docs/RULES.md`.

### What if multiple rules match?

The first matching rule executes.

### Can I call APIs?

Yes. Use helper scripts referenced in `command:`.

Full examples are documented in `docs/RULES.md`.

---

# Roadmap

Planned improvements:

- Plugin architecture for structured rule types
- Docker deployment option
- Role-based access control
- Metrics and enhanced logging

---

# Security Notes

This system executes local commands triggered by Signal messages.

You should:

- Restrict allowed senders
- Validate inputs carefully
- Use rate limiting
- Avoid committing secrets
- Run as a non-root user

For detailed configuration and safety guidance, see:

👉 **`docs/RULES.md`**