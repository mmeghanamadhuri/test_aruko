import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from nina.models.types import HealthReport


HEADER = 0xFF
PING = 0x01
READ_DATA = 0x02
WRITE_DATA = 0x03

REG_TORQUE_ENABLE = (24, 1)
REG_GOAL_POSITION = (30, 2)
REG_MOVING_SPEED = (32, 2)
REG_PRESENT_POS = (36, 2)

POS_MIN = 0
POS_MAX = 4095

BUS_SETTLE_SEC = 0.5
INTER_PACKET_SEC = 0.004
INTER_PING_SEC = 0.008
HEALTH_CHECK_PASSES = 3
HEALTH_PASS_REST_SEC = 0.05
PING_RETRIES = 5
READ_RETRIES = 4
PING_TIMEOUT_SEC = 0.15
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
        self._last_speed: Optional[int] = None
        self._last_positions: Dict[int, int] = {}
        self._capture_miss_count: int = 0
        self._capture_total_reads: int = 0

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

    def set_torque_all(self, enable: bool) -> None:
        self._require_initialized()
        value = 1 if enable else 0
        for sid in self.expected_motor_ids:
            self.write_reg(sid, *REG_TORQUE_ENABLE, value)

    def execute_action_file(self, action_path: Path, speed_scale: float = 1.0) -> None:
        self._require_initialized()
        action = json.loads(action_path.read_text(encoding="utf-8"))
        frames = action.get("frames", [])
        self._last_speed = None
        for frame in frames:
            self._execute_frame(frame, speed_scale=speed_scale)

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
