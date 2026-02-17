# plugins/base.py

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class PluginResult:
    """
    Standardized result object returned by plugins.
    """
    status: str                 # "ok" | "error"
    exit_code: int              # 0 = success, non-zero = failure
    body: str                   # human-friendly output
    meta: Dict[str, Any] = field(default_factory=dict)


class BasePlugin:
    """
    Minimal plugin interface.
    """
    name: str = "base"

    def validate(self, rule: Dict[str, Any], globals_raw: Dict[str, Any]) -> None:
        """
        Raise ValueError if rule/plugin config is invalid.
        Called on each run (safe), can later be moved to load-time validation.
        """
        return

    def run(self, rule: Dict[str, Any], globals_raw: Dict[str, Any], context: Dict[str, Any]) -> PluginResult:
        """
        Execute plugin. Must return PluginResult and never raise unhandled exceptions.
        """
        raise NotImplementedError