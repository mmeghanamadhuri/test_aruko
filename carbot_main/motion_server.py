import socket
import json
import logging
import threading
import os
import serial
import time
from typing import Dict, Optional

from carbot_record import (
    ABS_IDS, REL_IDS, SERVO_IDS,
    REG_PRESENT_POS, REG_GOAL_POSITION, REG_MOVING_SPEED, REG_TORQUE_ENABLE,
    BAUDRATE,
    read_reg, write_reg, set_torque_all, _s16,
    play_frames, loop_frames, _load_from_path, ping
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

HOST = "0.0.0.0"
PORT = 5000
MOTIONS_DIR = os.path.abspath(".")


class MotionPlayerWrapper:
    def __init__(self, ser: serial.Serial):
        self.ser = ser
        self._stop_flag = threading.Event()
        self.is_playing = False
        self._thread = None
        self.lock = threading.RLock()
        self._last_feedback: Dict[str, int] = {}

    def play(self, filepath: str, loop: bool = False):
        with self.lock:
            if self.is_playing:
                self.stop(mode="soft")
                self.join()

            frames = _load_from_path(filepath)
            if frames is None:
                logging.error(f"Failed to load frames from {filepath}")
                return

            self.is_playing = True
            self._stop_flag.clear()
            self._thread = threading.Thread(
                target=self._play_loop, args=(frames, loop), daemon=True
            )
            self._thread.start()

    def play_frame(self, frame: dict, loop: bool = False):
        """Play a single in-memory frame (or loop it) without loading from disk."""
        with self.lock:
            if self.is_playing:
                self.stop(mode="soft")
                self.join()

            self.is_playing = True
            self._stop_flag.clear()
            self._thread = threading.Thread(
                target=self._play_loop, args=([frame], loop), daemon=True
            )
            self._thread.start()

    def join(self):
        if self._thread and self._thread.is_alive():
            self._thread.join()

    def stop(self, mode="soft"):
        with self.lock:
            if self.is_playing:
                self._stop_flag.set()
            if mode == "hard":
                if self.ser and self.ser.is_open:
                    set_torque_all(self.ser, False)

    def freeze(self, servo_id: Optional[int] = None):
        """Enable torque. If servo_id is given, only that servo; else all."""
        with self.lock:
            if self.ser and self.ser.is_open:
                if servo_id is None:
                    set_torque_all(self.ser, True)
                else:
                    write_reg(self.ser, servo_id, *REG_TORQUE_ENABLE, 1)

    def neutral(self, servo_id: Optional[int] = None):
        """Disable torque. If servo_id is given, only that servo; else stop + disable all."""
        with self.lock:
            if servo_id is None:
                # Stop playback then cut all torque
                self.stop(mode="hard")
            else:
                if self.ser and self.ser.is_open:
                    write_reg(self.ser, servo_id, *REG_TORQUE_ENABLE, 0)

    def servo_move(self, servo_id: int, value: int, speed: int = 200) -> Optional[int]:
        """
        Move a single servo.
        - Abs servos (1-5): value is raw 16-bit goal position.
        - Rel servos (6-7): value is a signed offset from current present position.
        Returns the raw goal position sent, or None on failure.
        """
        with self.lock:
            if not (self.ser and self.ser.is_open):
                logging.error("Serial port not available for servo_move")
                return None

            write_reg(self.ser, servo_id, *REG_MOVING_SPEED, max(0, min(1023, speed)))

            if servo_id in REL_IDS:
                present = read_reg(self.ser, servo_id, *REG_PRESENT_POS)
                if present is None:
                    logging.error(f"servo_move: cannot read present pos for servo {servo_id}")
                    return None
                present_s16 = _s16(present)
                target = max(-32768, min(32767, present_s16 + value))
                raw_val = target & 0xFFFF
            else:
                raw_val = value & 0xFFFF

            write_reg(self.ser, servo_id, *REG_GOAL_POSITION, raw_val)
            return raw_val

    def get_feedback(self) -> Dict[str, int]:
        # Avoid bus contention during active playback by returning cached values.
        if self.is_playing:
            return dict(self._last_feedback)

        positions = {}
        with self.lock:
            if self.ser and self.ser.is_open:
                for sid in SERVO_IDS:
                    pos = read_reg(self.ser, sid, *REG_PRESENT_POS)
                    if pos is not None:
                        positions[str(sid)] = pos
        if positions:
            self._last_feedback = dict(positions)
        return positions

    def _play_loop(self, frames, loop):
        try:
            if self.ser and self.ser.is_open:
                for sid in SERVO_IDS:
                    ping(self.ser, sid)
                set_torque_all(self.ser, True)

                if self._stop_flag.is_set():
                    return

                if loop:
                    loop_frames(self.ser, frames, stop_flag=self._stop_flag)
                else:
                    play_frames(self.ser, frames, stop_flag=self._stop_flag)
        except serial.SerialException as e:
            logging.error(f"Serial exception during playback loop: {e}")
        except Exception as e:
            logging.error(f"Error during playback loop: {e}")
        finally:
            self.is_playing = False
            # Refresh cache once playback exits, if bus is still available.
            try:
                if self.ser and self.ser.is_open:
                    self._last_feedback = self.get_feedback()
            except Exception:
                pass
            self._stop_flag.clear()


class MotionServer:
    def __init__(self, player: MotionPlayerWrapper, arm=None, host=HOST, port=PORT):
        self.player = player
        self.arm = arm          # LinearActuator instance, or None
        self.host = host
        self.port = port
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._shutdown_flag = threading.Event()
        self.client_threads = []

    def start(self):
        if not os.path.exists(MOTIONS_DIR):
            os.makedirs(MOTIONS_DIR)

        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(5)
        self.server_socket.settimeout(1.0)
        logging.info(f"Motion Server listening on {self.host}:{self.port}")
        logging.info(f"Motions directory: {MOTIONS_DIR}")
        logging.info(f"Actuator available: {self.arm is not None}")

        try:
            while not self._shutdown_flag.is_set():
                try:
                    client_sock, addr = self.server_socket.accept()
                    logging.info(f"Connection from {addr}")
                    t = threading.Thread(
                        target=self.handle_client, args=(client_sock,), daemon=True
                    )
                    t.start()
                    self.client_threads.append(t)
                except socket.timeout:
                    continue
        except KeyboardInterrupt:
            logging.info("Keyboard interrupt — shutting down...")
        finally:
            self.stop()

    def send_resp(self, sock, resp_dict):
        try:
            msg = json.dumps(resp_dict) + "\n"
            sock.sendall(msg.encode("utf-8"))
        except Exception as e:
            logging.error(f"Failed to send response: {e}")

    def handle_client(self, client_sock):
        buffer = ""
        try:
            client_sock.settimeout(1.0)
            while not self._shutdown_flag.is_set():
                try:
                    data = client_sock.recv(4096)
                    if not data:
                        break
                    buffer += data.decode("utf-8")
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if line:
                            self.process_message(client_sock, line)
                except socket.timeout:
                    continue
        except Exception as e:
            logging.error(f"Client processing error: {e}")
        finally:
            client_sock.close()

    def process_message(self, client_sock, line):
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            self.send_resp(client_sock, {"status": "error", "error": "Invalid JSON"})
            return

        if not isinstance(msg, dict) or "cmd" not in msg:
            self.send_resp(client_sock, {"status": "error", "error": "Missing 'cmd'"})
            return

        cmd = msg["cmd"]

        def _resolve_json_path(rel_path: str) -> Optional[str]:
            if not isinstance(rel_path, str) or not rel_path.strip():
                return None
            if ".." in rel_path or rel_path.startswith("/"):
                return None
            if not rel_path.lower().endswith(".json"):
                return None
            filepath = os.path.abspath(os.path.join(MOTIONS_DIR, rel_path))
            if not filepath.startswith(MOTIONS_DIR):
                return None
            return filepath

        def _normalize_frame(frame_dict: dict) -> dict:
            """Normalize incoming frame payload to recorder/playback shape."""
            servos_in = frame_dict.get("servos", {})
            if not isinstance(servos_in, dict):
                raise ValueError("frame.servos must be an object")

            servos_out = {}
            for sid_key, servo_cfg in servos_in.items():
                sid_int = int(sid_key)
                servos_out[sid_int] = servo_cfg

            normalized = {
                "delay": float(frame_dict.get("delay", 0.5)),
                "duration": float(frame_dict.get("duration", 1.0)),
                "speed": int(frame_dict.get("speed", 200)),
                "servos": servos_out,
            }
            if "actuator" in frame_dict:
                if not isinstance(frame_dict["actuator"], dict):
                    raise ValueError("frame.actuator must be an object")
                normalized["actuator"] = frame_dict["actuator"]
            return normalized

        # ── play ──────────────────────────────────────────────────────────────
        if cmd == "play":
            if "file" not in msg:
                self.send_resp(client_sock, {"status": "error", "error": "Missing file parameter"})
                return

            filename = msg["file"]
            if ".." in filename or filename.startswith("/"):
                self.send_resp(client_sock, {"status": "error", "error": "Path traversal not allowed"})
                return

            filepath = os.path.abspath(os.path.join(MOTIONS_DIR, filename))
            if not filepath.startswith(MOTIONS_DIR):
                self.send_resp(client_sock, {"status": "error", "error": "Directory boundary crossed"})
                return
            if not os.path.exists(filepath):
                self.send_resp(client_sock, {"status": "error", "error": "File not found"})
                return

            loop = msg.get("loop", False)
            if not isinstance(loop, bool):
                self.send_resp(client_sock, {"status": "error", "error": "loop must be a boolean"})
                return

            logging.info(f"Playing: {filepath} (loop={loop})")
            self.player.play(filepath, loop=loop)
            self.send_resp(client_sock, {"status": "started"})

        # ── list_files (JSON under motions dir) ──────────────────────────────
        elif cmd == "list_files":
            files = []
            for root, _, names in os.walk(MOTIONS_DIR):
                for name in names:
                    if not name.lower().endswith(".json"):
                        continue
                    full = os.path.join(root, name)
                    rel = os.path.relpath(full, MOTIONS_DIR).replace("\\", "/")
                    if rel.startswith("./"):
                        rel = rel[2:]
                    files.append(rel)
            files.sort()
            self.send_resp(client_sock, {"status": "ok", "files": files})

        # ── get_file (read JSON content from motions dir) ────────────────────
        elif cmd == "get_file":
            rel_path = msg.get("path")
            filepath = _resolve_json_path(rel_path)
            if filepath is None:
                self.send_resp(client_sock, {"status": "error", "error": "Invalid JSON path"})
                return
            if not os.path.exists(filepath):
                self.send_resp(client_sock, {"status": "error", "error": "File not found"})
                return
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = json.load(f)
            except Exception as e:
                self.send_resp(client_sock, {"status": "error", "error": f"Read failed: {e}"})
                return
            self.send_resp(client_sock, {"status": "ok", "path": rel_path, "content": content})

        # ── save_file (write JSON content to motions dir) ────────────────────
        elif cmd == "save_file":
            rel_path = msg.get("path")
            content = msg.get("content")
            filepath = _resolve_json_path(rel_path)
            if filepath is None:
                self.send_resp(client_sock, {"status": "error", "error": "Invalid JSON path"})
                return
            try:
                os.makedirs(os.path.dirname(filepath), exist_ok=True)
                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(content, f, indent=2)
                self.send_resp(client_sock, {"status": "ok", "path": rel_path})
            except Exception as e:
                self.send_resp(client_sock, {"status": "error", "error": f"Write failed: {e}"})
                return

        # ── play_frame ────────────────────────────────────────────────────────
        elif cmd == "play_frame":
            frame = msg.get("frame")
            loop = msg.get("loop", False)
            if not isinstance(frame, dict):
                self.send_resp(client_sock, {"status": "error", "error": "frame must be an object"})
                return
            if not isinstance(loop, bool):
                self.send_resp(client_sock, {"status": "error", "error": "loop must be a boolean"})
                return
            try:
                normalized_frame = _normalize_frame(frame)
            except Exception as e:
                self.send_resp(client_sock, {"status": "error", "error": f"Invalid frame payload: {e}"})
                return

            logging.info(f"Playing single frame (loop={loop})")
            self.player.play_frame(normalized_frame, loop=loop)
            self.send_resp(client_sock, {"status": "started", "mode": "play_frame"})

        # ── stop ──────────────────────────────────────────────────────────────
        elif cmd == "stop":
            mode = msg.get("mode", "soft")
            if mode not in ("soft", "hard"):
                mode = "soft"
            logging.info(f"Stop ({mode})")
            self.player.stop(mode=mode)
            self.player.join()
            self.send_resp(client_sock, {"status": "stopped"})

        # ── status ────────────────────────────────────────────────────────────
        elif cmd == "status":
            positions = self.player.get_feedback()
            self.send_resp(client_sock, {
                "status": "ok",
                "is_playing": self.player.is_playing,
                "positions": positions,
            })

        # ── neutral  (torque OFF — single servo or all) ───────────────────────
        elif cmd == "neutral":
            servo_id = msg.get("servo_id")   # optional — None means all
            if servo_id is not None and servo_id not in SERVO_IDS:
                self.send_resp(client_sock, {"status": "error", "error": f"Invalid servo_id {servo_id}"})
                return
            label = f"S{servo_id}" if servo_id else "ALL"
            logging.info(f"Neutral (torque OFF) → {label}")
            self.player.neutral(servo_id=servo_id)
            self.send_resp(client_sock, {"status": "neutral", "servo_id": servo_id})

        # ── freeze  (torque ON — single servo or all) ─────────────────────────
        elif cmd == "freeze":
            servo_id = msg.get("servo_id")   # optional — None means all
            if servo_id is not None and servo_id not in SERVO_IDS:
                self.send_resp(client_sock, {"status": "error", "error": f"Invalid servo_id {servo_id}"})
                return
            label = f"S{servo_id}" if servo_id else "ALL"
            logging.info(f"Freeze (torque ON) → {label}")
            self.player.freeze(servo_id=servo_id)
            self.send_resp(client_sock, {"status": "frozen", "servo_id": servo_id})

        # ── torque  (explicit enable/disable — single servo or all) ───────────
        elif cmd == "torque":
            servo_id = msg.get("servo_id")   # None = all
            enable   = msg.get("enable", True)
            if not isinstance(enable, bool):
                self.send_resp(client_sock, {"status": "error", "error": "enable must be bool"})
                return
            if servo_id is not None and servo_id not in SERVO_IDS:
                self.send_resp(client_sock, {"status": "error", "error": f"Invalid servo_id {servo_id}"})
                return
            label = f"S{servo_id}" if servo_id else "ALL"
            logging.info(f"Torque {'ON' if enable else 'OFF'} → {label}")
            if enable:
                self.player.freeze(servo_id=servo_id)
            else:
                self.player.neutral(servo_id=servo_id)
            self.send_resp(client_sock, {"status": "ok", "servo_id": servo_id, "enable": enable})

        # ── servo_move  (move a single servo to a position) ───────────────────
        elif cmd == "servo_move":
            servo_id = msg.get("servo_id")
            value    = msg.get("value")
            speed    = msg.get("speed", 200)

            if servo_id is None or value is None:
                self.send_resp(client_sock, {"status": "error", "error": "Missing servo_id or value"})
                return
            if servo_id not in SERVO_IDS:
                self.send_resp(client_sock, {"status": "error", "error": f"Invalid servo_id {servo_id}"})
                return
            if not isinstance(value, int) or not isinstance(speed, int):
                self.send_resp(client_sock, {"status": "error", "error": "value and speed must be integers"})
                return

            mode = "rel" if servo_id in REL_IDS else "abs"
            logging.info(f"servo_move S{servo_id} [{mode}] value={value} speed={speed}")

            raw = self.player.servo_move(servo_id, value, speed)
            if raw is None:
                self.send_resp(client_sock, {"status": "error", "error": "servo_move failed (check serial / present pos)"})
                return

            deg = _s16(raw) * 0.088
            self.send_resp(client_sock, {
                "status": "ok",
                "servo_id": servo_id,
                "mode":     mode,
                "raw":      raw,
                "degrees":  round(deg, 2),
            })

        # ── actuator  (extend / retract / stop — with distance_mm) ────────────
        elif cmd == "actuator":
            action      = msg.get("action")
            distance_mm = msg.get("distance_mm")   # None = full travel
            duration    = msg.get("duration")       # None = derive from distance

            if action not in ("extend", "retract", "stop"):
                self.send_resp(client_sock, {"status": "error", "error": "action must be extend/retract/stop"})
                return

            if self.arm is None:
                self.send_resp(client_sock, {"status": "error", "error": "LinearActuator not initialised on this device"})
                return

            logging.info(f"Actuator {action.upper()} distance_mm={distance_mm} duration={duration}")
            try:
                if action == "extend":
                    kwargs = {}
                    if distance_mm is not None:
                        kwargs["distance_mm"] = float(distance_mm)
                    if duration is not None:
                        kwargs["duration"] = float(duration)
                    self.arm.extend(**kwargs)
                elif action == "retract":
                    kwargs = {}
                    if distance_mm is not None:
                        kwargs["distance_mm"] = float(distance_mm)
                    if duration is not None:
                        kwargs["duration"] = float(duration)
                    self.arm.retract(**kwargs)
                elif action == "stop":
                    self.arm.stop()
                self.send_resp(client_sock, {
                    "status":      "ok",
                    "action":      action,
                    "distance_mm": distance_mm,
                    "duration":    duration,
                })
            except Exception as e:
                logging.error(f"Actuator error: {e}")
                self.send_resp(client_sock, {"status": "error", "error": str(e)})

        # ── record ────────────────────────────────────────────────────────────
        elif cmd == "record":
            if "file" not in msg:
                self.send_resp(client_sock, {"status": "error", "error": "Missing file parameter"})
                return
            filename = msg["file"]
            if ".." in filename or filename.startswith("/"):
                self.send_resp(client_sock, {"status": "error", "error": "Path traversal not allowed"})
                return
            filepath = os.path.abspath(os.path.join(MOTIONS_DIR, filename))

            frames = []
            if os.path.exists(filepath):
                try:
                    with open(filepath, "r") as f:
                        frames = json.load(f)
                        if not isinstance(frames, list):
                            frames = []
                except Exception:
                    frames = []

            positions = self.player.get_feedback()

            frame = {
                "delay":    msg.get("delay",    0.0),
                "duration": msg.get("duration", 1.0),
                "speed":    msg.get("speed",    200),
                "servos":   {},
            }
            actuator = msg.get("actuator")
            if actuator is not None:
                if not isinstance(actuator, dict):
                    self.send_resp(client_sock, {"status": "error", "error": "actuator must be an object"})
                    return
                action = actuator.get("action")
                if action not in ("extend", "retract", "stop"):
                    self.send_resp(client_sock, {"status": "error", "error": "actuator.action must be extend/retract/stop"})
                    return
                frame["actuator"] = {"action": action}
                if "distance_mm" in actuator and actuator["distance_mm"] is not None:
                    frame["actuator"]["distance_mm"] = float(actuator["distance_mm"])
                if "duration" in actuator and actuator["duration"] is not None:
                    frame["actuator"]["duration"] = float(actuator["duration"])

            for sid_str, val in positions.items():
                sid = int(sid_str)
                if sid in ABS_IDS:
                    frame["servos"][sid_str] = {"type": "absolute", "value": val}
                elif sid in REL_IDS:
                    present_s16 = _s16(val)
                    prev_ref = None
                    for prev_frame in reversed(frames):
                        prev_sv = prev_frame["servos"].get(sid_str)
                        if prev_sv is not None and prev_sv.get("ref_pos") is not None:
                            prev_ref = prev_sv["ref_pos"]
                            break
                    if prev_ref is None:
                        frame["servos"][sid_str] = {
                            "type": "relative", "diff": 0, "sign": "+", "ref_pos": present_s16
                        }
                    else:
                        diff_signed = present_s16 - prev_ref
                        sign_char   = "+" if diff_signed >= 0 else "-"
                        frame["servos"][sid_str] = {
                            "type": "relative",
                            "diff": abs(diff_signed),
                            "sign": sign_char,
                            "ref_pos": present_s16,
                        }
                else:
                    frame["servos"][sid_str] = {"value": val}

            frames.append(frame)
            try:
                with open(filepath, "w") as f:
                    json.dump(frames, f, indent=4)
                logging.info(f"Recorded frame to {filename}. Total: {len(frames)}")
                self.send_resp(client_sock, {
                    "status":      "recorded",
                    "file":        filename,
                    "frame_count": len(frames),
                })
            except Exception as e:
                logging.error(f"Failed to write frame: {e}")
                self.send_resp(client_sock, {"status": "error", "error": f"Disk write failed: {e}"})

        else:
            logging.warning(f"Unknown command: '{cmd}'")
            self.send_resp(client_sock, {"status": "error", "error": f"Unknown command '{cmd}'"})

    def stop(self):
        logging.info("Shutting down server...")
        self._shutdown_flag.set()
        self.server_socket.close()
        self.player.stop(mode="hard")
        self.player.join()
        if self.player.ser and self.player.ser.is_open:
            self.player.ser.close()
        for t in self.client_threads:
            if t.is_alive():
                t.join(timeout=1.0)
        logging.info("Server stopped.")


if __name__ == "__main__":
    # ── Serial port ───────────────────────────────────────────────────────────
    ser = None
    try:
        ser = serial.Serial(port="/dev/ttyUSB0", baudrate=BAUDRATE, timeout=0.1)
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        logging.info("Connected to Dynamixel bus.")
    except Exception as e:
        logging.error(f"Hardware init failed: {e}")
        logging.warning("Starting in dry-run/dev mode (no serial).")

    # ── Linear actuator ───────────────────────────────────────────────────────
    arm = None
    try:
        from actuator import LinearActuator
        arm = LinearActuator(in3_pin=35, in4_pin=37)
        logging.info("LinearActuator initialised on pins 35/37.")
    except ImportError:
        logging.warning("actuator.py not found — actuator commands disabled.")
    except Exception as e:
        logging.error(f"LinearActuator init failed: {e}")

    player = MotionPlayerWrapper(ser)
    server = MotionServer(player, arm=arm)
    server.start()
