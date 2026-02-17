# Signal CLI Agent — Plugin Architecture Reference

This document provides a deep technical reference for the **plugin architecture** in Signal CLI Agent.

It covers:

- Plugin design goals
- Plugin lifecycle
- Plugin configuration schema
- Built-in plugins (Phase 0 / Phase 1)
- Validation rules
- Execution flow
- Safety constraints
- Safe vs unsafe plugin patterns

This is a technical reference document, not a quick-start guide.

---

# Schema Summary (Quick Reference)

## Rule with Plugin

```yaml
- name: str
  sender: str | [str, ...]
  trigger: str
  match: exact|contains|startswith|regex

  type: plugin_name

  <plugin_name>:
    # plugin-specific configuration
    ...

  timeout_sec: int
  reply_mode: full|output|bare
  split_reply: bool
  chunk_size: int
  numbered_chunks: bool
  cooldown_sec: int
  max_runs_per_hour: int
  reply_to: sender|admin|number
  reply_number: str
  dry_run: bool
```

---

## Plugin Result Contract

Every plugin must return:

```python
PluginResult(
    status="ok" | "error",
    exit_code=int,
    body=str,
    meta=dict
)
```

The agent then:

1. Applies redaction
2. Applies truncation
3. Applies reply formatting
4. Applies chunking
5. Sends response

---

# Table of Contents

- [1. Design Goals](#1-design-goals)
- [2. Plugin Lifecycle](#2-plugin-lifecycle)
- [3. Plugin Interface Contract](#3-plugin-interface-contract)
- [4. Built-in Plugins](#4-built-in-plugins)
  - [4.1 Home Assistant Plugin](#41-home-assistant-plugin)
  - [4.2 HTTP GET Plugin](#42-http-get-plugin)
- [5. Configuration Model](#5-configuration-model)
- [6. Validation Model](#6-validation-model)
- [7. Execution Model](#7-execution-model)
- [8. Output Handling](#8-output-handling)
- [9. Safe vs Unsafe Plugin Patterns](#9-safe-vs-unsafe-plugin-patterns)
- [10. Security Model](#10-security-model)

---

# 1. Design Goals

The plugin system exists to:

- Replace shell-based helper scripts for common integrations
- Standardize HTTP/API access patterns
- Provide structured validation
- Reduce injection risk
- Keep integrations explicit and auditable
- Avoid persistent background connections unless required

Plugins are:

- Synchronous
- Stateless by default
- Deterministic
- Narrow in scope

---

# 2. Plugin Lifecycle

When a rule includes:

```yaml
type: home_assistant
```

The agent:

1. Loads configuration
2. Identifies `type`
3. Fetches plugin from registry
4. Calls `plugin.validate(rule, globals)`
5. If valid, calls `plugin.run(rule, globals, context)`
6. Receives `PluginResult`
7. Applies standard formatting + chunking
8. Sends reply

If plugin validation fails:
- The rule is skipped
- An error is logged

---

# 3. Plugin Interface Contract

All plugins must implement:

```python
class BasePlugin:
    name: str

    def validate(self, rule: dict, globals_cfg: dict) -> None:
        pass

    def run(self, rule: dict, globals_cfg: dict, context: dict) -> PluginResult:
        pass
```

### `validate()`

- Ensures required fields exist
- Ensures types are correct
- Enforces bounds
- Raises `ValueError` if invalid

### `run()`

- Performs the integration logic
- Returns `PluginResult`
- Must not crash the agent
- Must handle errors gracefully

---

# 4. Built-in Plugins

---

## 4.1 Home Assistant Plugin

**Type:** `home_assistant`

### Supported Actions (Phase 0)

- `get_state`
- `template` (read-only rendering)

---

### Global Configuration

```yaml
globals:
  home_assistant:
    url: "http://homeassistant.local:8123"
    token_file: "/path/to/token"
    timeout_sec: 4
```

---

### Rule Example — get_state

```yaml
- name: ha_temp
  sender: "+15551234567"
  trigger: "temp?"
  match: exact

  type: home_assistant
  home_assistant:
    action: get_state
    entity_id: sensor.example_temperature
    label: "Temperature"

  reply_mode: output
```

Behavior:

- Performs HTTP GET:
  ```
  /api/states/sensor.example_temperature
  ```
- Returns sensor state
- Optional `label` prefixes output

---

### Rule Example — template

```yaml
- name: ha_template_example
  sender: "+15551234567"
  trigger: "who is home?"
  match: exact

  type: home_assistant
  home_assistant:
    action: template
    template: "{{ states('person.someone') }}"
    label: "Presence"
```

Behavior:

- Performs HTTP POST to `/api/template`
- Returns rendered string

---

## 4.2 HTTP GET Plugin

**Type:** `http_get`

Designed for generic read-only endpoints.

### Example

```yaml
- name: service_status
  sender: "+15551234567"
  trigger: "status?"
  match: exact

  type: http_get
  http_get:
    url: "https://example.com/status"
    timeout_sec: 3
    label: "Service"
```

Optional fields:

```yaml
http_get:
  headers:
    Authorization: "Bearer token"
  params:
    foo: bar
  json_path: "data.value"
```

If `json_path` provided:
- Parses JSON
- Extracts nested field using dot notation (`a.b.c`)
- Supports list indices when a segment is numeric (`items.0.name`)

---

# 5. Configuration Model

Plugin configuration is layered:

1. `globals.<plugin_name>` (default config)
2. `rule.<plugin_name>` (rule overrides)

Merge behavior:

```
rule config overrides globals config
```

Example:

```yaml
globals:
  home_assistant:
    url: "http://ha.local"
    token_file: "/secure/token"

rule:
  home_assistant:
    action: get_state
    entity_id: sensor.foo
```

---

# 6. Validation Model

Plugins must:

- Validate required fields
- Enforce safe bounds
- Reject unknown actions
- Enforce timeouts within safe range

Validation occurs before execution.

Invalid plugin config:

- Logs error
- Skips rule
- Does not crash agent

---

# 7. Execution Model

Execution flow:

1. Rate limit check
2. Plugin validate
3. Plugin run
4. Receive PluginResult
5. Redaction applied
6. Truncation applied
7. Reply formatting applied
8. Chunking applied
9. Response sent

Plugins never bypass global formatting behavior.

---

# 8. Output Handling

Plugins return a clean `body` string.

Agent then applies:

- `redact_regex`
- `max_reply_chars`
- `reply_mode`
- `split_reply`
- `numbered_chunks`

Plugins should not implement their own chunking.

---

# 9. Safe vs Unsafe Plugin Patterns

---

## SAFE Pattern — Static Home Assistant Read

```yaml
type: home_assistant
home_assistant:
  action: get_state
  entity_id: sensor.foo
```

Safe because:
- Read-only
- Fixed entity
- No user input interpolation

---

## SAFE Pattern — Validated Template

```yaml
type: home_assistant
home_assistant:
  action: template
  template: "{{ states('sensor.foo') }}"
```

Safe because:
- Static template
- No user-provided content

---

## UNSAFE Pattern — Dynamic Template from Message

```yaml
trigger: "^run (.*)$"
match: regex

type: home_assistant
home_assistant:
  action: template
  template: "{{ states('{input}') }}"
```

Why unsafe:
- Injects user input into template
- Could expose unintended data

Avoid dynamic template interpolation unless strongly validated.

---

## UNSAFE Pattern — Arbitrary HTTP GET

```yaml
type: http_get
http_get:
  url: "{user_input}"
```

Never allow user-controlled URLs.

---

# 10. Security Model

Plugins improve safety by:

- Avoiding shell execution
- Enforcing schema validation
- Centralizing HTTP handling
- Providing consistent timeout enforcement

User responsibilities:

- Restrict senders
- Avoid dynamic user-controlled URLs
- Avoid dynamic template construction
- Store tokens securely (`chmod 600`)
- Review plugin rules carefully before enabling

---

This document defines the plugin system contract.

For rule structure and core agent behavior, see:

👉 **docs/RULES.md**