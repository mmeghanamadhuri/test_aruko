"""
Choose between the local (Jetson GPIO direct) and remote (Pi serial
bridge) navigation manager based on `NavigationSettings.mode`.

Used by `sirena_ui.workers.nina_service` and by the CLI tools so the
selection logic lives in exactly one place. Tests can pass a fake
settings object to assert the routing.

The factory returns an *un-initialised* manager - the caller is still
responsible for `initialize()` (so we don't open serial ports on
import).

Env var summary (read at settings-load time, see `nina.config.settings`):

    NINA_NAV_MODE=local        # default; drives Jetson GPIOs directly
    NINA_NAV_MODE=remote       # talks to pi_motor_bridge over serial
    NINA_NAV_REMOTE_PORT       # default /dev/ttyUSB0
    NINA_NAV_REMOTE_BAUD       # default 115200
    NINA_NAV_REMOTE_TIMEOUT_SEC# default 0.4
"""

from __future__ import annotations

from typing import Any

from nina.config.settings import NavigationSettings
from nina.controllers.navigation_manager import (
    DEFAULT_PINS,
    NavigationConfig,
    NavigationManager,
)


def build_navigation_manager(settings: NavigationSettings) -> Any:
    """Return either a `NavigationManager` (local) or a
    `RemoteNavigationManager` (remote), un-initialised.

    The return type is `Any` because the two managers don't share a
    common base class, but they both implement the same public
    surface (initialize, shutdown, forward, backward, turn_left,
    turn_right, drive_continuous, set_wheels, stop, emergency_stop,
    engage_brake, release_brake, set_status). All callers in the
    codebase only touch this surface.
    """
    if settings.mode == "remote":
        # Lazy import so the rest of the app doesn't pay the pyserial
        # import cost when running purely local.
        from nina.controllers.remote_navigation_manager import (
            RemoteNavigationConfig,
            RemoteNavigationManager,
        )
        cfg = RemoteNavigationConfig(
            serial_port=settings.remote_serial_port,
            baudrate=settings.remote_baudrate,
            response_timeout_sec=settings.remote_response_timeout_sec,
            default_speed_percent=settings.default_speed_percent,
            turn_duration_sec=settings.turn_duration_sec,
            invert_left_dir=settings.invert_left_dir,
            invert_right_dir=settings.invert_right_dir,
        )
        return RemoteNavigationManager(cfg)

    cfg_local = NavigationConfig(
        pins=DEFAULT_PINS,
        backend_name=settings.backend_name,
        pwm_frequency_hz=settings.pwm_frequency_hz,
        default_speed_percent=settings.default_speed_percent,
        turn_duration_sec=settings.turn_duration_sec,
        invert_left_dir=settings.invert_left_dir,
        invert_right_dir=settings.invert_right_dir,
    )
    return NavigationManager(cfg_local)
