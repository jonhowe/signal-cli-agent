# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Overview

Signal CLI Agent is a rule-driven automation engine that connects to `signal-cli` via DBus and executes local actions in response to trusted Signal messages. It enables secure remote command execution using Signal as the control channel without exposing SSH, web servers, or public APIs.

## Common Development Commands

### Local Development (systemd mode)
```bash
# Development environment setup
pip install -e .[dev]   # Install in development mode with all dependencies

# Quick setup: configure + install + start
make quickstart

# Individual steps
make configure          # Generate rules.yaml and systemd units from templates
make install           # Install systemd user units + daemon-reload
make start             # Enable and start services
make stop              # Stop services
make restart           # Restart services
make status            # Show service status
make logs              # Tail logs with filtering
make uninstall         # Remove installed units (preserves user rules)
make clean             # Remove generated files (preserves user rules in rules.d/)

# Testing
make pytest            # Run unit tests
make test              # Run agent in --test mode (no DBus/Signal sends)
make test TEST_SENDER=+15551234567 TEST_MESSAGE="disk?"  # Custom test
```

### Container Development (recommended)
```bash
# Published image workflow
make docker-pull                              # Pull GHCR image
make docker-configure PHONE=+1XXXXXXXXXX     # Generate config from templates
make docker-link                             # Link Signal device (QR code)
make docker-sync                             # Sync contacts/groups
make docker-up                               # Start container
make docker-logs                             # View raw logs
make docker-logs-filter                      # View filtered logs (actionable messages only)
make docker-down                             # Stop container

# Local development builds
make docker-up-build                         # Force rebuild then start
make docker-configure-build PHONE=+1XXXXXXXXXX  # Configure with rebuild
make docker-link-build                       # Link device with rebuild
make docker-sync-build                       # Sync with rebuild
```

## Architecture

### Core Components

**signal-agent.py** - Main application entry point
- DBus listener for `org.asamk.Signal`
- YAML-based rule engine with matching logic (exact/contains/startswith/regex)
- Plugin system for extensible action types
- Rate limiting and sender authorization
- Message chunking and reply formatting
- Structured logging (text/JSON)

**Rule System** (`rules.yaml` + `rules.d/*.yaml`)
- Root configuration: `rules.yaml` (generated from `rules.yaml.in`)
- Additional rules: `rules.d/` directory (supports both shipped examples and user rules)
- Rule matching supports sender filters, message patterns, and action dispatch
- Command prefix gate for preventing accidental triggers
- Per-rule dry run and rate limiting support

**Plugin Architecture** (`plugins/`)
- Base class: `plugins/base.py`
- Registry: `plugins/registry.py`
- Built-in plugins:
  - `home_assistant.py` - Home Assistant integration
  - `home_assistant_service.py` - Home Assistant service calls
  - `http_get.py` - HTTP GET requests
  - `nlp_router.py` - Natural language routing via LiteLLM
- Services framework: `plugins/services/` (for background services like REST API)

### Configuration System

**configure.py** - Template rendering system
- Supports two modes: `container` (recommended) and `systemd`
- Renders `.in` template files with environment-specific values
- Safe overwriting: only touches files "owned" by shipped templates
- Preserves user-created rules in `rules.d/`

### Operational Modes

**Container Mode (Recommended)**
- Application code lives in container image (`/app/`)
- Configuration mounted to `/config/`
- Persistent state mounted to `/data/`
- Uses Docker Compose with `network_mode: host`

**Systemd Mode (Non-container)**
- Direct host execution with systemd user services
- Generated units: `signal-agent.service`, `signal-cli-dbus.service`
- Local rule files and configuration

## Development Patterns

### Plugin Development
- Extend `plugins.base.BasePlugin`
- Register in `plugins/registry.py`
- Follow established patterns for configuration validation and execution
- Support dry-run mode for testing

### Rule Development
- Test rules using `make test` with custom sender/message
- Use rule-level `dry_run: true` for safe development
- Leverage command prefix gate to prevent accidental execution
- Follow security best practices for sender filtering and input validation

### Testing
- Unit tests in `tests/` directory using pytest
- Test server: `tests/http_test_server.py` for HTTP plugin testing
- Integration testing via `--test` mode (no DBus/Signal interaction)

## Key Files and Locations

**Application Code** (baked into container)
- `signal-agent.py` - Main application
- `plugins/` - Plugin system
- `scripts/` - Utility scripts
- `templates/` - Template files for configuration generation
- `docker/` - Container build files
- `pyproject.toml` - Python project configuration and dependencies
- `requirements*.txt` - Legacy/simple dependency files

**Runtime Configuration** (mounted when running)
- `./config/rules.yaml` - Main rule configuration
- `./config/rules.d/` - Additional rule files
- `./config/tokens/` - API tokens and secrets
- `./data/signal-cli/` - Signal linked device state (preserve to avoid re-linking)

**Development Files**
- `tests/` - Test suite
- `docs/` - Documentation (including DEPENDENCIES.md)
- `AGENTS.md` - This file (development guide)

## Security Considerations

- All rules should restrict `sender` to authorized phone numbers
- Use exact matching when possible vs regex
- Validate inputs carefully, especially regex capture groups
- Rate limiting is built-in per sender+rule
- Command prefix gate prevents accidental triggers
- Secrets should be stored in mounted config, never in container image
- Run as non-root user where feasible

## Documentation Structure

Core documentation is in `docs/`:
- `RULES.md` - Authoritative rule configuration reference
- `PLUGINS.md` - Plugin system documentation
- `EXAMPLE_RULES.md` - Walkthrough and samples
- `DOCKER.md` - Container deployment details
- `DEVELOPMENT.md` - Contributor guide
- `NON_CONTAINER.md` - Systemd deployment
- `REST_API.md` - Optional REST API service
- `NLP.md` - Natural language processing integration