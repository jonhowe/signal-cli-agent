# plugins/services/registry.py

from __future__ import annotations

from typing import List

from .base import BaseServicePlugin
from .rest_api import RestApiService


# Service plugin registry.
# These are long-running components started once at agent startup.

_SERVICE_REGISTRY = {
    "rest_api": RestApiService(),
}


def all_services() -> List[BaseServicePlugin]:
    return list(_SERVICE_REGISTRY.values())
