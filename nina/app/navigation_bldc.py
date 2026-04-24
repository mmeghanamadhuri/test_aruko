"""
Deprecated module-level shim.

The original procedural navigation script has been refactored into
nina.controllers.navigation_manager.NavigationManager.

This file is kept temporarily so existing call sites importing
`navigation_bldc.<function>` continue to work while callers migrate.
Prefer the controller class for any new code.
"""

import warnings
from typing import Optional

from nina.controllers.navigation_manager import (
    DEFAULT_PINS,
    NavigationConfig,
    NavigationManager,
)


warnings.warn(
    "nina.app.navigation_bldc is deprecated; use nina.controllers.navigation_manager.NavigationManager.",
    DeprecationWarning,
    stacklevel=2,
)


_manager: Optional[NavigationManager] = None
object_pi = None


def _get_manager() -> NavigationManager:
    global _manager
    if _manager is None:
        _manager = NavigationManager(NavigationConfig(pins=DEFAULT_PINS))
    return _manager


def setup_gpio() -> bool:
    global object_pi
    try:
        manager = _get_manager()
        manager.initialize()
        object_pi = manager
        return True
    except Exception as exc:
        print(f"[ERROR] setup_gpio failed: {exc}")
        return False


def stop() -> None:
    _get_manager().stop()


def forward_forever() -> None:
    _get_manager().forward()


def backward_forever() -> None:
    _get_manager().backward()


def turn_left() -> None:
    _get_manager().turn_left()


def turn_right() -> None:
    _get_manager().turn_right()


def emergency_stop() -> None:
    global object_pi
    manager = _get_manager()
    try:
        manager.emergency_stop()
    finally:
        manager.shutdown()
        object_pi = None


def notifier(mode: str) -> None:
    _get_manager().set_status(mode)
