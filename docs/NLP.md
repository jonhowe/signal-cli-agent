# Signal CLI Agent — NLP Routing (LiteLLM)

This document describes the **optional** NLP routing layer that provides
"less strict" prompts.

The NLP router is a **fallback**: it only runs when no normal rule matches.

It uses a local **LiteLLM Proxy** (OpenAI-compatible) to map a free-form
message to a **pre-approved rule name**.

## Safety model

The model is never allowed to run commands.

It may only choose:
- a rule name from the candidate set (rules with `nlp.enabled: true`), or
- `no_match`

The agent enforces:
- sender allowlists
- cooldown / max-runs-per-hour
- rule execution safety (argv execution, plugin allowlists, etc.)

## Configuration

Add this under `globals:` in `rules.yaml` (or `rules.yaml.in` template):

```yaml
globals:
  nlp:
    enabled: true
    base_url: "http://127.0.0.1:4000/v1"   # LiteLLM default
    model: "gpt-4o-mini"                   # whatever your proxy routes
    token_file: "~/.config/signal-agent/litellm_token"  # optional
    timeout_sec: 8
    min_confidence: 0.85
```

Notes:
- `token_file` is optional. If set, it is read and passed as `Authorization: Bearer ...`.
- Keep `temperature=0` (built-in) for deterministic routing.

## Enabling rules for NLP routing

Only rules explicitly marked `nlp.enabled: true` are considered by the router.

Example:

```yaml
- name: bedroom_on
  description: "Turn on bedroom scene"
  sender: ["+15551234567"]
  trigger: "bedroom on"
  match: exact

  type: home_assistant_service
  home_assistant_service:
    domain: scene
    service: turn_on
    entity_id: scene.bedroom_on

  nlp:
    enabled: true
    phrases:
      - "turn on the bedroom lights"
      - "make the bedroom cozy"
```

`phrases` are optional, but help accuracy.

## Output contract

The router requests strict JSON:

```json
{"rule":"<rule_name|no_match>","confidence":0.0,"reason":"..."}
```

The agent rejects:
- rule names not in the candidate set
- decisions with confidence lower than `min_confidence`
