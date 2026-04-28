"""
Remote BLDC navigation manager (Jetson side of the Pi motor bridge).

Drop-in replacement for `nina.controllers.navigation_manager.NavigationManager`.
Instead of touching the Jetson's GPIOs, every public call serializes
a one-line ASCII command over a serial port to the Raspberry Pi
running `pi_motor_bridge/motor_bridge.py`. The Pi owns the JYQD
drivers and does the actual switching.

The public surface matches `NavigationManager` 1:1 (same method names,
same kwargs, same constants) so `DriveController`, the autonomy pilot,
and the CLI tools work without changes - the factory in
`navigation_factory.py` decides which implementation to instantiate
based on `NavigationSettings.mode`.

Wire protocol is documented in `pi_motor_bridge/motor_bridge.py`:

    PING                              -> PONG
    SET <ldir> <lspeed> <rdir> <rspeed>
    STOP    (PWM=0, EL HIGH)
    ESTOP   (PWM=0, EL LOW)
    LED <mode>
    READY                             (async, on bridge boot)
    EVT WATCHDOG                      (async, when Pi watchdog parks the wheels)

Direction inversion:
  The Pi side already mirrors the right-wheel polarity inside
  `control_speed()`. This class additionally honours
  `RemoteNavigationConfig.invert_left_dir` /
  `invert_right_dir` so the same `NINA_NAV_INVERT_*` env var that
  worked in local mode also works in remote mode. If a wheel is
  spinning the wrong way, set the env var on the Jetson - no Pi-side
  code change needed.

Failure model:
  All public methods are best-effort. If the serial port is closed,
  unreachable, or returns `ERR ...`, the call logs a warning and
  returns. The GUI will still see a connected NavigationManager-like
  object; it just won't drive anything. This matches the existing
  "simulation mode" fallback in `DriveController`.

Reconnection:
  `initialize()` opens the port and verifies the bridge with a `PING`.
  If the port drops mid-session, `_send_command()` will try to reopen
  it lazily on the next call, so a Pi reboot doesn't permanently
  brick the link.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("nina.navigation.remote")


@dataclass(frozen=True)
class RemoteNavigationConfig:
    """Settings for the serial-bridge navigation manager.

    serial_port            : OS path of the UART / USB-UART device
                              (default `/dev/ttyUSB0`; if Dynamixel
                              already owns USB0, use `/dev/ttyUSB1`).
    baudrate               : must match `motor_bridge.py --baud`.
    response_timeout_sec   : per-line response wait. Keep small
                              (~0.4 s) so the GUI's tick loop never
                              blocks visibly.
    connect_timeout_sec    : how long `initialize()` waits to see the
                              bridge respond to a PING. Bridges that
                              just booted may emit a `READY` first.
    default_speed_percent  : exposed so `DriveController` and the
                              autonomy pilot can read a default the
                              same way they do in local mode.
    turn_duration_sec      : in-place spin time for `turn_left`/`turn_right`.
    invert_left_dir        : flip left-wheel forward/backward.
    invert_right_dir       : flip right-wheel forward/backward.
    """
    serial_port: str = "/dev/ttyUSB0"
    baudrate: int = 115200
    response_timeout_sec: float = 0.4
    connect_timeout_sec: float = 2.0
    default_speed_percent: int = 15
    turn_duration_sec: float = 2.3
    invert_left_dir: bool = False
    invert_right_dir: bool = False


class RemoteNavigationManager:
    """Serial-bridge implementation of the NavigationManager interface."""

    SIDE_LEFT = "left"
    SIDE_RIGHT = "right"
    DIR_FORWARD = "forward"
    DIR_BACKWARD = "backward"

    def __init__(self, config: Optional[RemoteNavigationConfig] = None) -> None:
        self.config = config or RemoteNavigationConfig()
        # Lazy serial import so the rest of the codebase doesn't pay
        # the pyserial import cost when running in local mode.
        self._serial_module = None
        self._port = None  # type: ignore[assignment]
        self._lock = threading.Lock()
        self._is_initialized = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        if self._is_initialized:
            return
        try:
            import serial as serial_module  # noqa: WPS433 (lazy)
        except ImportError as exc:
            raise RuntimeError(
                "pyserial is required for remote navigation mode; "
                "install with `pip install pyserial`"
            ) from exc
        self._serial_module = serial_module
        self._open_port()

        # Bridges send `READY` once on boot. If we caught the boot, drain
        # it; otherwise the buffer is empty and we just probe with PING.
        time.sleep(0.2)
        try:
            self._port.reset_input_buffer()
        except Exception:
            pass

        if not self._send_command("PING", expect="PONG"):
            log.warning(
                "Bridge at %s did not reply to PING (it may still come up)",
                self.config.serial_port,
            )
        else:
            log.info(
                "RemoteNavigationManager connected to %s @ %d",
                self.config.serial_port,
                self.config.baudrate,
            )
        self._is_initialized = True

    def shutdown(self) -> None:
        if not self._is_initialized:
            return
        try:
            self.emergency_stop()
        finally:
            self._close_port()
            self._is_initialized = False
            log.info("RemoteNavigationManager shutdown")

    # ------------------------------------------------------------------
    # Motion API (same surface as NavigationManager)
    # ------------------------------------------------------------------

    def forward(self, speed_percent: Optional[int] = None) -> None:
        speed = self._resolve_speed(speed_percent)
        self.set_wheels(
            left_dir=self.DIR_FORWARD, left_speed=speed,
            right_dir=self.DIR_FORWARD, right_speed=speed,
        )
        log.info("forward speed=%s%%", speed)

    def backward(self, speed_percent: Optional[int] = None) -> None:
        speed = self._resolve_speed(speed_percent)
        self.set_wheels(
            left_dir=self.DIR_BACKWARD, left_speed=speed,
            right_dir=self.DIR_BACKWARD, right_speed=speed,
        )
        log.info("backward speed=%s%%", speed)

    def turn_left(
        self,
        speed_percent: Optional[int] = None,
        duration: Optional[float] = None,
    ) -> None:
        """In-place pivot left: left wheel reverses, right wheel forwards."""
        speed = self._resolve_speed(speed_percent)
        self._timed_turn(
            left_dir=self.DIR_BACKWARD,
            right_dir=self.DIR_FORWARD,
            speed=speed,
            duration=duration,
        )
        log.info("turn_left speed=%s%% (L=back R=forward)", speed)

    def turn_right(
        self,
        speed_percent: Optional[int] = None,
        duration: Optional[float] = None,
    ) -> None:
        """In-place pivot right: left wheel forwards, right wheel reverses."""
        speed = self._resolve_speed(speed_percent)
        self._timed_turn(
            left_dir=self.DIR_FORWARD,
            right_dir=self.DIR_BACKWARD,
            speed=speed,
            duration=duration,
        )
        log.info("turn_right speed=%s%% (L=forward R=back)", speed)

    def drive_continuous(
        self,
        left_dir: str,
        right_dir: str,
        speed_percent: Optional[int] = None,
    ) -> None:
        """Per-wheel motion that does not auto-stop (held D-pad style)."""
        if left_dir not in (self.DIR_FORWARD, self.DIR_BACKWARD):
            raise ValueError(f"Invalid left_dir '{left_dir}'")
        if right_dir not in (self.DIR_FORWARD, self.DIR_BACKWARD):
            raise ValueError(f"Invalid right_dir '{right_dir}'")
        speed = self._resolve_speed(speed_percent)
        self.set_wheels(
            left_dir=left_dir, left_speed=speed,
            right_dir=right_dir, right_speed=speed,
        )
        log.info("drive_continuous L=%s R=%s speed=%s%%", left_dir, right_dir, speed)

    def set_wheels(
        self,
        *,
        left_dir: str,
        left_speed: int,
        right_dir: str,
        right_speed: int,
    ) -> None:
        """Per-wheel direction + speed in one round-trip to the Pi."""
        if left_dir not in (self.DIR_FORWARD, self.DIR_BACKWARD):
            raise ValueError(f"Invalid left_dir '{left_dir}'")
        if right_dir not in (self.DIR_FORWARD, self.DIR_BACKWARD):
            raise ValueError(f"Invalid right_dir '{right_dir}'")
        l_letter = self._dir_letter(self.SIDE_LEFT, left_dir)
        r_letter = self._dir_letter(self.SIDE_RIGHT, right_dir)
        ls = max(0, min(100, int(left_speed)))
        rs = max(0, min(100, int(right_speed)))
        self._send_command(f"SET {l_letter} {ls} {r_letter} {rs}")

    def stop(self) -> None:
        """Soft stop: PWM=0 on both wheels, EL stays HIGH (chip armed)."""
        self._send_command("STOP")
        log.info("stop (PWM=0, EL=HIGH)")

    def emergency_stop(self) -> None:
        """Hard stop: PWM=0 + EL LOW on both wheels (chip disabled, no torque)."""
        log.warning("EMERGENCY STOP requested")
        self._send_command("ESTOP")

    def engage_brake(self) -> None:
        """Coast stop. Same semantics as the local manager (PWM=0 IS the brake)."""
        self.stop()
        log.info("brake engaged (PWM=0; motors coast)")

    def release_brake(self) -> None:
        """Logical brake-off. No-op (chip stays armed at EL HIGH)."""
        log.info("brake released (no-op; ready for next motion command)")

    def set_status(self, mode: str) -> None:
        """Drive the Pi's status LED. Modes: CONNECTED / ERROR / WAITING / OFF."""
        m = (mode or "OFF").upper()
        if m not in ("CONNECTED", "ERROR", "WAITING", "OFF"):
            log.warning("Unknown status mode '%s', sending OFF", mode)
            m = "OFF"
        self._send_command(f"LED {m}")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_speed(self, requested: Optional[int]) -> int:
        if requested is None:
            return int(self.config.default_speed_percent)
        return max(0, min(100, int(requested)))

    def _timed_turn(
        self,
        left_dir: str,
        right_dir: str,
        speed: int,
        duration: Optional[float],
    ) -> None:
        d = duration if duration is not None else self.config.turn_duration_sec
        self.set_wheels(
            left_dir=left_dir, left_speed=speed,
            right_dir=right_dir, right_speed=speed,
        )
        time.sleep(max(0.0, d))
        self.stop()

    def _dir_letter(self, side: str, direction: str) -> str:
        forward = direction == self.DIR_FORWARD
        if side == self.SIDE_LEFT and self.config.invert_left_dir:
            forward = not forward
        elif side == self.SIDE_RIGHT and self.config.invert_right_dir:
            forward = not forward
        return "F" if forward else "B"

    def _open_port(self) -> None:
        assert self._serial_module is not None
        try:
            self._port = self._serial_module.Serial(
                self.config.serial_port,
                self.config.baudrate,
                timeout=self.config.response_timeout_sec,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Cannot open serial port {self.config.serial_port}: {exc}"
            ) from exc

    def _close_port(self) -> None:
        if self._port is None:
            return
        try:
            self._port.close()
        except Exception:
            pass
        self._port = None

    def _ensure_port(self) -> bool:
        """Lazily reopen the serial port if it dropped, e.g. Pi rebooted."""
        if self._port is not None and getattr(self._port, "is_open", True):
            return True
        if self._serial_module is None:
            return False
        try:
            self._open_port()
            time.sleep(0.1)
            try:
                self._port.reset_input_buffer()
            except Exception:
                pass
            log.info("Serial port reopened")
            return True
        except Exception as exc:
            log.warning("Serial reopen failed: %s", exc)
            return False

    def _send_command(self, cmd: str, *, expect: Optional[str] = None) -> bool:
        """Send one ASCII line and read one response line.

        Returns True on a clean reply (or any non-error reply when
        `expect` is None), False on timeout / error / mismatch.
        """
        if self._serial_module is None:
            log.error("RemoteNavigationManager.initialize() was never called")
            return False
        if not self._ensure_port():
            return False
        with self._lock:
            try:
                self._port.write((cmd + "\n").encode("utf-8"))
                self._port.flush()
                response = (
                    self._port.readline()
                    .decode("utf-8", errors="ignore")
                    .strip()
                )
            except Exception as exc:
                log.error("Serial I/O failed for '%s': %s", cmd, exc)
                self._close_port()
                return False

            if not response:
                log.warning("No response to '%s' within %.2fs",
                            cmd, self.config.response_timeout_sec)
                return False
            if response.startswith("ERR"):
                log.warning("Cmd '%s' got error: %s", cmd, response)
                return False
            if expect is not None and response != expect:
                log.warning("Cmd '%s' got '%s' (expected '%s')",
                            cmd, response, expect)
                return False
            return True
