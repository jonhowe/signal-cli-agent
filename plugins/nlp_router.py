"""plugins/nlp_router.py

LiteLLM (OpenAI-compatible) intent router.

This is **not** an execution plugin. It's a helper used by signal-agent.py as a
fallback when no normal rule matches.

Design goals:
- Model can only choose from an allowlisted set of rule names.
- Output must be strict JSON: {"rule": "<name>|no_match", "confidence": 0-1, "reason": "..."}
- Agent enforces sender allowlists, cooldowns, and max/hour like any other rule.

Default LiteLLM proxy base URL (Option A): http://127.0.0.1:4000/v1
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


DEFAULT_BASE_URL = "http://127.0.0.1:4000/v1"


@dataclass
class RouteDecision:
    rule: str
    confidence: float
    reason: str = ""


_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


def _read_token_file(path: str) -> str:
    p = os.path.expanduser(path)
    with open(p, "r", encoding="utf-8") as f:
        return f.read().strip()


def _post_json(url: str, payload: Dict[str, Any], headers: Dict[str, str], timeout_sec: int) -> Dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url=url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            data = resp.read().decode("utf-8", errors="replace")
            return json.loads(data)
    except urllib.error.HTTPError as e:
        txt = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LiteLLM HTTP {e.code}: {txt[:400]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"LiteLLM URL error: {e}")


def _extract_json_object(s: str) -> Dict[str, Any]:
    s = (s or "").strip()
    if not s:
        raise ValueError("empty model response")
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    m = _JSON_OBJ_RE.search(s)
    if not m:
        raise ValueError("response did not contain a JSON object")
    obj = json.loads(m.group(0))
    if not isinstance(obj, dict):
        raise ValueError("parsed JSON was not an object")
    return obj


def route_message(
    globals_raw: Dict[str, Any],
    message: str,
    candidates: List[Dict[str, Any]],
    *,
    sender: str = "",
) -> Optional[RouteDecision]:
    """Ask LiteLLM to select a rule from candidates.

    Returns RouteDecision or None if routing is disabled or no candidates.
    Raises RuntimeError on hard failures contacting LiteLLM.
    """
    nlp = (globals_raw.get("nlp") or {}) if isinstance(globals_raw, dict) else {}
    if not bool(nlp.get("enabled", False)):
        return None
    if not candidates:
        return None

    base_url = str(nlp.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
    model = str(nlp.get("model") or "gpt-4o-mini")
    timeout_sec = int(nlp.get("timeout_sec") or 8)

    token_file = str(nlp.get("token_file") or "").strip()
    headers: Dict[str, str] = {}
    if token_file:
        token = _read_token_file(token_file)
        if token:
            headers["Authorization"] = f"Bearer {token}"

    allowed = []
    for c in candidates:
        allowed.append(
            {
                "name": str(c.get("name", "")),
                "description": str(c.get("description", ""))[:200],
                "phrases": (c.get("phrases") or [])[:8],
            }
        )

    system = (
        "You are a strict router. Choose exactly one rule from ALLOWED_RULES or choose 'no_match'. "
        "Return JSON only with keys: rule, confidence, reason. "
        "- rule: one of the allowed rule names or 'no_match'\n"
        "- confidence: number 0.0 to 1.0\n"
        "- reason: short string\n"
        "Never invent rule names. Never include commands."
    )

    user_obj = {
        "sender": sender,
        "message": message,
        "allowed_rules": allowed,
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user_obj, ensure_ascii=False)},
        ],
        "temperature": 0,
        "max_tokens": 200,
        "response_format": {"type": "json_object"},
    }

    url = f"{base_url}/chat/completions"
    resp = _post_json(url, payload, headers=headers, timeout_sec=timeout_sec)

    try:
        content = resp["choices"][0]["message"]["content"]
    except Exception:
        raise RuntimeError("LiteLLM response missing choices[0].message.content")

    obj = _extract_json_object(content)
    rule = str(obj.get("rule", "")).strip() or "no_match"
    try:
        conf = float(obj.get("confidence", 0.0))
    except Exception:
        conf = 0.0
    reason = str(obj.get("reason", "")).strip()
    return RouteDecision(rule=rule, confidence=conf, reason=reason)
