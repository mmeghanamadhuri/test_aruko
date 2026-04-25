import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

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

BUS_SETTLE_SEC = 0.25
INTER_PACKET_SEC = 0.004
PING_RETRIES = 3
READ_RETRIES = 4
PING_TIMEOUT_SEC = 0.08
READ_TIMEOUT_SEC = 0.05
WRITE_STATUS_TIMEOUT_SEC = 0.03


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

    def initialize_bus(self) -> None:
        if self._serial and getattr(self._serial, "is_open", False):
            return

        serial = self._load_serial_module()
        self._serial = serial.Serial(port=self.serial_port, baudrate=self.baudrate, timeout=0.1)
        self._serial.reset_input_buffer()
        self._serial.reset_output_buffer()
        time.sleep(BUS_SETTLE_SEC)
        self._robust_clear()
        self._is_initialized = True

    def close(self) -> None:
        if self._serial and getattr(self._serial, "is_open", False):
            self._serial.close()
        self._is_initialized = False

    def run_health_check(self) -> HealthReport:
        self._require_initialized()
        reachable = [sid for sid in self.expected_motor_ids if self.ping(sid)]
        missing = [sid for sid in self.expected_motor_ids if sid not in reachable]
        connected = len(missing) == 0
        detail = "All expected motors reachable." if connected else f"Missing motor IDs: {missing}"
        return HealthReport(
            connected=connected,
            detected_motors=len(reachable),
            expected_motors=len(self.expected_motor_ids),
            detail=detail,
        )

    def set_torque_all(self, enable: bool) -> None:
        self._require_initialized()
        value = 1 if enable else 0
        for sid in self.expected_motor_ids:
            self.write_reg(sid, *REG_TORQUE_ENABLE, value)

    def execute_action_file(self, action_path: Path) -> None:
        self._require_initialized()
        action = json.loads(action_path.read_text(encoding="utf-8"))
        frames = action.get("frames", [])
        for frame in frames:
            self._execute_frame(frame)

    def play_smooth(
        self,
        action_path: Path,
        sub_hz: float = 50.0,
        max_speed: int = 1023,
        warmup_sec: float = 0.5,
        speed: float = 1.0,
    ) -> None:
        """
        Smoothly play back a recorded action.

        Between every pair of recorded keyframes we linearly interpolate goal
        positions and push them as one SyncWrite per tick at `sub_hz` Hz.
        The motor's internal trapezoidal profile is bypassed by raising
        `Moving Speed` once at the start, so the trajectory shape is set by
        the interpolated goal stream rather than by the per-frame "step + wait"
        used in `execute_action_file`.

        `speed` is a playback-time multiplier:
          1.0 -> play at the recorded tempo
          0.5 -> half speed (twice as long)
          2.0 -> double speed (half as long)
        Interpolation density (sub_hz) is unchanged, so smoothness is preserved
        at any tempo.
        """
        self._require_initialized()
        action = json.loads(action_path.read_text(encoding="utf-8"))
        frames = action.get("frames", [])
        if not frames:
            return

        sub_hz = max(1.0, float(sub_hz))
        sub_dt = 1.0 / sub_hz
        speed = max(0.05, float(speed))

        self.set_moving_speed_all(max_speed)

        present: Dict[int, int] = {}
        for sid in self.expected_motor_ids:
            v = self.read_reg(sid, *REG_PRESENT_POS)
            if v is not None:
                present[sid] = self._clamp_pos(v)

        first_goals = self._frame_goals(frames[0])
        warmup = max(float(warmup_sec), float(frames[0].get("duration", 0.0)) / speed)
        if warmup > 0 and first_goals:
            self._interpolate_segment(present, first_goals, warmup, sub_dt)
        elif first_goals:
            self.sync_write_goal_position(first_goals)

        for i in range(len(frames) - 1):
            a = frames[i]
            b = frames[i + 1]
            delay = float(b.get("delay", 0.0)) / speed
            if delay > 0:
                time.sleep(delay)
            duration = max(0.001, float(b.get("duration", 0.05)) / speed)
            a_goals = self._frame_goals(a) or first_goals
            b_goals = self._frame_goals(b)
            self._interpolate_segment(a_goals, b_goals, duration, sub_dt)

    def sync_write(self, addr: int, size: int, payload: Dict[int, List[int]]) -> None:
        """Broadcast SyncWrite to many servos at once. No status return."""
        if not payload:
            return
        params: List[int] = [addr, size]
        for sid, data in payload.items():
            params.append(int(sid) & 0xFF)
            for byte in data[:size]:
                params.append(int(byte) & 0xFF)
        pkt = self._build(BROADCAST_ID, SYNC_WRITE, params)
        self._serial.write(pkt)
        self._serial.flush()
        time.sleep(max(INTER_PACKET_SEC, len(pkt) * 10.0 / self.baudrate))

    def sync_write_goal_position(self, positions: Dict[int, int]) -> None:
        if not positions:
            return
        payload: Dict[int, List[int]] = {}
        for sid, val in positions.items():
            if sid not in self.expected_motor_ids:
                continue
            v = self._clamp_pos(int(val))
            payload[sid] = [v & 0xFF, (v >> 8) & 0xFF]
        self.sync_write(*REG_GOAL_POSITION, payload)

    def set_moving_speed_all(self, speed: int) -> None:
        speed = max(0, min(1023, int(speed)))
        lo = speed & 0xFF
        hi = (speed >> 8) & 0xFF
        payload = {sid: [lo, hi] for sid in self.expected_motor_ids}
        self.sync_write(*REG_MOVING_SPEED, payload)

    def capture_frame(self, duration: float, speed: int = 200, delay: float = 0.0) -> Dict[str, Any]:
        self._require_initialized()
        servos = {}
        for sid in self.expected_motor_ids:
            present = self.read_reg(sid, *REG_PRESENT_POS)
            if present is not None:
                servos[str(sid)] = {"type": "absolute", "value": self._clamp_pos(present)}
        return {
            "delay": delay,
            "duration": duration,
            "speed": speed,
            "servos": servos,
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

    def write_reg(self, sid: int, addr: int, size: int, value: int) -> bool:
        value = int(value) & (0xFF if size == 1 else 0xFFFF)
        params = [addr, value & 0xFF] if size == 1 else [addr, value & 0xFF, (value >> 8) & 0xFF]
        pkt = self._build(sid, WRITE_DATA, params)
        self._serial.reset_input_buffer()
        self._serial.write(pkt)
        self._serial.flush()
        time.sleep(max(INTER_PACKET_SEC, len(pkt) * 10.0 / self.baudrate))
        self._recv(sid, timeout=WRITE_STATUS_TIMEOUT_SEC)
        return True

    def _interpolate_segment(
        self,
        a_goals: Dict[int, int],
        b_goals: Dict[int, int],
        duration: float,
        sub_dt: float,
    ) -> None:
        ids = sorted(set(a_goals.keys()) | set(b_goals.keys()))
        if not ids or duration <= 0:
            if b_goals:
                self.sync_write_goal_position(b_goals)
            if duration > 0:
                time.sleep(duration)
            return

        steps = max(1, int(round(duration / sub_dt)))
        start = time.monotonic()
        for k in range(1, steps):
            t = k / steps
            interp: Dict[int, int] = {}
            for sid in ids:
                av = a_goals.get(sid, b_goals.get(sid))
                bv = b_goals.get(sid, av)
                if av is None or bv is None:
                    continue
                interp[sid] = int(round(av + (bv - av) * t))
            if interp:
                self.sync_write_goal_position(interp)
            target = start + k * sub_dt
            sleep_for = target - time.monotonic()
            if sleep_for > 0:
                time.sleep(sleep_for)

        if b_goals:
            self.sync_write_goal_position(b_goals)
        target = start + duration
        sleep_for = target - time.monotonic()
        if sleep_for > 0:
            time.sleep(sleep_for)

    def _frame_goals(self, frame: Dict[str, Any]) -> Dict[int, int]:
        goals: Dict[int, int] = {}
        for raw_sid, spec in frame.get("servos", {}).items():
            try:
                sid = int(raw_sid)
            except (TypeError, ValueError):
                continue
            if sid not in self.expected_motor_ids:
                continue
            if spec.get("type", "absolute") != "absolute":
                continue
            value = spec.get("value")
            if value is None:
                continue
            goals[sid] = self._clamp_pos(int(value))
        return goals

    def _execute_frame(self, frame: Dict[str, Any]) -> None:
        delay = float(frame.get("delay", 0.0))
        duration = float(frame.get("duration", 1.0))
        speed = int(frame.get("speed", 200))
        servos = frame.get("servos", {})

        if delay > 0:
            time.sleep(delay)

        for sid in self.expected_motor_ids:
            self.write_reg(sid, *REG_MOVING_SPEED, speed)

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
        self._serial.reset_input_buffer()
        time.sleep(0.005)
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
