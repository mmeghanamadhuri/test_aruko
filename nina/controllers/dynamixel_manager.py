import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from nina.models.types import HealthReport


HEADER = 0xFF
PING = 0x01
READ_DATA = 0x02
WRITE_DATA = 0x03
SYNC_WRITE = 0x83
BROADCAST_ID = 0xFE

REG_TORQUE_ENABLE = (24, 1)
REG_GOAL_POSITION = (30, 2)
REG_MOVING_SPEED = (32, 2)
REG_PRESENT_POS = (36, 2)

POS_MIN = 0
POS_MAX = 4095

# MX-28/MX-106 in joint mode: 1 unit of Moving Speed = 0.114 RPM
# = 0.684 deg/s = ~7.78 ticks/s (4096 ticks/rev). Used to convert a
# desired ticks-per-second into a register value.
TICKS_PER_SPEED_UNIT = 7.78
# Floor on smoothed speed so micro-deltas don't reduce to a literal crawl
# (and so we never write speed=0, which means "max speed" in joint mode).
MIN_SMOOTH_SPEED = 8
MAX_SMOOTH_SPEED = 1023

# Bus timing. The single biggest reliability factor on USB-FTDI Dynamixel chains
# is the FTDI latency_timer (default 16ms). With the default timer, motor
# responses can sit in the FTDI internal FIFO for up to 16ms before being
# forwarded to the OS - long enough to land in the next ping's response window
# and confuse parsing. We set latency_timer=1ms in initialize_bus() and use an
# active drain (DRAIN_QUIET_SEC of silence) instead of a fixed sleep.
# Settle time covers the FTDI USB enumeration + UART hand-shake at port-open;
# 0.3s is enough on every Jetson Nano + U2D2 combo we've measured. The
# previous 1.5s was paying for the lack of an active drain, which we now do
# in _robust_clear() right after.
BUS_SETTLE_SEC = 0.3
FTDI_LATENCY_MS = 1
# Drain waits long enough that any in-flight FTDI byte arrives before the
# next packet is sent. With latency_timer=1ms the FTDI flushes promptly and
# 3ms of quiet is plenty. With the default 16ms latency a late response can
# arrive ~16ms after our send, so we need a much longer quiet window.
DRAIN_QUIET_SEC_FAST = 0.003
DRAIN_QUIET_SEC_SLOW = 0.020
DRAIN_MAX_SEC_FAST = 0.030
DRAIN_MAX_SEC_SLOW = 0.080
INTER_PACKET_SEC = 0.004
INTER_PING_SEC = 0.004
# Empirically the user's MX-28/MX-106 chain misses ping at 30 ms timeout
# but hits read_reg (4 retries x 40 ms = 160 ms per motor) reliably, so
# 3 passes x 60 ms gives the same 180 ms per-motor budget as read_reg
# while being early-exit on a clean bus.
HEALTH_CHECK_PASSES = 3
HEALTH_PASS_REST_SEC = 0.03
# Public ping() retries are for ad-hoc use. run_health_check() bypasses this
# loop and uses _ping_once() because its own multi-pass loop is the retry,
# avoiding a 5*3 = 15-retry cascade that turned a noisy bus into a 30s hang.
PING_RETRIES = 3
READ_RETRIES = 4
WRITE_RETRIES = 4
# A Dynamixel status packet at 1Mbaud arrives in <2 ms. With the FTDI
# latency_timer at 1 ms the round-trip is well under 10 ms, but USB stack
# jitter on the Jetson can push it to 30-50 ms when other USB traffic is
# active. 60 ms keeps the ping budget roughly equal to read_reg
# (4 attempts x 40 ms) so we don't lie about which motors are alive.
PING_TIMEOUT_SEC = 0.060
READ_TIMEOUT_SEC = 0.040
WRITE_STATUS_TIMEOUT_SEC = 0.040
TORQUE_VERIFY_PASSES = 3


class DynamixelManager:
    """
    High-level arm motor manager for Nina's Dynamixel Protocol 1.0 bus.
    """

    def __init__(self, serial_port: str, baudrate: int, expected_motor_ids: List[int]) -> None:
        self.serial_port = serial_port
        self.baudrate = baudrate
        self.expected_motor_ids = expected_motor_ids
        self._serial: Optional[Any] = None
        self._is_initialized = False
        self._last_speed: Optional[int] = None
        self._last_positions: Dict[int, int] = {}
        self._last_goal: Dict[int, int] = {}
        self._capture_miss_count: int = 0
        self._capture_total_reads: int = 0
        self._latency_timer_ms: Optional[int] = None
        self._drain_quiet_sec: float = DRAIN_QUIET_SEC_FAST
        self._drain_max_sec: float = DRAIN_MAX_SEC_FAST
        # Some FTDI Dynamixel cables (any TTL chain that doesn't have
        # hardware TX-disable like the Robotis U2D2) echo every byte we
        # transmit back onto the receive line. Without compensation
        # those echo bytes get parsed as fake "successful" status
        # responses and ping returns True for every motor, present or
        # not. Set on first bus init by _detect_echo().
        self._echo_present: bool = False

    def initialize_bus(self) -> None:
        if self._serial and getattr(self._serial, "is_open", False):
            return

        t0 = time.time()
        serial = self._load_serial_module()
        self._serial = serial.Serial(port=self.serial_port, baudrate=self.baudrate, timeout=0.1)
        try:
            latency_ms = int(os.environ.get("NINA_DXL_LATENCY_MS", FTDI_LATENCY_MS))
        except ValueError:
            latency_ms = FTDI_LATENCY_MS
        latency_set = self._set_ftdi_latency_timer(latency_ms)
        if latency_set:
            self._latency_timer_ms = latency_ms
        else:
            self._latency_timer_ms = self.read_ftdi_latency_timer()
        self._tune_for_latency()
        self._announce_bus_health(target_latency_ms=latency_ms, latency_set=latency_set)
        self._serial.reset_input_buffer()
        self._serial.reset_output_buffer()
        try:
            settle = float(os.environ.get("NINA_DXL_SETTLE_SEC", BUS_SETTLE_SEC))
        except ValueError:
            settle = BUS_SETTLE_SEC
        time.sleep(max(0.0, settle))
        self._robust_clear()
        self._detect_echo()
        self._is_initialized = True
        self._timing("initialize_bus", t0)

    def _detect_echo(self) -> None:
        """Send a few short pings to a bogus motor ID and watch for
        bytes coming back. A non-zero echo rate means our cable is
        echoing every transmission back onto the receive line; we then
        flip self._echo_present so every send drains its own echo
        before the parser sees it."""
        bogus_id = 0xFD
        echoes = 0
        trials = 4
        try:
            for _ in range(trials):
                self._serial.reset_input_buffer()
                pkt = self._build(bogus_id, PING)
                self._serial.write(pkt)
                self._serial.flush()
                time.sleep(0.005)
                if self._serial.in_waiting:
                    self._serial.read(self._serial.in_waiting)
                    echoes += 1
                time.sleep(0.005)
        except (OSError, AttributeError):
            self._echo_present = False
            return
        self._echo_present = echoes >= max(1, trials // 2)
        if self._echo_present:
            print(
                "[bus] FTDI cable is echoing transmissions; enabling "
                "post-send echo drain so ping/read results reflect real "
                "motor responses (not our own echo)."
            )

    @staticmethod
    def _timing_enabled() -> bool:
        val = os.environ.get("NINA_TIMING", "1").strip().lower()
        return val not in ("0", "false", "no", "off", "")

    def _timing(self, label: str, t0: float, extra: str = "") -> None:
        if not self._timing_enabled():
            return
        ms = (time.time() - t0) * 1000.0
        suffix = f" ({extra})" if extra else ""
        print(f"[timing] {label}: {ms:.0f} ms{suffix}")

    def _tune_for_latency(self) -> None:
        """Pick drain timings that match the FTDI latency_timer. With the
        kernel default of 16ms a late response can arrive 16ms after our
        send, so we wait longer for the line to go quiet before sending
        the next packet."""
        actual = self._latency_timer_ms
        if actual is None or actual > 4:
            self._drain_quiet_sec = DRAIN_QUIET_SEC_SLOW
            self._drain_max_sec = DRAIN_MAX_SEC_SLOW
        else:
            self._drain_quiet_sec = DRAIN_QUIET_SEC_FAST
            self._drain_max_sec = DRAIN_MAX_SEC_FAST

    def _announce_bus_health(self, target_latency_ms: int, latency_set: bool) -> None:
        """Print a single, actionable line so the user always knows whether
        the FTDI latency_timer is at the recommended value. The check is
        on the *actual* value, not on whether we wrote it - the udev rule
        sets it for us at plug time, so a successful state can show up
        even when our own write returned permission-denied."""
        actual = self._latency_timer_ms
        if actual is not None and actual == int(target_latency_ms):
            via = "set by us" if latency_set else "set by udev rule or previous run"
            print(f"[bus] FTDI latency_timer = {actual} ms ({via}).")
            return
        if actual is None:
            print(
                "[bus] WARNING: could not read or set FTDI latency_timer. "
                "Bus reads will be unreliable. Run with sudo, or install a "
                "udev rule (see scripts/install-ftdi-udev.sh) so the timer "
                "can be set without root."
            )
        else:
            print(
                f"[bus] WARNING: FTDI latency_timer = {actual} ms "
                f"(target {target_latency_ms} ms). Without root we cannot "
                "lower it. Intermittent missing motors are expected. Either "
                "re-run with sudo, or run 'sudo bash "
                "scripts/install-ftdi-udev.sh' once to fix this permanently."
            )

    def _set_ftdi_latency_timer(self, value_ms: int) -> bool:
        """Set the FTDI USB-serial chip's latency_timer register.

        Default is 16ms which is too slow for Dynamixel - response bytes can
        sit in the FTDI FIFO long enough to leak into the next request's
        response window. 1ms is the Robotis-recommended value.

        Tries multiple sysfs paths because different kernels expose the
        attribute at different locations. Returns True if the timer was set
        (and was actually accepted by the kernel), False otherwise.
        """
        port = getattr(self._serial, "port", None) or self.serial_port
        if not port or not port.startswith("/dev/"):
            return False
        device_name = port.rsplit("/", 1)[-1]
        candidates = [
            f"/sys/bus/usb-serial/devices/{device_name}/latency_timer",
            f"/sys/class/tty/{device_name}/device/latency_timer",
        ]
        for path in candidates:
            try:
                with open(path, "w") as fh:
                    fh.write(str(int(value_ms)))
                try:
                    with open(path, "r") as fh:
                        actual = int(fh.read().strip())
                    if actual == int(value_ms):
                        return True
                except (FileNotFoundError, PermissionError, OSError, ValueError):
                    return True
            except (FileNotFoundError, PermissionError, OSError):
                continue
        return False

    def read_ftdi_latency_timer(self) -> Optional[int]:
        """Read the current FTDI latency_timer value from sysfs without
        modifying it. Returns the integer ms value, or None if no sysfs
        path could be read."""
        port = getattr(self._serial, "port", None) or self.serial_port
        if not port or not port.startswith("/dev/"):
            return None
        device_name = port.rsplit("/", 1)[-1]
        candidates = [
            f"/sys/bus/usb-serial/devices/{device_name}/latency_timer",
            f"/sys/class/tty/{device_name}/device/latency_timer",
        ]
        for path in candidates:
            try:
                with open(path, "r") as fh:
                    return int(fh.read().strip())
            except (FileNotFoundError, PermissionError, OSError, ValueError):
                continue
        return None

    @property
    def latency_timer_ms(self) -> Optional[int]:
        return getattr(self, "_latency_timer_ms", None)

    def close(self) -> None:
        if self._serial and getattr(self._serial, "is_open", False):
            self._serial.close()
        self._is_initialized = False

    def run_health_check(self, passes: int = HEALTH_CHECK_PASSES) -> HealthReport:
        """Multi-pass ping of every expected motor. Each pass uses a SINGLE
        ping attempt per motor (not the public ping()'s built-in retry
        loop) so the multi-pass loop is the only retry layer - this is
        what kept a noisy bus from turning startup into a 30 second hang.
        """
        self._require_initialized()
        t0 = time.time()
        reachable: set = set()
        passes = max(1, int(passes))
        passes_used = 0
        for pass_idx in range(passes):
            passes_used = pass_idx + 1
            for sid in self.expected_motor_ids:
                if sid in reachable:
                    continue
                if self._ping_once(sid):
                    reachable.add(sid)
                time.sleep(INTER_PING_SEC)
            if len(reachable) == len(self.expected_motor_ids):
                break
            if pass_idx < passes - 1:
                self._robust_clear()
                time.sleep(HEALTH_PASS_REST_SEC)
        missing = [sid for sid in self.expected_motor_ids if sid not in reachable]
        connected = len(missing) == 0
        detail = "All expected motors reachable." if connected else f"Missing motor IDs: {missing}"
        self._timing(
            "run_health_check",
            t0,
            f"passes={passes_used}, found={len(reachable)}/{len(self.expected_motor_ids)}",
        )
        return HealthReport(
            connected=connected,
            detected_motors=len(reachable),
            expected_motors=len(self.expected_motor_ids),
            detail=detail,
        )

    def _ping_once(self, sid: int) -> bool:
        """Single-attempt ping with no internal retry. Used by
        run_health_check, which provides its own retry via multi-pass."""
        pkt = self._build(sid, PING)
        self._robust_clear()
        self._send_packet(pkt)
        return self._recv(sid, timeout=PING_TIMEOUT_SEC) is not None

    def scan_baudrates(self,
                       baudrates: Optional[List[int]] = None,
                       samples_per_baud: int = 5) -> Dict[int, Dict[str, Any]]:
        """Try a list of common Dynamixel baudrates and ping every
        expected motor at each one. Returns per-baudrate stats so the
        caller can identify which baud the motors are actually using.

        This is the diagnostic to run when bus-diag shows uniformly
        terrible reliability across every motor: the symptom is what
        you get when the host is talking at the wrong baud and just
        catches occasional random byte-pattern matches in the noise.
        """
        self._require_initialized()
        if baudrates is None:
            baudrates = [
                9600, 57600, 115200, 200000, 222222, 250000,
                400000, 500000, 1_000_000, 2_000_000, 3_000_000,
            ]
        original_baud = self._serial.baudrate
        results: Dict[int, Dict[str, Any]] = {}
        try:
            for baud in baudrates:
                try:
                    self._serial.baudrate = baud
                except (ValueError, OSError):
                    results[baud] = {"error": "unsupported by driver", "found": []}
                    continue
                time.sleep(0.05)
                self._robust_clear()
                found: List[int] = []
                for sid in self.expected_motor_ids:
                    hits = 0
                    for _ in range(max(1, int(samples_per_baud))):
                        if self._ping_once(sid):
                            hits += 1
                    if hits >= max(1, samples_per_baud // 2):
                        found.append(sid)
                results[baud] = {
                    "found": found,
                    "found_count": len(found),
                }
        finally:
            try:
                self._serial.baudrate = original_baud
            except (ValueError, OSError):
                pass
            self._robust_clear()
        return results

    def echo_check(self, samples: int = 20) -> Dict[str, Any]:
        """Send a ping to a bogus motor ID (99) and listen. If anything
        comes back, the FTDI cable is echoing our own transmissions
        onto the receive line - that means the echo can be parsed as a
        fake 'success' response and confuse every ping. Robotis U2D2
        suppresses echo in hardware; some generic FTDI cables do not.
        """
        self._require_initialized()
        bogus_id = 99
        echoes = 0
        for _ in range(samples):
            self._robust_clear()
            pkt = self._build(bogus_id, PING)
            self._serial.write(pkt)
            self._serial.flush()
            time.sleep(0.005)
            if self._serial.in_waiting:
                _ = self._serial.read(self._serial.in_waiting)
                echoes += 1
            time.sleep(0.005)
        return {
            "samples": samples,
            "echoes_seen": echoes,
            "echo_rate": echoes / samples,
        }

    def bus_reliability_report(self, samples: int = 20) -> Dict[int, Dict[str, Any]]:
        """Ping each expected motor `samples` times and report success
        rate, average response time, and longest streak of consecutive
        failures. Useful for pinpointing physical bus problems: one
        motor at 5/20 with a long failure streak is almost certainly a
        loose connector to that motor; a smooth gradient where higher
        IDs get worse points to a missing termination resistor at the
        end of the chain or to power-supply sag.
        """
        self._require_initialized()
        report: Dict[int, Dict[str, Any]] = {}
        for sid in self.expected_motor_ids:
            successes = 0
            response_times: List[float] = []
            longest_streak = 0
            current_streak = 0
            for _ in range(samples):
                t0 = time.time()
                ok = self._ping_once(sid)
                elapsed = time.time() - t0
                if ok:
                    successes += 1
                    response_times.append(elapsed)
                    current_streak = 0
                else:
                    current_streak += 1
                    longest_streak = max(longest_streak, current_streak)
                time.sleep(INTER_PING_SEC)
            avg_resp_ms = (
                1000.0 * sum(response_times) / len(response_times)
                if response_times else None
            )
            report[sid] = {
                "samples": samples,
                "successes": successes,
                "success_rate": successes / samples,
                "avg_response_ms": avg_resp_ms,
                "longest_failure_streak": longest_streak,
            }
        return report

    def set_torque_all(self, enable: bool, verify: bool = True) -> List[int]:
        """Enable or disable torque on every expected motor.

        Fast path: a single SYNC_WRITE pushes the torque byte to all 11
        motors in one ~30-byte packet (~0.3 ms on the wire). If verify
        is True we then do ONE single-attempt read per motor (~30 ms x
        11 = ~350 ms) and only fall back to per-motor write_reg+read
        retries for whatever didn't latch. This avoids the previous
        nested 3 verify-passes x (11 writes + 11 reads with 4 internal
        retries) cascade that turned a single stuck motor into 6+ s of
        bus time.

        Returns the list of motor IDs that could NOT be set after all
        retries. This matters most for release-before-recording: a single
        dropped write would otherwise leave one motor rigid for the
        whole session with no warning.
        """
        self._require_initialized()
        t0 = time.time()
        target = 1 if enable else 0
        ids = list(self.expected_motor_ids)
        self._sync_write_byte(REG_TORQUE_ENABLE[0], {sid: target for sid in ids})
        time.sleep(0.005)
        if not verify:
            self._timing("set_torque_all", t0, f"{'on' if enable else 'off'}, no-verify")
            return []

        failed: List[int] = []
        for sid in ids:
            actual = self._read_reg_once(sid, *REG_TORQUE_ENABLE)
            if actual is None or actual != target:
                failed.append(sid)
        if not failed:
            self._timing("set_torque_all", t0, f"{'on' if enable else 'off'}, fast-path")
            return []

        # Per-motor retries only for the small subset that missed.
        remaining = failed
        for _ in range(max(1, TORQUE_VERIFY_PASSES - 1)):
            for sid in remaining:
                self.write_reg(sid, *REG_TORQUE_ENABLE, target)
            still_wrong: List[int] = []
            for sid in remaining:
                actual = self._read_reg_once(sid, *REG_TORQUE_ENABLE)
                if actual is None or actual != target:
                    still_wrong.append(sid)
            if not still_wrong:
                self._timing("set_torque_all", t0, f"{'on' if enable else 'off'}, retry-path")
                return []
            remaining = still_wrong
            time.sleep(0.02)
        self._timing(
            "set_torque_all", t0,
            f"{'on' if enable else 'off'}, FAILED on {remaining}",
        )
        return remaining

    def _sync_write_byte(self, addr: int, values: Dict[int, int]) -> None:
        """Write a single byte at `addr` to many motors in one
        Protocol-1.0 SYNC_WRITE packet. Used for torque enable, which
        otherwise costs 11 individual round-trips.
        """
        if not values:
            return
        params: List[int] = [addr, 1]
        for sid, val in values.items():
            params.extend([sid & 0xFF, int(val) & 0xFF])
        pkt = self._build(BROADCAST_ID, SYNC_WRITE, params)
        self._serial.reset_input_buffer()
        self._send_packet(pkt)

    def execute_action_file(self, action_path: Path,
                            speed_scale: float = 1.0,
                            smooth: bool = True) -> None:
        """Play an action file frame-by-frame.

        smooth=True (default): per-motor moving-speed is computed from the
        delta between waypoints and the frame duration, so each motor
        sweeps through the entire frame interval at constant velocity
        instead of snapping to its goal in 5-10 ms and sitting idle for
        the rest of the frame. All goal+speed writes for a frame go out in
        a single sync-write packet, and frame timing is paced on the
        wall-clock so bus-write time is absorbed into the frame interval
        rather than added to it.

        smooth=False reverts to the legacy per-motor write loop and the
        recorded `speed` value (useful for debugging).
        """
        self._require_initialized()
        t_total = time.time()
        action = json.loads(action_path.read_text(encoding="utf-8"))
        frames = action.get("frames", [])
        self._last_speed = None
        self._last_goal.clear()
        if smooth:
            t_seed = time.time()
            self._seed_last_goal_from_present()
            self._timing(
                "seed_present_positions", t_seed,
                f"{len(self._last_goal)}/{len(self.expected_motor_ids)} motors read",
            )

        scale = max(0.05, float(speed_scale))
        next_deadline = time.time()
        first_frame_sent_at: Optional[float] = None
        for frame in frames:
            if smooth:
                next_deadline = self._execute_frame_smooth(
                    frame, scale=scale, deadline=next_deadline)
            else:
                self._execute_frame(frame, speed_scale=speed_scale)
            if first_frame_sent_at is None:
                first_frame_sent_at = time.time()
                self._timing(
                    "first_frame_sent", t_total,
                    f"{len(frames)} frames queued",
                )
        self._timing("execute_action_file_total", t_total, f"{len(frames)} frames")

    def _seed_last_goal_from_present(self) -> None:
        """Read every motor's current position so the first frame's
        smooth-speed math has a real starting point. Best-effort with a
        single attempt per motor - if a read drops, the next frame's
        smoothing falls back to the recorded goal as the prior, which is
        close enough that the user won't perceive it (and the prepended
        neutral frame in every recording further covers this case)."""
        for sid in self.expected_motor_ids:
            value = self._read_reg_once(sid, *REG_PRESENT_POS)
            if value is not None:
                self._last_goal[sid] = self._clamp_pos(value)

    def _read_reg_once(self, sid: int, addr: int, size: int) -> Optional[int]:
        """Single-attempt register read, used where best-effort latency
        is more important than guaranteed delivery (seeding playback,
        capture priming)."""
        pkt = self._build(sid, READ_DATA, [addr, size])
        self._robust_clear()
        self._send_packet(pkt)
        resp = self._recv(sid, timeout=READ_TIMEOUT_SEC)
        if resp is not None and len(resp[1]) >= size:
            data = resp[1]
            return data[0] if size == 1 else (data[0] | (data[1] << 8))
        return None

    def _execute_frame_smooth(self, frame: Dict[str, Any],
                              scale: float, deadline: float) -> float:
        delay = float(frame.get("delay", 0.0)) / scale
        duration = float(frame.get("duration", 1.0)) / scale
        servos = frame.get("servos", {}) or {}

        if delay > 0:
            time.sleep(delay)

        targets: Dict[int, int] = {}
        for raw_sid, spec in servos.items():
            try:
                sid = int(raw_sid)
            except (TypeError, ValueError):
                continue
            if sid not in self.expected_motor_ids:
                continue
            if not isinstance(spec, dict):
                continue
            if spec.get("type", "absolute") != "absolute":
                raise ValueError(f"Unsupported servo command type for S{sid}: {spec.get('type')}")
            value = spec.get("value")
            if value is None:
                continue
            targets[sid] = self._clamp_pos(value)

        if not targets:
            time.sleep(max(0.0, duration))
            return time.time() + duration

        recorded_speed = max(
            MIN_SMOOTH_SPEED,
            min(MAX_SMOOTH_SPEED, int(frame.get("speed", 800))),
        )
        goal_speed: Dict[int, Tuple[int, int]] = {}
        for sid, goal in targets.items():
            if sid in self._last_goal:
                prev = self._last_goal[sid]
                delta = abs(goal - prev)
                speed = self._compute_smooth_speed(delta, duration)
            else:
                # No known starting position (seed read failed for this
                # motor on a noisy bus). delta=0 would smooth this to
                # MIN_SMOOTH_SPEED (~1 RPM) and the motor would visibly
                # not reach the goal during the frame. Fall back to the
                # recorded per-frame speed so the motor actually moves;
                # subsequent frames pick up smoothing automatically.
                speed = recorded_speed
            goal_speed[sid] = (goal, speed)
            self._last_goal[sid] = goal

        self.sync_write_goal_speed(goal_speed)

        deadline = max(deadline, time.time()) + duration
        sleep_for = deadline - time.time()
        if sleep_for > 0:
            time.sleep(sleep_for)
        return deadline

    @staticmethod
    def _compute_smooth_speed(delta: int, duration: float) -> int:
        if duration <= 0:
            return MAX_SMOOTH_SPEED
        ticks_per_sec = abs(delta) / duration
        speed = int(round(ticks_per_sec / TICKS_PER_SPEED_UNIT))
        return max(MIN_SMOOTH_SPEED, min(MAX_SMOOTH_SPEED, speed))

    def sync_write_goal_speed(self, values: Dict[int, Tuple[int, int]]) -> None:
        """Broadcast goal_position + moving_speed for many motors in one
        Protocol-1.0 SYNC_WRITE packet. Writes 4 bytes starting at
        REG_GOAL_POSITION (addr 30): goal_lo, goal_hi, speed_lo, speed_hi.

        SYNC_WRITE has no status response, so this is fire-and-forget.
        Bus dead time drops from ~80 ms (11 individual writes) to ~3 ms.
        """
        if not values:
            return
        addr = REG_GOAL_POSITION[0]
        bytes_per_motor = 4
        params: List[int] = [addr, bytes_per_motor]
        for sid, (goal, speed) in values.items():
            goal = max(POS_MIN, min(POS_MAX, int(goal)))
            speed = max(0, min(MAX_SMOOTH_SPEED, int(speed)))
            params.extend([
                sid & 0xFF,
                goal & 0xFF, (goal >> 8) & 0xFF,
                speed & 0xFF, (speed >> 8) & 0xFF,
            ])
        pkt = self._build(BROADCAST_ID, SYNC_WRITE, params)
        self._serial.reset_input_buffer()
        self._send_packet(pkt)

    def analyze_action_file(self, action_path: Path) -> Dict[str, Any]:
        """Inspect an action JSON and report which expected motors are
        actually addressed across all frames. A motor that never appears
        in any frame will never receive a goal position on playback.
        """
        action = json.loads(action_path.read_text(encoding="utf-8"))
        frames = action.get("frames", [])
        covered: set = set()
        per_frame_counts: List[int] = []
        for frame in frames:
            servos = frame.get("servos", {}) or {}
            present_in_frame = 0
            for raw_sid in servos.keys():
                try:
                    sid = int(raw_sid)
                except (TypeError, ValueError):
                    continue
                if sid in self.expected_motor_ids:
                    covered.add(sid)
                    present_in_frame += 1
            per_frame_counts.append(present_in_frame)
        missing = [sid for sid in self.expected_motor_ids if sid not in covered]
        avg_motors_per_frame = (
            sum(per_frame_counts) / len(per_frame_counts) if per_frame_counts else 0.0
        )
        return {
            "frame_count": len(frames),
            "motors_covered": sorted(covered),
            "motors_missing": missing,
            "avg_motors_per_frame": avg_motors_per_frame,
            "min_motors_per_frame": min(per_frame_counts) if per_frame_counts else 0,
        }

    def capture_frame(self, duration: float, speed: int = 800, delay: float = 0.0) -> Dict[str, Any]:
        """Sample all motor positions into one frame.

        Uses single-attempt reads (_read_reg_once) instead of the 4-retry
        read_reg, because every captured frame already has a safety net:
        if a motor misses on this pass, we forward-fill from the last
        successfully read value. Retrying 4 times per motor would
        balloon per-frame time to 1-2 s on a noisy bus and silently
        slow recording to a crawl - the forward-fill keeps the data
        clean while the capture loop stays at its target rate.
        """
        self._require_initialized()
        servos: Dict[str, Any] = {}
        for sid in self.expected_motor_ids:
            self._capture_total_reads += 1
            present = self._read_reg_once(sid, *REG_PRESENT_POS)
            if present is not None:
                value = self._clamp_pos(present)
                self._last_positions[sid] = value
            elif sid in self._last_positions:
                self._capture_miss_count += 1
                value = self._last_positions[sid]
            else:
                self._capture_miss_count += 1
                continue
            servos[str(sid)] = {"type": "absolute", "value": value}
        return {
            "delay": delay,
            "duration": duration,
            "speed": speed,
            "servos": servos,
        }

    def prime_capture(self, max_attempts: int = 3) -> Dict[int, Optional[int]]:
        """Read every expected motor before recording starts so the
        last-known cache is seeded. Returns the seed values per motor.

        Uses _read_reg_once (single attempt) wrapped in our own
        max_attempts loop with a short backoff. read_reg's internal
        4-retry would multiply with this loop and produce 16 attempts
        per motor on a missing-motor case (~9 s of priming for an
        11-motor chain on a noisy bus). Single-attempt reads with a
        short outer retry keeps priming under ~1 s while still giving
        each motor a real chance to respond.
        """
        self._require_initialized()
        self._last_positions.clear()
        self._capture_miss_count = 0
        self._capture_total_reads = 0
        seed: Dict[int, Optional[int]] = {sid: None for sid in self.expected_motor_ids}
        for sid in self.expected_motor_ids:
            for _ in range(max(1, max_attempts)):
                value = self._read_reg_once(sid, *REG_PRESENT_POS)
                if value is not None:
                    seed[sid] = self._clamp_pos(value)
                    self._last_positions[sid] = seed[sid]
                    break
                time.sleep(0.01)
        return seed

    def capture_stats(self) -> Dict[str, int]:
        return {
            "total_reads": self._capture_total_reads,
            "missed_reads": self._capture_miss_count,
            "tracked_motors": len(self._last_positions),
        }

    def ping(self, sid: int) -> bool:
        pkt = self._build(sid, PING)
        for attempt in range(PING_RETRIES):
            self._robust_clear()
            self._send_packet(pkt)
            if self._recv(sid, timeout=PING_TIMEOUT_SEC) is not None:
                return True
            if attempt < PING_RETRIES - 1:
                time.sleep(0.015)
        return False

    def read_reg(self, sid: int, addr: int, size: int) -> Optional[int]:
        pkt = self._build(sid, READ_DATA, [addr, size])
        for attempt in range(READ_RETRIES):
            self._robust_clear()
            self._send_packet(pkt)
            resp = self._recv(sid, timeout=READ_TIMEOUT_SEC)
            if resp is not None and len(resp[1]) >= size:
                data = resp[1]
                return data[0] if size == 1 else (data[0] | (data[1] << 8))
            if attempt < READ_RETRIES - 1:
                time.sleep(0.01)
        return None

    def write_reg(self, sid: int, addr: int, size: int, value: int,
                  retries: int = WRITE_RETRIES) -> bool:
        """Write to a Dynamixel register and verify the status response.

        Returns True only if the motor actually replied with a status
        packet (which is its acknowledgement of the write). Retries up to
        `retries` times on no-response - this is critical for the torque
        register, where a silently dropped packet leaves the motor in the
        wrong state with no warning.
        """
        value = int(value) & (0xFF if size == 1 else 0xFFFF)
        params = [addr, value & 0xFF] if size == 1 else [addr, value & 0xFF, (value >> 8) & 0xFF]
        pkt = self._build(sid, WRITE_DATA, params)
        attempts = max(1, int(retries))
        for attempt in range(attempts):
            self._robust_clear()
            self._send_packet(pkt)
            if self._recv(sid, timeout=WRITE_STATUS_TIMEOUT_SEC) is not None:
                return True
            if attempt < attempts - 1:
                time.sleep(0.01)
        return False

    def _execute_frame(self, frame: Dict[str, Any], speed_scale: float = 1.0) -> None:
        scale = max(0.05, float(speed_scale))
        delay = float(frame.get("delay", 0.0)) / scale
        duration = float(frame.get("duration", 1.0)) / scale
        raw_speed = int(frame.get("speed", 800))
        speed = max(1, min(1023, int(round(raw_speed * scale))))
        servos = frame.get("servos", {})

        if delay > 0:
            time.sleep(delay)

        if speed != self._last_speed:
            for sid in self.expected_motor_ids:
                self.write_reg(sid, *REG_MOVING_SPEED, speed)
            self._last_speed = speed

        for raw_sid, spec in servos.items():
            sid = int(raw_sid)
            if sid not in self.expected_motor_ids:
                continue
            if spec.get("type", "absolute") != "absolute":
                raise ValueError(f"Unsupported servo command type for S{sid}: {spec.get('type')}")
            value = spec.get("value")
            if value is None:
                continue
            self.write_reg(sid, *REG_GOAL_POSITION, self._clamp_pos(value))

        if duration > 0:
            time.sleep(duration)

    def _recv(self, servo_id: int, timeout: float = 0.1):
        deadline = time.time() + timeout
        buf = bytearray()
        while time.time() < deadline:
            if self._serial.in_waiting:
                buf.extend(self._serial.read(self._serial.in_waiting))
                if len(buf) >= 6:
                    for i in range(len(buf) - 1):
                        if buf[i] == HEADER and buf[i + 1] == HEADER:
                            chunk = buf[i:]
                            if len(chunk) >= 4:
                                length = chunk[3]
                                if len(chunk) >= 4 + length:
                                    full = chunk[: 4 + length]
                                    if self._checksum(full[:-1]) == full[-1] and full[2] == servo_id:
                                        return full[4], list(full[5:-1])
            time.sleep(0.001)
        return None

    def _send_packet(self, pkt: bytes) -> None:
        """Write a packet to the bus and consume our own echo bytes if
        the cable echoes (auto-detected at init). On a non-echoing
        cable (Robotis U2D2 etc.) this is a near-no-op since
        _consume_echo finds no bytes and returns immediately.

        Replaces the inline write+flush+sleep pattern used by every
        sender (ping, read_reg, write_reg, sync_write_*) so the echo
        compensation only has to live in one place.
        """
        self._serial.write(pkt)
        self._serial.flush()
        if self._echo_present:
            self._consume_echo(len(pkt))
        time.sleep(max(INTER_PACKET_SEC, len(pkt) * 10.0 / self.baudrate))

    def _consume_echo(self, n_bytes: int) -> int:
        """Read up to n_bytes from the input buffer (which on an
        echoing cable will be exactly the bytes we just transmitted).
        Bounded by a 30 ms deadline so a partially-corrupted echo can't
        hang us. Returns count actually drained."""
        drained = 0
        deadline = time.time() + 0.030
        while drained < n_bytes and time.time() < deadline:
            avail = self._serial.in_waiting
            if avail > 0:
                take = min(avail, n_bytes - drained)
                chunk = self._serial.read(take)
                drained += len(chunk)
            else:
                time.sleep(0.0005)
        return drained

    def _robust_clear(self) -> None:
        """Actively drain the FTDI internal FIFO before the next request.

        reset_input_buffer() only flushes the kernel-side buffer, NOT the
        FTDI chip's internal FIFO. Bytes still in the chip can land in the
        OS buffer right after the flush and contaminate the next response.
        We poll for actual bytes and read them out; once the line has been
        quiet for self._drain_quiet_sec (tuned to the latency_timer) we do
        one final OS-side flush.
        """
        deadline = time.time() + self._drain_max_sec
        last_byte_seen_at = time.time()
        while time.time() < deadline:
            pending = self._serial.in_waiting
            if pending:
                self._serial.read(pending)
                last_byte_seen_at = time.time()
            else:
                if time.time() - last_byte_seen_at >= self._drain_quiet_sec:
                    break
                time.sleep(0.0005)
        self._serial.reset_input_buffer()

    @staticmethod
    def _build(servo_id: int, instr: int, params: Optional[List[int]] = None) -> bytes:
        params = params or []
        pkt = [HEADER, HEADER, servo_id, 2 + len(params), instr] + params
        pkt.append(DynamixelManager._checksum(pkt))
        return bytes(pkt)

    @staticmethod
    def _checksum(pkt: List[int]) -> int:
        return (~sum(pkt[2:])) & 0xFF

    @staticmethod
    def _clamp_pos(value: int) -> int:
        return max(POS_MIN, min(POS_MAX, int(value)))

    def _require_initialized(self) -> None:
        if not self._is_initialized or self._serial is None:
            raise RuntimeError("Dynamixel bus is not initialized.")

    @staticmethod
    def _load_serial_module():
        try:
            import serial  # type: ignore
        except ImportError as exc:
            raise RuntimeError("pyserial is required. Install with: pip install pyserial") from exc
        return serial
