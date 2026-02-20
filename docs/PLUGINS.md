# Signal CLI Agent — Plugin Reference

This document is the authoritative reference for the **plugin architecture** in Signal CLI Agent.

It explains:

- What plugins are and why they exist
- How plugin rules are configured
- The **global HTTP safety controls** used by network-capable plugins
- Built-in plugins (Phase 0–1)
- Security and best practices

This is a technical reference, not a quick-start guide.

For the rule engine, matching, chunking, and general configuration reference, see:

👉 **[docs/RULES.md](RULES.md)**

---

# Schema Summary (Quick Reference)

## Global Plugin HTTP Defaults (`rules.yaml` → `globals.plugin_http`)

```yaml
globals:
  plugin_http:
    allowed_schemes: ["http", "https"]
    allowed_hosts: []          # empty => allow all (not recommended)
    follow_redirects: true     # redirects are validated against allowed_hosts
    max_response_bytes: 262144 # hard cap per HTTP response
```

These values are consumed by plugins that perform HTTP requests (Phase 1 includes `home_assistant` and `http_get`).

---

## Plugin Rule Shape

A plugin rule is a normal rule, but uses:

- `type: <plugin_name>`
- A plugin-specific config block

Example:

```yaml
- name: my_rule
  sender: ["+15551234567"]
  trigger: "example?"
  match: exact
  reply_mode: output

  type: http_get
  http_get:
    url: "http://127.0.0.1:8080/health"
```

---

# Table of Contents

- [1. Plugin Concepts](#1-plugin-concepts)
- [2. Plugin Lifecycle](#2-plugin-lifecycle)
- [3. Global HTTP Safety Controls](#3-global-http-safety-controls)
- [4. Built-in Plugins](#4-built-in-plugins)
  - [4.1 `home_assistant`](#41-home_assistant)
  - [4.2 `home_assistant_service`](#42-home_assistant_service)
  - [4.3 `http_get`](#43-http_get)
- [5. Security Guidance](#5-security-guidance)
- [6. Troubleshooting](#6-troubleshooting)

---

# 1. Plugin Concepts

Plugins exist to support **API-style rules** without requiring you to:

- write helper scripts for every integration
- parse JSON in shell commands
- leak tokens into command lines

A plugin is a small Python component that:

- validates its configuration (`validate()`)
- executes a specific integration (`run()`)
- returns a standardized result object (`PluginResult`)

Plugins are designed for **extensible, structured** integrations.

---

# 2. Plugin Lifecycle

When a message arrives:

1. The agent reloads configuration if needed.
2. It scans rules in order.
3. When a rule matches sender + trigger:
   - If the rule has `type: <plugin>`:
     1. The plugin is looked up in the registry.
     2. `plugin.validate(rule, globals_raw)` is called.
     3. `plugin.run(rule, globals_raw, context)` is called.
     4. The plugin returns a `PluginResult`.
   - The agent formats and sends the reply using the normal `reply_mode` logic.

**First match wins.**

Plugins are mutually exclusive with `command` / `command_template`:

- If `type:` is present, the agent runs the plugin.
- If `type:` is absent, the agent runs `command` / `command_template`.

---

# 3. Global HTTP Safety Controls

Network-capable plugins use `globals.plugin_http` as their default safety profile.

## 3.1 `allowed_hosts`

`allowed_hosts` is the primary control that prevents SSRF-style behavior.

```yaml
globals:
  plugin_http:
    allowed_hosts:
      - "homeassistant.home.lan"
      - "homeassistant.home.lan:8123"   # optional port pinning
```

Behavior:

- If `allowed_hosts` is empty: any host is allowed.
- If set: the plugin will reject any URL whose hostname (or hostname:port) is not listed.
- Redirect targets are also validated against `allowed_hosts`.

## 3.2 `max_response_bytes`

All plugin HTTP responses are capped to avoid unbounded memory usage.

```yaml
globals:
  plugin_http:
    max_response_bytes: 262144
```

If a response exceeds this size, the plugin returns an error.

## 3.3 `follow_redirects`

Redirect handling is configurable:

```yaml
globals:
  plugin_http:
    follow_redirects: true
```

- If `true`, redirects are allowed but the redirect URL is still validated.
- If `false`, any redirect response (30x) is treated as an error.

---

# 4. Built-in Plugins

Phase 0–1 includes two plugins:

- `home_assistant`
- `http_get`

---

## 4.1 `home_assistant`

The `home_assistant` plugin provides **read-only** access patterns for Home Assistant.

Supported actions:

- `get_state` — read an entity via `GET /api/states/<entity_id>`
- `template` — render a template via `POST /api/template` (read-only, returns text)

### 4.1.1 Global configuration

You can define Home Assistant defaults in `rules.yaml` under `globals.home_assistant`:

```yaml
globals:
  home_assistant:
    url: "http://homeassistant.home.lan:8123"
    token_file: "~/.config/signal-cli-agent/ha_token"
    timeout_sec: 4
    require_private_token_file: true
```

Notes:

- `token_file` is a plain text file containing a Home Assistant Long-Lived Access Token.
- Recommended permissions: `chmod 600 ~/.config/signal-cli-agent/ha_token`

### 4.1.2 Rule configuration (`action: get_state`)

Example rule:

```yaml
- name: ha_week
  sender: ["+15551234567"]
  trigger: "what week is it?"
  match: exact
  reply_mode: output

  type: home_assistant
  home_assistant:
    action: get_state
    entity_id: sensor.example_week_a_or_b
    label: "Week"
```

#### Fields

- `action`: `get_state`
- `entity_id`: required, must match `domain.object` (example: `sensor.kitchen_temp`)
- `value` / `json_path`: optional dot-path into the returned JSON (default: `state`)
  - Examples:
    - `state` (default)
    - `attributes.friendly_name`
    - `attributes.unit_of_measurement`
- `append_unit`: if `true` and `value/state` is used, appends `attributes.unit_of_measurement`
- `label`: optional prefix like `"Kitchen Temp"`
- `strip`: default `true` — trims whitespace
- `empty_as`: if the value is empty, substitute this string
- HTTP controls (optional overrides):
  - `timeout_sec`
  - `max_response_bytes`
  - `follow_redirects`
  - `allowed_hosts`

### 4.1.3 Rule configuration (`action: template`)

Example rule:

```yaml
- name: ha_template
  sender: ["+15551234567"]
  trigger: "lights summary?"
  match: exact
  reply_mode: output

  type: home_assistant
  home_assistant:
    action: template
    template: "{{ states('light.kitchen') }}"
    label: "Kitchen"
```

Notes:

- This uses `POST /api/template`, but is considered **read-only** because it only renders a template and returns text.
- This is still an HTTP request that must pass `allowed_hosts` rules.

### 4.1.4 Security notes

- Use `globals.plugin_http.allowed_hosts` to pin Home Assistant host(s).
- Keep token files out of the repo.
- Prefer read-only actions until you have strong validation and sender restrictions.

---

## 4.2 `home_assistant_service`

This plugin calls **Home Assistant services** via the REST API. It is designed for actions like **activating scenes**, turning lights on/off, and other controlled automations.

It performs a **POST** to:

`{url}/api/services/{domain}/{service}`

### Configuration

You can set defaults globally in `rules.yaml` under `globals.home_assistant_service`, and override per-rule under `home_assistant_service`.

**Globals example:**

```yaml
globals:
  home_assistant_service:
    url: "http://homeassistant.local:8123"
    token_file: "~/.config/signal-agent/ha_token"
    timeout_sec: 6
```

### Rule fields

- `domain` (required): Home Assistant service domain (e.g., `scene`, `light`, `switch`).
- `service` (required): The service name within the domain (e.g., `turn_on`, `turn_off`).
- `entity_id` (optional): A single entity id (`"scene.bedroom_on"`) **or** a list of entity ids.
- `service_data` (optional): A YAML mapping passed as the JSON body (merged with `entity_id` if provided).
- `label` (optional): Prefix used in the reply body.
- `timeout_sec` (optional, default 6): Request timeout (1–30 seconds).
- `max_body_chars` (optional, default 4000): Caps plugin output (prevents huge replies).

### Example: Activate a scene

This rule activates `scene.bedroom_on` using `scene.turn_on`.

```yaml
rules:
  - name: bedroom_on
    sender: ["+15551234567"]
    trigger: "bedroom on"
    match: exact

    type: home_assistant_service
    home_assistant_service:
      domain: scene
      service: turn_on
      entity_id: scene.bedroom_on
      label: "Bedroom"

    # Only show the plugin output (no exit code / command)
    reply_mode: output
    split_reply: false
```

**Notes:**
- The plugin reads the Home Assistant token from `token_file`. The file must be private (recommended `chmod 600`).
- Host allowlisting and timeouts are enforced via `globals.plugin_http` (see section 3.3).

## 4.3 `http_get`

The `http_get` plugin is a generic read-only GET helper.

It is useful for:

- health checks (`/health`, `/metrics`)
- reading JSON endpoints
- lightweight status queries

### 4.2.1 Example: plain text

```yaml
- name: local_health
  sender: ["+15551234567"]
  trigger: "health?"
  match: exact
  reply_mode: output

  type: http_get
  http_get:
    url: "http://127.0.0.1:8080/health"
    label: "health"
```

### 4.2.2 Example: JSON + `json_path`

If the endpoint returns JSON:

```json
{"data": {"value": 123}}
```

You can extract a specific field:

```yaml
- name: my_value
  sender: ["+15551234567"]
  trigger: "value?"
  match: exact
  reply_mode: output

  type: http_get
  http_get:
    url: "http://127.0.0.1:8080/status"
    json_path: "data.value"
    label: "value"
```

### 4.2.3 Fields

- `url`: required
- `headers`: optional mapping
- `params`: optional mapping (merged into the URL query string)
- `json_path`: optional dot-path into response JSON
- `label`, `strip`, `empty_as`
- HTTP controls (optional overrides):
  - `timeout_sec`
  - `max_response_bytes`
  - `follow_redirects`
  - `allowed_hosts`

---

# 5. Security Guidance

Plugins reduce the need for shell scripts, but they introduce their own risks.

Recommendations:

1. **Always restrict senders** (`sender: ["+15551234567"]`).
2. **Pin allowed hosts** using `globals.plugin_http.allowed_hosts`.
3. Keep `max_response_bytes` small.
4. Avoid rules that accept user-provided URLs or arbitrary paths.
5. Store secrets in files with strict permissions (`chmod 600`).

---

# 6. Troubleshooting

## “URL host not in allowed_hosts”

Your global `allowed_hosts` is set and the plugin URL does not match.

Fix by adding the host (optionally with port) to `globals.plugin_http.allowed_hosts`.

## “token file permissions too open”

Your Home Assistant token file should not be readable by group/others.

Fix:

```sh
chmod 600 ~/.config/signal-cli-agent/ha_token
```

## “response exceeded max_response_bytes”

The endpoint returned more data than allowed.

Fix by:

- increasing `globals.plugin_http.max_response_bytes`, or
- adjusting the endpoint to return less data
