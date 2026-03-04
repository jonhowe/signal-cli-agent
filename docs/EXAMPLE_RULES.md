# Example Rule Set

---

## Rule 1 — `bedroom_on`

### Purpose
Turns on a Home Assistant scene for the bedroom (e.g., lights/scene configuration).

### Trigger
- Exact command: `wake up!`

### Plugin / Type
- `home_assistant_service` (write/state-changing)

### Notes
- This is a state-changing action; keep sender allowlists tight and consider rate limits.
- NLP routing is enabled for natural-language variants.

### Sanitized YAML
```yaml
- name: bedroom_on
  sender:
    - "<E164_NUMBER_1>"
    - "<E164_NUMBER_2>"
  trigger: "wake up!"
  match: exact

  type: home_assistant_service
  home_assistant_service:
    domain: scene
    service: turn_on
    entity_id: scene.bedroom_on
    label: "Bedroom"

  reply_mode: bare
  split_reply: false

  nlp:
    enabled: true
    phrases:
      - "turn on the bedroom lights"
      - "bedroom lights on"
      - "make the bedroom cozy"
```

---

## Rule 2 — `calendar_status`

### Purpose
Returns a text “calendar/status” style response derived from a local JSON file via a script.

### Trigger
- Exact command: `what’s on the schedule?`

### Plugin / Type
- `command` (executes a local command inside the container)

### Notes
- The script path is inside the container (`/app/scripts/Query-JSON.py`).
- The JSON path is a mounted config artifact (`/config/exchange.json`).
- Reply is chunked for long output.

### Sanitized YAML
```yaml
- name: calendar_status
  sender:
    - "<E164_NUMBER_1>"
    - "<E164_NUMBER_2>"
  trigger: "what’s on the schedule?"
  match: exact

  command:
    - "/usr/bin/python3"
    - "/app/scripts/Query-JSON.py"
    - "/config/exchange.json"

  reply_mode: bare
  split_reply: true
  chunk_size: 1400
  numbered_chunks: true

  nlp:
    enabled: true
    phrases:
      - "what’s on the schedule?"
      - "what’s happening today?"
      - "what does the calendar look like?"
      - "what’s coming up next?"
```

---

## Rule 3 — `rotation_week`

### Purpose
Queries Home Assistant for the current “Week A / Week B” (or similar rotation) using a template sensor/state.

### Trigger
- Exact command: `what week is it?`

### Plugin / Type
- `home_assistant` (read-only template action)

### Notes
- Removed the explicit Home Assistant URL and token file from the rule.
  - Best practice: set them globally in `rules.yaml` under `globals.home_assistant`.
- The template text was updated to remove disallowed terminology.

### Sanitized YAML
```yaml
- name: rotation_week
  sender:
    - "<E164_NUMBER_1>"
    - "<E164_NUMBER_2>"
  trigger: "what week is it?"
  match: exact

  type: home_assistant
  home_assistant:
    action: template

    # Recommended: set these globally in rules.yaml under globals.home_assistant
    # url: "<HOME_ASSISTANT_URL>"
    # token_file: "<TOKEN_FILE_PATH>"

    template: >-
      It is currently {{ states('sensor.rotation_week_a_or_b') }}

    strip: true
    empty_as: "Unknown"

  reply_mode: bare
  split_reply: false

  nlp:
    enabled: true
    phrases:
      - "what week is it"
      - "week a or b"
      - "am I on week a or week b"
      - "which week is it"
```

---

## Rule 4 — `device_location`

### Purpose
Returns a location string (e.g., geocoded location) from Home Assistant via a template.

### Trigger
- Exact command: `where is the device?`

### Plugin / Type
- `home_assistant` (read-only template action)

### Notes
- Removed explicit Home Assistant URL and token file from the rule (recommend global config).
- Template text and NLP phrases were updated to remove disallowed terminology.

### Sanitized YAML
```yaml
- name: device_location
  sender:
    - "<E164_NUMBER_1>"
    - "<E164_NUMBER_2>"
  trigger: "where is the device?"
  match: exact

  type: home_assistant
  home_assistant:
    action: template

    # Recommended: set these globally in rules.yaml under globals.home_assistant
    # url: "<HOME_ASSISTANT_URL>"
    # token_file: "<TOKEN_FILE_PATH>"

    template: >-
      Device location: {{ states('sensor.device_geocoded_location') }}

    strip: true
    empty_as: "Unknown"

  reply_mode: bare
  split_reply: false

  nlp:
    enabled: true
    phrases:
      - "where is the device?"
      - "what is the device location?"
      - "where are you?"
      - "share location"
```

---

## Plugin Association Summary

- **`home_assistant_service`**
  - Used by: `bedroom_on`
  - Writes/changing state in Home Assistant (service calls)

- **`command`**
  - Used by: `calendar_status`
  - Runs local commands/scripts inside the container

- **`home_assistant`**
  - Used by: `rotation_week`, `device_location`
  - Read-only actions (template rendering / state lookup)

---

## Recommended Global Configuration (to avoid per-rule URLs/tokens)

In your root `rules.yaml`:

```yaml
globals:
  home_assistant:
    url: "<HOME_ASSISTANT_URL>"
    token_file: "<TOKEN_FILE_PATH>"
```

This keeps rule files portable and avoids embedding URLs or secrets in rule YAML.
