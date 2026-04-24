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
BUS_SETTLE_SEC = 1.5
FTDI_LATENCY_MS = 1
DRAIN_QUIET_SEC = 0.003
DRAIN_MAX_SEC = 0.030
INTER_PACKET_SEC = 0.004
INTER_PING_SEC = 0.008
HEALTH_CHECK_PASSES = 3
HEALTH_PASS_REST_SEC = 0.05
PING_RETRIES = 5
READ_RETRIES = 4
WRITE_RETRIES = 4
PING_TIMEOUT_SEC = 0.15
READ_TIMEOUT_SEC = 0.05
WRITE_STATUS_TIMEOUT_SEC = 0.05
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

    def initialize_bus(self) -> None:
        if self._serial and getattr(self._serial, "is_open", False):
            return

        serial = self._load_serial_module()
        self._serial = serial.Serial(port=self.serial_port, baudrate=self.baudrate, timeout=0.1)
        try:
            latency_ms = int(os.environ.get("NINA_DXL_LATENCY_MS", FTDI_LATENCY_MS))
        except ValueError:
            latency_ms = FTDI_LATENCY_MS
        latency_set = self._set_ftdi_latency_timer(latency_ms)
        self._latency_timer_ms = latency_ms if latency_set else None
        self._serial.reset_input_buffer()
        self._serial.reset_output_buffer()
        try:
            settle = float(os.environ.get("NINA_DXL_SETTLE_SEC", BUS_SETTLE_SEC))
        except ValueError:
            settle = BUS_SETTLE_SEC
        time.sleep(max(0.0, settle))
        self._robust_clear()
        self._is_initialized = True

    def _set_ftdi_latency_timer(self, value_ms: int) -> bool:
        """Set the FTDI USB-serial chip's latency_timer register.

        Default is 16ms which is too slow for Dynamixel - response bytes can
        sit in the FTDI FIFO long enough to leak into the next request's
        response window. 1ms is the Robotis-recommended value.

        We try the sysfs path (works for any FTDI on Linux). If that fails we
        silently continue - the active drain in _robust_clear still helps.
        Returns True if the timer was set, False otherwise.
        """
        port = getattr(self._serial, "port", None) or self.serial_port
        if not port or not port.startswith("/dev/"):
            return False
        device_name = port.rsplit("/", 1)[-1]
        candidates = [
            f"/sys/bus/usb-serial/devices/{device_name}/latency_timer",
        ]
        for path in candidates:
            try:
                with open(path, "w") as fh:
                    fh.write(str(int(value_ms)))
                return True
            except (FileNotFoundError, PermissionError, OSError):
                continue
        return False

    @property
    def latency_timer_ms(self) -> Optional[int]:
        return getattr(self, "_latency_timer_ms", None)

    def close(self) -> None:
        if self._serial and getattr(self._serial, "is_open", False):
            self._serial.close()
        self._is_initialized = False

    def run_health_check(self, passes: int = HEALTH_CHECK_PASSES) -> HealthReport:
        self._require_initialized()
        reachable: set = set()
        passes = max(1, int(passes))
        for pass_idx in range(passes):
            for sid in self.expected_motor_ids:
                if sid in reachable:
                    continue
                if self.ping(sid):
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
        return HealthReport(
            connected=connected,
            detected_motors=len(reachable),
            expected_motors=len(self.expected_motor_ids),
            detail=detail,
        )

    def set_torque_all(self, enable: bool) -> List[int]:
        """Enable or disable torque on every expected motor and verify by
        reading the torque register back. Retries any motor that didn't
        latch the new value. Returns the list of motor IDs that could NOT
        be set after all retries (empty list = success).

        This matters most for release-before-recording: a single dropped
        write would otherwise leave one motor rigid for the whole session
        with no warning.
        """
        self._require_initialized()
        target = 1 if enable else 0
        remaining = list(self.expected_motor_ids)
        for _ in range(max(1, TORQUE_VERIFY_PASSES)):
            for sid in remaining:
                self.write_reg(sid, *REG_TORQUE_ENABLE, target)
            still_wrong: List[int] = []
            for sid in remaining:
                actual = self.read_reg(sid, *REG_TORQUE_ENABLE)
                if actual is None or actual != target:
                    still_wrong.append(sid)
            if not still_wrong:
                return []
            remaining = still_wrong
            time.sleep(0.02)
        return remaining

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
        action = json.loads(action_path.read_text(encoding="utf-8"))
        frames = action.get("frames", [])
        self._last_speed = None
        self._last_goal.clear()
        if smooth:
            self._seed_last_goal_from_present()

        scale = max(0.05, float(speed_scale))
        next_deadline = time.time()
        for frame in frames:
            if smooth:
                next_deadline = self._execute_frame_smooth(
                    frame, scale=scale, deadline=next_deadline)
            else:
                self._execute_frame(frame, speed_scale=speed_scale)

    def _seed_last_goal_from_present(self) -> None:
        """Read every motor's current position so the first frame's
        smooth-speed math has a real starting point."""
        for sid in self.expected_motor_ids:
            value = self.read_reg(sid, *REG_PRESENT_POS)
            if value is not None:
                self._last_goal[sid] = self._clamp_pos(value)

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

        goal_speed: Dict[int, Tuple[int, int]] = {}
        for sid, goal in targets.items():
            prev = self._last_goal.get(sid, goal)
            delta = abs(goal - prev)
            speed = self._compute_smooth_speed(delta, duration)
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
        self._serial.write(pkt)
        self._serial.flush()
        time.sleep(max(INTER_PACKET_SEC, len(pkt) * 10.0 / self.baudrate))

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

        Reads are inherently jittery on a long half-duplex chain. Rather than
        dropping motors that fail to respond on a given pass (which makes the
        recorded frame skip them and leaves them frozen on playback), we fall
        back to the last successfully read position per motor. Every frame
        therefore contains every expected motor.
        """
        self._require_initialized()
        servos: Dict[str, Any] = {}
        for sid in self.expected_motor_ids:
            self._capture_total_reads += 1
            present = self.read_reg(sid, *REG_PRESENT_POS)
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
        """Read every expected motor once before recording starts so the
        last-known cache is seeded. Returns the seed values per motor.
        """
        self._require_initialized()
        self._last_positions.clear()
        self._capture_miss_count = 0
        self._capture_total_reads = 0
        seed: Dict[int, Optional[int]] = {sid: None for sid in self.expected_motor_ids}
        for sid in self.expected_motor_ids:
            for _ in range(max(1, max_attempts)):
                value = self.read_reg(sid, *REG_PRESENT_POS)
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
            self._serial.write(pkt)
            self._serial.flush()
            time.sleep(max(INTER_PACKET_SEC, len(pkt) * 10.0 / self.baudrate))
            if self._recv(sid, timeout=PING_TIMEOUT_SEC) is not None:
                return True
            if attempt < PING_RETRIES - 1:
                time.sleep(0.015)
        return False

    def read_reg(self, sid: int, addr: int, size: int) -> Optional[int]:
        pkt = self._build(sid, READ_DATA, [addr, size])
        for attempt in range(READ_RETRIES):
            self._robust_clear()
            self._serial.write(pkt)
            self._serial.flush()
            time.sleep(max(INTER_PACKET_SEC, len(pkt) * 10.0 / self.baudrate))
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
            self._serial.write(pkt)
            self._serial.flush()
            time.sleep(max(INTER_PACKET_SEC, len(pkt) * 10.0 / self.baudrate))
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

    def _robust_clear(self) -> None:
        """Actively drain the FTDI internal FIFO before the next request.

        reset_input_buffer() only flushes the kernel-side buffer, NOT the
        FTDI chip's internal FIFO. Bytes still in the chip can land in the
        OS buffer right after the flush and contaminate the next response.
        We poll for actual bytes and read them out; once the line has been
        quiet for DRAIN_QUIET_SEC we do one final OS-side flush.
        """
        deadline = time.time() + DRAIN_MAX_SEC
        last_byte_seen_at = time.time()
        while time.time() < deadline:
            pending = self._serial.in_waiting
            if pending:
                self._serial.read(pending)
                last_byte_seen_at = time.time()
            else:
                if time.time() - last_byte_seen_at >= DRAIN_QUIET_SEC:
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
