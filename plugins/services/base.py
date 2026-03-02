# plugins/services/base.py

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional


# A service plugin is a long-running extension that adds capabilities to the agent
# (e.g., an HTTP server). It is distinct from a *rule plugin* (action plugin) which
# is executed only when a rule matches.


SendFn = Callable[[str, str], None]


@dataclass
class ServiceContext:
    """Context provided to service plugins at start-time."""

    # Thread-safe function for sending a Signal message.
    send_message: SendFn

    # Whether the agent is currently in dry-run mode.
    dry_run: bool = False

    # Optional extra context bag for future service types.
    extra: Optional[Dict[str, Any]] = None


class BaseServicePlugin:
    """Base interface for service plugins."""

    name: str = "base_service"

    def validate(self, globals_raw: Dict[str, Any]) -> None:
        """Validate configuration for this service plugin.

        Implementations should raise ValueError for invalid config.
        """

        return

    def start(self, globals_raw: Dict[str, Any], ctx: ServiceContext) -> None:
        """Start the service.

        Must not block indefinitely. Long-running servers should run in a background
        thread.
        """

        raise NotImplementedError

    def stop(self) -> None:
        """Stop the service and release resources."""

        return
