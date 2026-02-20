# plugins/registry.py

from __future__ import annotations

from typing import Optional

from .base import BasePlugin
from .home_assistant import HomeAssistantPlugin
from .home_assistant_service import HomeAssistantServicePlugin
from .http_get import HttpGetPlugin


# Phase 0/1 registry
_PLUGIN_REGISTRY = {
    "home_assistant": HomeAssistantPlugin(),
    "home_assistant_service": HomeAssistantServicePlugin(),
    "http_get": HttpGetPlugin(),
}


def get_plugin(name: str) -> Optional[BasePlugin]:
    if not name:
        return None
    return _PLUGIN_REGISTRY.get(name.strip().lower())
