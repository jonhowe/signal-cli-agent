# plugins/registry.py

from __future__ import annotations

from typing import Optional

from .base import BasePlugin
from .home_assistant import HomeAssistantPlugin


# Phase 0 registry
_PLUGIN_REGISTRY = {
    "home_assistant": HomeAssistantPlugin(),
}


def get_plugin(name: str) -> Optional[BasePlugin]:
    if not name:
        return None
    return _PLUGIN_REGISTRY.get(name.strip().lower())