# Signal CLI Agent — Rule & Configuration Reference

This document is the authoritative reference for:

- `rules.yaml` (root configuration)
- `rules.d/*.yaml` (production rules)
- Rule evaluation lifecycle
- All supported rule fields
- Matching behavior
- Command execution behavior
- Output formatting and chunking

Plugin rules are covered at a high level here. For full plugin configuration and supported plugins, see:

👉 **[PLUGINS.md](PLUGINS.md)**

This is a technical reference, not a quick-start guide.

---

# Schema Summary (Quick Reference)

## Root Configuration (`rules.yaml`)

```yaml
globals:
  deny_groups: bool
  default_timeout_sec: int
  default_max_reply_chars: int
  default_split_reply: bool
  default_chunk_size: int
  numbered_chunks: bool
  default_match: exact|contains|startswith|regex
  default_case_sensitive: bool
  default_cooldown_sec: int
  default_max_runs_per_hour: int
  reply_prefix: str
  reply_suffix: str
  default_reply_to: sender|admin
  admin: str
  redact_regex: [regex, ...]
  log_level: DEBUG|INFO|WARNING|ERROR
  log_format: text|json
  log_file: str
  dry_run: bool

  # Optional: plugin default config blocks (plugin-dependent)
  # home_assistant: { ... }
  # http_get: { ... }

rules_dir: "/absolute/path/to/rules.d"
rules:
  - <rule>
```

---

## Rule Schema

```yaml
- name: str
  sender: str | [str, ...]
  trigger: str
  match: exact|contains|startswith|regex

  # Optional: plugin rule type (if set, this rule uses a plugin instead of command execution)
  type: str               # e.g. "home_assistant" | "home_assistant_service" | "http_get"
  <plugin_name>: { ... }  # plugin-specific config block (see PLUGINS.md)

  command: [str, ...]
  # OR
  command_template: [str, ...]
  args:
    param_name:
      type: int|str
      min: int
      max: int

  timeout_sec: int
  reply_mode: full|output|bare
  reply_prefix: str
  reply_suffix: str

  split_reply: bool
  chunk_size: int
  numbered_chunks: bool

  cooldown_sec: int
  max_runs_per_hour: int

  reply_to: sender|admin|number
  reply_number: str

  dry_run: bool

  # Optional: enable NLP routing (LiteLLM) as a fallback when no rule matches.
  nlp:
    enabled: bool
    phrases: [str, ...]
```

---

# Table of Contents

- [1. Configuration Layers](#1-configuration-layers)
- [2. Rule Evaluation Lifecycle](#2-rule-evaluation-lifecycle)
- [3. Root Configuration (`globals`)](#3-root-configuration-globals)
- [4. Rule Structure](#4-rule-structure)
- [4.6 Plugin Rules](#46-plugin-rules)
- [5. Splitting & Chunking](#5-splitting--chunking)
- [6. Safe vs Unsafe Rule Patterns](#6-safe-vs-unsafe-rule-patterns)
- [7. Template vs Production Rules](#7-template-vs-production-rules)
- [8. Security Model](#8-security-model)

---

# 1. Configuration Layers

Signal CLI Agent loads configuration in two stages:

1. `rules.yaml`
2. All YAML files inside `rules_dir`

Rules from all files are merged in load order.

---

# 2. Rule Evaluation Lifecycle

When a message arrives:

1. Reload configuration if changed.
2. Iterate through rules.
3. For each rule:
   - Check sender
   - Check trigger match
   - Check rate limits
4. Execute first matching rule.
5. Stop evaluation.

**First match wins.**

---

# 3. Root Configuration (`globals`)

Globals provide default values used by rules that do not override them.

Example:

```yaml
globals:
  default_timeout_sec: 5
  default_match: exact
  default_cooldown_sec: 2
  dry_run: false
```

Rules may override any default.

---

# 4. Rule Structure

## 4.1 Required Fields

- `name`
- `trigger`
- `match`

---

## 4.2 Sender Control

Single sender:

```yaml
sender: "+15551234567"
```

Multiple senders:

```yaml
sender:
  - "+15551234567"
  - "+15557654321"
```

If omitted, rule allows all senders.

---

## 4.3 Matching Modes

### exact

Must match entire message.

```yaml
match: exact
trigger: "disk?"
```

---

### contains

Trigger must appear anywhere.

```yaml
match: contains
trigger: "disk"
```

---

### startswith

Message must begin with trigger.

```yaml
match: startswith
trigger: "tail "
```

---

### regex

Trigger treated as regex.

```yaml
match: regex
trigger: "^tail (\\d+)$"
```

Use `args` to validate captured values.

---

## 4.4 Command Execution

### `command`

Safe argv execution.

```yaml
command: ["ls", "-lah", "/home/user"]
```

Properties:

- No shell interpretation
- No variable expansion
- No globbing
- No injection

---

### `command_template`

Parameterized execution with validation.

```yaml
command_template: ["journalctl", "-n", "{n}"]
```

Requires `args`.

Execution process:

1. Regex captures values.
2. Values validated.
3. Template rendered.
4. Executed as argv list.

---

### `timeout_sec`

Maximum allowed execution time.

---

## 4.5 Output Formatting

### reply_mode

- `full`
- `output`
- `bare`

---

## 4.6 Plugin Rules

A rule can execute either:

- a **local command** (`command` / `command_template`), or
- a **plugin** (`type: <plugin_name>`)

If `type` is present and non-empty, the agent treats the rule as a **plugin rule** and ignores `command` / `command_template` for that rule.

Plugin rules must include a plugin config block named after the plugin.

Example (conceptual):

```yaml
- name: example_plugin
  sender: "+15551234567"
  trigger: "example?"
  match: exact

  type: http_get
  http_get:
    url: "https://example.com/status"

  reply_mode: output
```

For plugin-specific configuration fields, validation rules, and supported plugins, see:

👉 **[PLUGINS.md](PLUGINS.md)**

---

# 5. Splitting & Chunking

## What It Is

Signal messages have size limits. Large command output may exceed limits.

Chunking splits output into multiple messages.

---

## Configuration

```yaml
split_reply: true
chunk_size: 1400
numbered_chunks: true
```

---

## Behavior

If output length > chunk_size:

- Output split at newline boundaries.
- Multiple messages sent.

If numbered_chunks enabled:

```
[rule message 1/3]
...
```

If only one message needed:

- No numbering applied.

---

# 6. Safe vs Unsafe Rule Patterns

This section demonstrates good and bad practices.

---

## SAFE Pattern: Explicit Command List

```yaml
command: ["ls", "-lah", "/home/user"]
```

Why safe:

- No shell expansion
- Arguments explicitly defined
- No user input injection

---

## SAFE Pattern: Regex + Validated Args

```yaml
trigger: "^tail (\\d+)$"
match: regex
command_template: ["journalctl", "-n", "{n}"]
args:
  n:
    type: int
    min: 1
    max: 200
```

Why safe:

- Only integers allowed
- Upper bound enforced
- No arbitrary command execution

---

## UNSAFE Pattern: Broad Regex Without Validation

```yaml
trigger: "^run (.*)$"
match: regex
command_template: ["bash", "-c", "{cmd}"]
args:
  cmd:
    type: str
```

Why unsafe:

- Arbitrary command injection
- Full shell access
- Extremely dangerous

---

## UNSAFE Pattern: Overly Permissive Matching

```yaml
match: contains
trigger: "run"
```

Why unsafe:

- Matches unintended messages
- May execute accidentally

---

## SAFE Pattern: Explicit Sender Restrictions

```yaml
sender:
  - "+15551234567"
```

Never omit sender for powerful commands.

---

# 7. Template vs Production Rules

Templates:

```
templates/rules/*.yaml.in
```

Rendered into:

```
rules.d/example-rules.yaml
```

User-created rules in `rules.d/`:

- Never overwritten
- Never deleted by clean/uninstall

---

# 8. Security Model

The agent enforces:

- No shell execution
- Explicit argv execution
- Regex argument validation
- Rate limiting
- Optional redaction

User responsibility:

- Restrict senders
- Avoid permissive regex
- Validate parameters
- Store secrets securely
- Avoid destructive commands

---

# 9. NLP Routing (optional)

Signal CLI Agent can optionally use a local LiteLLM proxy to map free-form text
to a **pre-approved** rule ("less strict" prompts). This is disabled by default.

See: **docs/NLP.md**