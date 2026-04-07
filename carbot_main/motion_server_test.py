import socket
import json
import logging
import threading
import os
import serial
import time
from typing import Dict

from carbot_record import (
    ABS_IDS, REL_IDS, SERVO_IDS, REG_PRESENT_POS, BAUDRATE,
    read_reg, set_torque_all, _s16, play_frames, loop_frames,
    _load_from_path, ping
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
            
            self._thread = threading.Thread(target=self._play_loop, args=(frames, loop), daemon=True)
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

    def freeze(self):
        with self.lock:
            if self.ser and self.ser.is_open:
                set_torque_all(self.ser, True)

    def neutral(self):
        with self.lock:
            # We must stop playback loop if running before disabling torque,
            self.stop(mode="hard")

    def get_feedback(self) -> Dict[str, int]:
        positions = {}
        with self.lock:
            if self.ser and self.ser.is_open:
                for sid in SERVO_IDS:
                    pos = read_reg(self.ser, sid, *REG_PRESENT_POS)
                    if pos is not None:
                        positions[str(sid)] = pos
        return positions

    def _play_loop(self, frames, loop):
        try:
            if self.ser and self.ser.is_open:
                # Wake up and enable physical torque over the bus
                for sid in SERVO_IDS:
                    ping(self.ser, sid)
                set_torque_all(self.ser, True)
                
                if self._stop_flag.is_set():
                    return
                
                if loop:
                    loop_frames(self.ser, frames, stop_flag=self._stop_flag)
                else:
                    play_frames(self.ser, frames, stop_flag=self._stop_flag)
        except Exception as e:
            logging.error(f"Error during playback loop: {e}")
        finally:
            self.is_playing = False
            self._stop_flag.clear()

class MotionServer:
    def __init__(self, player: MotionPlayerWrapper, host=HOST, port=PORT):
        self.player = player
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
        logging.info(f"Motion Server safely listening on {self.host}:{self.port}")
        logging.info(f"Allowed motions directory: {MOTIONS_DIR}")
        
        try:
            while not self._shutdown_flag.is_set():
                try:
                    client_sock, addr = self.server_socket.accept()
                    logging.info(f"Connection from {addr}")
                    client_thread = threading.Thread(target=self.handle_client, args=(client_sock,), daemon=True)
                    client_thread.start()
                    self.client_threads.append(client_thread)
                except socket.timeout:
                    continue
        except KeyboardInterrupt:
            logging.info("Keyboard interrupt received, shutting down server...")
        finally:
            self.stop()

    def send_resp(self, sock, resp_dict):
        try:
            msg = json.dumps(resp_dict) + "\n"
            sock.sendall(msg.encode('utf-8'))
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
                        
                    buffer += data.decode('utf-8')
                    
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if not line:
                            continue
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
            logging.warning("Rejected packet: Malformed JSON")
            self.send_resp(client_sock, {"status": "error", "error": "Invalid format"})
            return

        if not isinstance(msg, dict):
            logging.warning("Rejected packet: JSON is not a dictionary")
            self.send_resp(client_sock, {"status": "error", "error": "JSON must be dict"})
            return

        if "cmd" not in msg:
            logging.warning("Rejected packet: Missing 'cmd'")
            self.send_resp(client_sock, {"status": "error", "error": "Missing 'cmd'"})
            return

        cmd = msg["cmd"]

        if cmd == "play":
            if "file" not in msg:
                logging.warning("Rejected 'play': Missing 'file'")
                self.send_resp(client_sock, {"status": "error", "error": "Missing file parameter"})
                return
            
            filename = msg["file"]
            
            if ".." in filename or filename.startswith("/"):
                logging.warning(f"Rejected path traversal: {filename}")
                self.send_resp(client_sock, {"status": "error", "error": "Path traversal not allowed"})
                return
                
            filepath = os.path.abspath(os.path.join(MOTIONS_DIR, filename))
            
            if not filepath.startswith(MOTIONS_DIR):
                logging.warning(f"Rejected external file path: {filepath}")
                self.send_resp(client_sock, {"status": "error", "error": "Constrained directory boundary crossed"})
                return

            if not os.path.exists(filepath):
                logging.warning(f"File not found: {filepath}")
                self.send_resp(client_sock, {"status": "error", "error": "File not found"})
                return
            
            loop = msg.get("loop", False)
            if not isinstance(loop, bool):
                self.send_resp(client_sock, {"status": "error", "error": "loop must be a boolean"})
                return
                
            logging.info(f"Executing payload: {filepath} (loop={loop})")
            self.player.play(filepath, loop=loop)
            self.send_resp(client_sock, {"status": "started"})
            
        elif cmd == "stop":
            mode = msg.get("mode", "soft")
            if mode not in ["soft", "hard"]:
                logging.warning(f"Invalid stop mode '{mode}', defaulting to soft")
                mode = "soft"
                
            logging.info(f"Issuing immediate {mode} stop...")
            self.player.stop(mode=mode)
            self.player.join()
            self.send_resp(client_sock, {"status": "stopped"})
            
        elif cmd == "status":
            is_playing = self.player.is_playing
            positions = self.player.get_feedback()
            self.send_resp(client_sock, {"status": "ok", "is_playing": is_playing, "positions": positions})
            
        elif cmd == "neutral":
            logging.info("Releasing all motors to neutral (torque disabled)...")
            self.player.neutral()
            self.send_resp(client_sock, {"status": "neutral"})

        elif cmd == "freeze":
            logging.info("Freezing all motors (torque enabled)...")
            self.player.freeze()
            self.send_resp(client_sock, {"status": "frozen"})

        elif cmd == "record":
            if "file" not in msg:
                self.send_resp(client_sock, {"status": "error", "error": "Missing file parameter"})
                return
            filename = msg["file"]
            if ".." in filename or filename.startswith("/"):
                self.send_resp(client_sock, {"status": "error", "error": "Path traversal not allowed"})
                return
            filepath = os.path.abspath(os.path.join(MOTIONS_DIR, filename))
            
            # Read existing file or create new list
            frames = []
            if os.path.exists(filepath):
                try:
                    with open(filepath, "r") as f:
                        frames = json.load(f)
                        if not isinstance(frames, list):
                            frames = []
                except:
                    frames = []

            # Fetch current pose
            positions = self.player.get_feedback()
            
            # Format as a frame
            frame = {
                "delay": msg.get("delay", 0.0),
                "duration": msg.get("duration", 1.0),
                "speed": msg.get("speed", 200),
                "servos": {}
            }
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
                        frame["servos"][sid_str] = {"type": "relative", "diff": 0, "sign": "+", "ref_pos": present_s16}
                    else:
                        diff_signed = present_s16 - prev_ref
                        sign_char = "+" if diff_signed >= 0 else "-"
                        frame["servos"][sid_str] = {"type": "relative", "diff": abs(diff_signed), "sign": sign_char, "ref_pos": present_s16}
                else:
                    frame["servos"][sid_str] = {"value": val}
                    
            frames.append(frame)
            
            try:
                with open(filepath, "w") as f:
                    json.dump(frames, f, indent=4)
                logging.info(f"Recorded frame to {filename}. Total frames: {len(frames)}")
                self.send_resp(client_sock, {"status": "recorded", "file": filename, "frame_count": len(frames)})
            except Exception as e:
                logging.error(f"Failed to record frame: {e}")
                self.send_resp(client_sock, {"status": "error", "error": f"Failed writing to disk: {e}"})

        else:
            logging.warning(f"Unknown command: '{cmd}'")
            self.send_resp(client_sock, {"status": "error", "error": "Unknown command"})

    def stop(self):
        logging.info("Initiating strict server shutdown sequence...")
        self._shutdown_flag.set()
        
        self.server_socket.close()
        
        self.player.stop(mode="hard") 
        self.player.join()
        
        if self.player.ser and self.player.ser.is_open:
            self.player.ser.close()
            
        for t in self.client_threads:
            if t.is_alive():
                t.join(timeout=1.0)
                
        logging.info("Server disconnected safely.")


if __name__ == "__main__":
    ser = None
    try:
        ser = serial.Serial(port="/dev/ttyUSB0", baudrate=BAUDRATE, timeout=0.1)
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        logging.info("Connected to Dynamixel bus successfully.")
    except Exception as e:
        logging.error(f"Hardware initialization failed: {e}")
        logging.warning("Initiating network server anyway for dry-run/development mode.")
    
    player = MotionPlayerWrapper(ser)
    server = MotionServer(player)
    server.start()
