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

Straight-line preload (opposite jog before crawl) issues low-duty symmetric
reverse SETs (e.g. ``B 3 B 3``). The Pi bridge must use ``kick_and_set`` for
those kicks, not ``warm_reverse_and_set``, which inserts a forward puff before
reverse and would cancel the preload. See ``pi_motor_bridge/motor_bridge.py``.

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
import os
import threading
import time
from dataclasses import dataclass
from typing import Optional

from nina.config.settings import NAV_START_KICK_SEC_MAX

log = logging.getLogger("nina.navigation.remote")

# Re-issue SET during in-place turns so the Pi motor-bridge watchdog
# (default 1.5 s, see NINA_BRIDGE_WATCHDOG_SEC) does not stop the
# wheels mid-turn while the Jetson blocks in time.sleep().
def _remote_turn_keepalive_sec() -> float:
    raw = (os.environ.get("NINA_NAV_REMOTE_TURN_TICK_SEC") or "").strip()
    if raw:
        try:
            return max(0.1, min(1.0, float(raw)))
        except ValueError:
            pass
    return 0.35


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
    connect_timeout_sec    : how long `initialize()` keeps retrying
                              the boot-time PING before giving up.
                              Bridges that just booted may emit a
                              `READY` line first; the retry loop
                              tolerates that.
    reconnect_min_interval_sec : when the link is broken, throttle
                              `_ensure_port` reopen attempts to no
                              more than once per this many seconds.
                              Stops the GUI from busy-looping on a
                              dead Pi at the drive_continuous tick
                              rate.
    default_speed_percent  : exposed so `DriveController` and the
                              autonomy pilot can read a default the
                              same way they do in local mode.
    turn_duration_sec      : in-place spin time for `turn_left`/`turn_right`.
    invert_left_dir        : flip left-wheel forward/backward.
    invert_right_dir       : flip right-wheel forward/backward.
    """
    serial_port: str = "/dev/ttyUSB0"
    baudrate: int = 115200
    # Pi can take >400 ms to finish `warm_reverse_and_set` + kick before
    # it prints OK; 0.4s was marginal and caused missed responses on pivots.
    response_timeout_sec: float = 1.2
    connect_timeout_sec: float = 2.0
    reconnect_min_interval_sec: float = 1.0
    default_speed_percent: int = 8
    turn_duration_sec: float = 2.3
    invert_left_dir: bool = False
    invert_right_dir: bool = False
    # 0 / 0.0 = off. `build_navigation_manager` passes env-driven values
    # from NavigationSettings. Unit tests keep these zero: one SET + OK.
    start_kick_percent: int = 0
    start_kick_sec: float = 0.0
    dir_pwm_gap_sec: float = 0.0
    straight_opposite_nudge_sec: float = 0.0
    straight_opposite_nudge_pct: int = 20
    opposite_zero_settle_sec: float = 0.0
    # Second PWM-zero write timing (mirrors local); 0 in tests skips extra SET.
    pwm_reassert_sec: float = 0.0
    # Matches local `NavigationConfig.settle_delay_sec` — pause after STOP
    # before a fresh SET in `drive_continuous`.
    settle_delay_sec: float = 0.1


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
        # Reconnect throttle: monotonic timestamp of the last failed
        # `_open_port` attempt. `_ensure_port` won't retry until at
        # least `reconnect_min_interval_sec` has elapsed since this.
        self._last_reconnect_failure_ts: float = 0.0
        # Runtime polarity overrides. None = use the frozen config
        # (which itself was seeded from NINA_NAV_INVERT_LEFT/RIGHT env
        # vars). The Drive screen / DriveController calls
        # set_invert_left/right() to flip a wheel without restarting
        # the kiosk service - the override wins over the config so the
        # change is effective on the next SET command.
        self._invert_left_override: Optional[bool] = None
        self._invert_right_override: Optional[bool] = None
        self._last_l_pwm = 0
        self._last_r_pwm = 0
        self._last_straight_sign: Optional[int] = None
        self._last_was_symmetric_straight = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        """Open the serial port and verify the bridge with a PING.

        Retries the PING for up to `connect_timeout_sec` so a bridge
        that's still emitting its boot `READY` (or hasn't quite come
        up yet) is tolerated. Raises `RuntimeError` on failure so
        `DriveController` falls into simulation mode with a useful
        error string instead of pretending the link is up.
        """
        if self._is_initialized:
            return
        try:
            import serial as serial_module  # noqa: WPS433 (lazy)
        except ImportError as exc:
            raise RuntimeError(
                "pyserial is required for remote navigation mode; "
                "install with `sudo apt install -y python3-serial` "
                "(or `pip install pyserial` in a venv)"
            ) from exc
        self._serial_module = serial_module
        self._open_port()

        # Give the OS / USB stack a beat to settle, then drain anything
        # left in the buffer (e.g. a `READY` from a bridge that just
        # booted, or junk from a previous session).
        time.sleep(0.2)
        try:
            self._port.reset_input_buffer()
        except Exception:
            pass

        deadline = time.monotonic() + max(0.0, self.config.connect_timeout_sec)
        attempt = 0
        while True:
            attempt += 1
            if self._send_command("PING", expect="PONG"):
                log.info(
                    "RemoteNavigationManager connected to %s @ %d (PING attempt %d)",
                    self.config.serial_port,
                    self.config.baudrate,
                    attempt,
                )
                self._reset_pi_motor_bridge_after_connect()
                self._is_initialized = True
                return
            if time.monotonic() >= deadline:
                break
            time.sleep(0.2)

        # PING never came back. Close the port and surface the failure;
        # `DriveController._do_init` will catch this and put the GUI
        # into simulation mode with a meaningful `driver_message`.
        self._close_port()
        raise RuntimeError(
            f"Bridge at {self.config.serial_port} did not reply to PING "
            f"within {self.config.connect_timeout_sec:.1f}s. Is "
            "motor_bridge.py running on the Pi? "
            "(`sudo systemctl status motor-bridge`)"
        )

    def _reset_pi_motor_bridge_after_connect(self) -> None:
        """Tell the Pi to ESTOP then STOP so JYQDs and bridge state are idle-safe.

        Runs once after a successful PING when the Nina app (or any Jetson
        client) opens the link — same effect as a manual emergency reset without
        restarting ``motor_bridge.py``.
        """
        if not self._send_command("ESTOP"):
            log.warning(
                "Post-connect ESTOP did not get OK; Pi bridge may be out of sync"
            )
        time.sleep(0.05)
        if not self._send_command("STOP"):
            log.warning("Post-connect STOP did not get OK")
        self._last_l_pwm = 0
        self._last_r_pwm = 0
        self._last_straight_sign = None
        self._last_was_symmetric_straight = False
        log.info("Pi motor bridge: ESTOP+STOP after Nina connect (hardware sync)")

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
        *,
        right_speed_percent: Optional[int] = None,
    ) -> None:
        """Per-wheel motion that does not auto-stop (held D-pad style)."""
        if left_dir not in (self.DIR_FORWARD, self.DIR_BACKWARD):
            raise ValueError(f"Invalid left_dir '{left_dir}'")
        if right_dir not in (self.DIR_FORWARD, self.DIR_BACKWARD):
            raise ValueError(f"Invalid right_dir '{right_dir}'")
        left_speed = self._resolve_speed(speed_percent)
        if right_speed_percent is None:
            right_speed = left_speed
        else:
            right_speed = self._resolve_speed(right_speed_percent)
        # Mirror local `NavigationManager.drive_continuous`: park PWM,
        # brief settle, then arm the new motion. Without this, remote
        # mode only issued set_wheels() and straight-line nudge never ran
        # on turn → straight or other shape changes while PWM stayed up.
        self.stop()
        time.sleep(max(0.0, float(self.config.settle_delay_sec)))
        self.set_wheels(
            left_dir=left_dir, left_speed=left_speed,
            right_dir=right_dir, right_speed=right_speed,
        )
        if right_speed != left_speed:
            log.info(
                "drive_continuous L=%s R=%s L_spd=%s%% R_spd=%s%%",
                left_dir,
                right_dir,
                left_speed,
                right_speed,
            )
        else:
            log.info(
                "drive_continuous L=%s R=%s speed=%s%%",
                left_dir, right_dir, left_speed,
            )

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
        was_rest = self._last_l_pwm == 0 and self._last_r_pwm == 0
        moving_now = ls > 0 or rs > 0
        straight_crawl = (
            left_dir == right_dir
            and ls == rs
            and ls > 0
        )
        target_sign: Optional[int] = None
        if straight_crawl:
            target_sign = 1 if left_dir == self.DIR_FORWARD else -1

        cfg = self.config
        ns = max(0.0, min(2.0, float(cfg.straight_opposite_nudge_sec)))
        pct = max(0, min(100, int(cfg.straight_opposite_nudge_pct)))
        want_nudge = (
            straight_crawl
            and ns > 0
            and pct > 0
            and (
                was_rest
                or (
                    self._last_straight_sign is not None
                    and target_sign is not None
                    and self._last_straight_sign != target_sign
                )
                or (
                    moving_now
                    and not was_rest
                    and not self._last_was_symmetric_straight
                )
            )
        )

        if want_nudge:
            opp_dir = (
                self.DIR_BACKWARD
                if left_dir == self.DIR_FORWARD
                else self.DIR_FORWARD
            )
            ol = self._dir_letter(self.SIDE_LEFT, opp_dir)
            orr = self._dir_letter(self.SIDE_RIGHT, opp_dir)
            nd = max(3, min(100, (ls * pct + 99) // 100))
            gap_pre = max(0.0, min(0.2, float(cfg.dir_pwm_gap_sec)))
            if gap_pre > 0:
                time.sleep(gap_pre)
            self._send_command(f"SET {ol} {nd} {orr} {nd}")
            time.sleep(ns)
            self._send_command(f"SET {ol} 0 {orr} 0")
            gap_z = float(self.config.pwm_reassert_sec)
            if gap_z > 0:
                gap_z = max(0.002, min(0.1, gap_z))
                time.sleep(gap_z)
                self._send_command(f"SET {ol} 0 {orr} 0")
            zs = max(0.0, min(0.2, float(cfg.opposite_zero_settle_sec)))
            if zs > 0:
                time.sleep(zs)
            gap_mid = max(0.0, min(0.2, float(cfg.dir_pwm_gap_sec)))
            if gap_mid > 0:
                time.sleep(gap_mid)

        kp = max(0, min(100, int(self.config.start_kick_percent)))
        ks = max(0.0, min(NAV_START_KICK_SEC_MAX, float(self.config.start_kick_sec)))

        def _kick_duty(cmd: int) -> int:
            if cmd <= 0 or kp <= 0 or ks <= 0:
                return cmd
            return max(cmd, kp)

        kls = _kick_duty(ls)
        krs = _kick_duty(rs)
        need_kick = was_rest and moving_now and (kls > ls or krs > rs)
        if need_kick:
            self._send_command(f"SET {l_letter} {kls} {r_letter} {krs}")
            time.sleep(ks)
        self._send_command(f"SET {l_letter} {ls} {r_letter} {rs}")
        if ls == 0 and rs == 0:
            gap_z = float(self.config.pwm_reassert_sec)
            if gap_z > 0:
                gap_z = max(0.002, min(0.1, gap_z))
                time.sleep(gap_z)
                self._send_command(f"SET {l_letter} 0 {r_letter} 0")
        self._last_l_pwm = ls
        self._last_r_pwm = rs
        if ls == 0 and rs == 0:
            self._last_straight_sign = None
        elif straight_crawl and target_sign is not None:
            self._last_straight_sign = target_sign
        else:
            self._last_straight_sign = None
        self._last_was_symmetric_straight = (
            ls > 0
            and rs > 0
            and left_dir == right_dir
            and ls == rs
        )

    def stop(self) -> None:
        """Soft stop: PWM=0 on both wheels, EL stays HIGH (chip armed)."""
        self._send_command("STOP")
        self._last_l_pwm = 0
        self._last_r_pwm = 0
        self._last_straight_sign = None
        self._last_was_symmetric_straight = False
        log.info("stop (PWM=0, EL=HIGH)")

    def emergency_stop(self) -> None:
        """Hard stop: PWM=0 + EL LOW on both wheels (chip disabled, no torque)."""
        log.warning("EMERGENCY STOP requested")
        self._send_command("ESTOP")
        self._last_l_pwm = 0
        self._last_r_pwm = 0
        self._last_straight_sign = None
        self._last_was_symmetric_straight = False

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
        d = max(0.0, float(duration if duration is not None else self.config.turn_duration_sec))
        # Mirror local NavigationManager: park, settle, then spin. Without
        # stop+settle, odd DIR/PWM sequencing can leave one wheel idle on
        # some bridges.
        self.stop()
        time.sleep(max(0.0, float(self.config.settle_delay_sec)))
        self.set_wheels(
            left_dir=left_dir, left_speed=speed,
            right_dir=right_dir, right_speed=speed,
        )
        tick = _remote_turn_keepalive_sec()
        deadline = time.monotonic() + d
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            sleep_for = min(tick, remaining)
            time.sleep(sleep_for)
            if remaining > tick:
                self.set_wheels(
                    left_dir=left_dir, left_speed=speed,
                    right_dir=right_dir, right_speed=speed,
                )
        self.stop()

    def _dir_letter(self, side: str, direction: str) -> str:
        forward = direction == self.DIR_FORWARD
        if side == self.SIDE_LEFT and self._effective_invert_left():
            forward = not forward
        elif side == self.SIDE_RIGHT and self._effective_invert_right():
            forward = not forward
        return "F" if forward else "B"

    def _effective_invert_left(self) -> bool:
        """Runtime override wins over the frozen config."""
        if self._invert_left_override is not None:
            return self._invert_left_override
        return bool(self.config.invert_left_dir)

    def _effective_invert_right(self) -> bool:
        if self._invert_right_override is not None:
            return self._invert_right_override
        return bool(self.config.invert_right_dir)

    # ------------------------------------------------------------------
    # Runtime polarity controls (called from the GUI's Drive screen via
    # DriveController.set_invert_left/right). Effective on the very next
    # SET command - no restart, no re-init.
    # ------------------------------------------------------------------

    def set_invert_left(self, on: bool) -> None:
        self._invert_left_override = bool(on)
        log.info("invert_left set to %s (runtime override)", bool(on))

    def set_invert_right(self, on: bool) -> None:
        self._invert_right_override = bool(on)
        log.info("invert_right set to %s (runtime override)", bool(on))

    def get_invert_left(self) -> bool:
        return self._effective_invert_left()

    def get_invert_right(self) -> bool:
        return self._effective_invert_right()

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
        """Lazily reopen the serial port if it dropped, e.g. Pi rebooted.

        Throttled to one attempt per `reconnect_min_interval_sec` so a
        dead Pi can't make the GUI's drive_continuous tick busy-loop.
        Returns True only when the port is open and ready for I/O.
        """
        if self._port is not None and getattr(self._port, "is_open", True):
            return True
        if self._serial_module is None:
            return False
        # Throttle: skip the reopen if we *just* failed and the cooldown
        # hasn't elapsed yet. We deliberately gate on "have we ever
        # failed?" (ts > 0) instead of comparing against the raw
        # timestamp - `time.monotonic()` is process-relative on some
        # platforms and can be small enough early in a run to make the
        # `now - 0.0` math accidentally throttle the very first reopen.
        now = time.monotonic()
        cooldown = max(0.0, self.config.reconnect_min_interval_sec)
        if (
            self._last_reconnect_failure_ts > 0.0
            and (now - self._last_reconnect_failure_ts) < cooldown
        ):
            return False
        try:
            self._open_port()
            time.sleep(0.1)
            try:
                self._port.reset_input_buffer()
            except Exception:
                pass
            self._last_reconnect_failure_ts = 0.0
            log.info("Serial port reopened")
            return True
        except Exception as exc:
            self._last_reconnect_failure_ts = now
            log.warning(
                "Serial reopen failed (next retry in %.1fs): %s",
                cooldown, exc,
            )
            return False

    def _read_response_line(self, deadline: float) -> str:
        """Read one *response* line, ignoring async events.

        The bridge can emit unsolicited `EVT WATCHDOG` / `READY` lines
        at any time. Treat those as logging events and keep reading
        until either we get a non-event line or the per-command
        deadline expires. Without this filter a stray `EVT WATCHDOG`
        between commands desyncs the response stream by exactly one
        line - the next SET would consume the EVT and every command
        after that would read the previous command's reply.
        """
        while time.monotonic() < deadline:
            try:
                raw = self._port.readline()
            except Exception:
                raise
            line = raw.decode("utf-8", errors="ignore").strip()
            if not line:
                # readline() returned empty -> the per-call serial
                # timeout fired. Loop only if we still have budget.
                continue
            if line == "READY":
                log.info("bridge event: READY (Pi bridge (re)started)")
                continue
            if line.startswith("EVT "):
                log.warning("bridge event: %s", line)
                continue
            return line
        return ""

    def _send_command(self, cmd: str, *, expect: Optional[str] = None) -> bool:
        """Send one ASCII line and read one response line.

        Returns True on a clean reply (or any non-error reply when
        `expect` is None), False on timeout / error / mismatch.
        Async `EVT ...` / `READY` lines from the bridge are skipped
        (logged) so they can't desync the response stream.
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
                deadline = time.monotonic() + self.config.response_timeout_sec
                response = self._read_response_line(deadline)
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
