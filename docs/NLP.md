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
    base_url: "http://127.0.0.1:4001/v1"   # LiteLLM (example)
    model: "GPT-5 nano"                    # whatever your proxy routes
    token_file: "~/.config/signal-agent/litellm_token"  # optional
    timeout_sec: 8
    min_confidence: 0.85
    max_tokens: 800
    temperature: 1
```

Notes:
- `token_file` is optional. If set, it is read and passed as `Authorization: Bearer ...`.
- If you use GPT-5 models, set `temperature: 1` (some GPT-5 model groups reject `temperature: 0`).

### Command prefix gate

If you enable `globals.command_prefix`, users must prefix NLP commands too.
The prefix is stripped before NLP routing.

Example:

```
! can you turn on the bedroom lights?
```

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

## GPT-5: empty content with finish_reason=length

If you see logs like:

- `finish_reason: "length"`
- `message.content: ""`
- `completion_tokens_details.reasoning_tokens` equals the max tokens

…it means the model consumed the entire completion budget on reasoning and produced no visible output.

Fix:
- increase `globals.nlp.max_tokens` (try 600–1200)
- keep `temperature: 1` for GPT-5 models