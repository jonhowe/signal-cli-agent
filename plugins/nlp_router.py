# plugins/nlp_router.py
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _read_token_file(path: str) -> str:
    p = os.path.expanduser(path)
    with open(p, "r", encoding="utf-8") as f:
        token = f.read().strip()
    if not token:
        raise ValueError(f"NLP token file is empty: {path}")
    return token


def _post_json(url: str, headers: Dict[str, str], body: Dict[str, Any], timeout_sec: int) -> Dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url=url,
        data=data,
        method="POST",
        headers={**headers, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        raise ValueError(f"LiteLLM HTTP {e.code}: {raw[:2000]}")
    except urllib.error.URLError as e:
        raise ValueError(f"LiteLLM URL error: {e}")


def _dump_resp(resp: Dict[str, Any], limit: int = 2000) -> str:
    try:
        s = json.dumps(resp, ensure_ascii=False)
    except Exception:
        s = str(resp)
    s = s.replace("\n", "\\n")
    return s[:limit]


def _finish_reason(resp: Dict[str, Any]) -> str:
    try:
        return str(resp.get("choices", [{}])[0].get("finish_reason", "")).strip()
    except Exception:
        return ""


def _extract_content_from_resp(resp: Dict[str, Any]) -> str:
    """
    Robustly extract text content from OpenAI-ish responses.

    Handles:
      - choices[0].message.content is string
      - choices[0].message.content is list of blocks
      - choices[0].text exists (older schema)
      - proxy error objects returned with 200
    """
    # Some proxies return {"error": {...}} with HTTP 200
    if isinstance(resp.get("error"), dict):
        return json.dumps(resp["error"], ensure_ascii=False)

    choices = resp.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""

    c0 = choices[0]
    if not isinstance(c0, dict):
        return ""

    # Older/alt schema
    if isinstance(c0.get("text"), str) and c0.get("text").strip():
        return c0["text"]

    msg = c0.get("message")
    if isinstance(msg, dict):
        content = msg.get("content")

        if isinstance(content, str):
            return content

        # Some APIs: list of content blocks
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    if isinstance(item.get("text"), str):
                        parts.append(item["text"])
                    elif isinstance(item.get("content"), str):
                        parts.append(item["content"])
                    elif isinstance(item.get("value"), str):
                        parts.append(item["value"])
            return "\n".join([p for p in parts if p.strip()])

    return ""


def _extract_json_object(s: str) -> Dict[str, Any]:
    """
    Extract a JSON object from model output. Supports:
      - pure JSON
      - JSON wrapped in code fences
      - extra text containing a JSON object somewhere
    """
    s = (s or "").strip()

    # Strip code fences
    if s.startswith("```"):
        s = s.strip("`").strip()
        if s.lower().startswith("json"):
            s = s[4:].strip()

    if s.startswith("{") and s.endswith("}"):
        return json.loads(s)

    m = _JSON_RE.search(s)
    if not m:
        raise ValueError("Model did not return JSON object.")
    return json.loads(m.group(0))


def _temperature_for_model(model: str, configured: Any) -> float:
    """
    LiteLLM / GPT-5 constraint: GPT-5 model groups require temperature=1.
    """
    m = (model or "").lower()
    if "gpt-5" in m:
        return 1.0
    if configured is None or configured == "":
        return 0.0
    return float(configured)


@dataclass
class NlpRouteResult:
    rule: str
    confidence: float
    reason: str = ""


def route_message(
    globals_raw: Dict[str, Any],
    message: str,
    candidates: List[Dict[str, str]],
    sender: str = "",
    **_kwargs: Any,
) -> Optional[NlpRouteResult]:
    """
    Route a message using LiteLLM (OpenAI-compatible /v1/chat/completions).

    Returns:
      - NlpRouteResult if NLP routing is enabled and a response was produced.
        (This includes "no_match" and low-confidence decisions; callers should
        apply policy thresholds themselves.)
      - None if NLP routing is disabled

    GPT-5 NOTE:
      GPT-5 family can consume the entire completion budget as reasoning tokens and
      return empty content (finish_reason="length"). We retry once with a larger
      max_tokens and a "print now" instruction.
    """
    cfg = dict(globals_raw.get("nlp") or {})
    if not bool(cfg.get("enabled", False)):
        return None

    base_url = str(cfg.get("base_url", "")).strip().rstrip("/")
    if not base_url:
        raise ValueError("globals.nlp.base_url is required (e.g. http://127.0.0.1:4001/v1)")

    model = str(cfg.get("model", "")).strip()
    if not model:
        raise ValueError("globals.nlp.model is required")

    timeout_sec = int(cfg.get("timeout_sec", 8))
    # NOTE: min_confidence is intentionally NOT enforced here. The caller
    # (signal-agent) applies confidence gating so we can still return
    # structured "no_match" responses for observability/testing.
    _min_conf = float(cfg.get("min_confidence", 0.85))

    # For GPT-5, default higher max_tokens so it can actually emit content
    max_tokens_cfg = cfg.get("max_tokens", cfg.get("max_output_tokens", 800))
    try:
        max_tokens = int(max_tokens_cfg)
    except Exception:
        max_tokens = 800

    temperature = _temperature_for_model(model, cfg.get("temperature", 1))

    token_file = str(cfg.get("token_file", "")).strip()
    token = _read_token_file(token_file) if token_file else ""

    allowed: List[Dict[str, str]] = []
    for c in candidates:
        allowed.append(
            {
                "name": c.get("name", ""),
                "description": c.get("description", ""),
                "phrases": c.get("phrases", ""),
            }
        )

    system_prompt = (
        "You are a strict routing function.\n"
        "Return ONLY one JSON object and nothing else.\n"
        "No markdown, no code fences, no extra keys.\n"
        "Schema: {\"rule\":\"<rule_name|no_match>\",\"confidence\":0..1,\"reason\":\"<short>\"}\n"
        "rule MUST be exactly one of allowed_rules[].name OR \"no_match\".\n"
    )

    user_payload = {
        "message": message,
        "allowed_rules": allowed,
        # reserved for future policy use:
        # "sender": sender,
    }

    body: Dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    headers: Dict[str, str] = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = f"{base_url}/chat/completions"
    resp = _post_json(url, headers=headers, body=body, timeout_sec=timeout_sec)
    content = _extract_content_from_resp(resp).strip()

    # Retry once if we got empty content due to finish_reason=length (common with GPT-5 reasoning)
    if not content and _finish_reason(resp) == "length":
        retry_tokens = max(max_tokens * 4, 1200)
        body_retry = dict(body)
        body_retry["max_tokens"] = retry_tokens
        body_retry["messages"] = list(body["messages"]) + [
            {
                "role": "system",
                "content": "Your previous output was empty. Output the JSON object now. Do not include any other text.",
            }
        ]
        resp = _post_json(url, headers=headers, body=body_retry, timeout_sec=timeout_sec)
        content = _extract_content_from_resp(resp).strip()

    if not content:
        raise ValueError(f"LiteLLM returned empty content. Full response: {_dump_resp(resp)}")

    try:
        obj = _extract_json_object(content)
    except Exception as e:
        snippet = content.replace("\n", "\\n")[:300]
        raise ValueError(f"{e} Raw content (first 300 chars): {snippet}. Full response: {_dump_resp(resp)}")

    rule = str(obj.get("rule", "")).strip()
    confidence = float(obj.get("confidence", 0.0))
    reason = str(obj.get("reason", "")).strip()

    if not rule:
        raise ValueError("Model response missing required key: rule")

    return NlpRouteResult(rule=rule, confidence=confidence, reason=reason)