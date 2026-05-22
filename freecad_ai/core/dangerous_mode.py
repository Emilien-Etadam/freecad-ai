"""Dangerous-mode state for FreeCAD AI.

Dangerous mode relaxes the executor's safety layers and widens run_macro's
file-resolution reach. It can be armed for the current session (in-memory,
never persisted) or persisted by hand-editing ``dangerous_skip_safety: true``
in config.json. ``active`` is the single source of truth consulted at the
execution edges.
"""


class DangerousMode:
    def __init__(self):
        self._session_armed = False

    @property
    def persisted(self) -> bool:
        """True if config.json has dangerous_skip_safety set (hand-edited)."""
        from ..config import get_config
        return bool(getattr(get_config(), "dangerous_skip_safety", False))

    @property
    def active(self) -> bool:
        """True if dangerous mode is in effect (session-armed OR persisted)."""
        return self._session_armed or self.persisted

    def arm(self) -> None:
        """Arm for the current session only. Never touches config."""
        self._session_armed = True

    def disarm(self) -> None:
        self._session_armed = False


_INSTANCE = None


def get_dangerous_mode() -> DangerousMode:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = DangerousMode()
    return _INSTANCE
