"""
carbot_web_server.py
--------------------
FastAPI bridge that exposes the CarBot motion_server (running on Jetson Xavier NX)
to a local web UI over HTTP/WebSocket.


Architecture:
  Browser  <──HTTP/WS──>  This FastAPI server (Windows/Mac/Linux)
                      
                                                  │
                              TCP socket (Ethernet)
                                    │
                          motion_server.py (Jetson, port 5000)

Run:
    pip install fastapi uvicorn websockets
    python carbot_web_server.py

Then open:  http://localhost:8000
"""

import asyncio
import json
import logging
import os
import socket
import threading
import time
import webbrowser
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ── Config ─────────────────────────────────────────────────────────────────────
JETSON_IP   = os.environ.get("CARBOT_IP",   "192.168.99.1")
JETSON_PORT = int(os.environ.get("CARBOT_PORT", 5000))
WEB_PORT    = int(os.environ.get("WEB_PORT",    8000))
FILE_ROOT   = Path(os.environ.get("CARBOT_FILE_ROOT", Path(__file__).parent))
AUTO_OPEN_BROWSER = os.environ.get("CARBOT_AUTO_OPEN_BROWSER", "1").strip().lower() not in ("0", "false", "no")

SERVO_IDS = [1, 2, 3, 4, 5, 6, 7]
ABS_IDS   = [1, 2, 3, 4, 5]
REL_IDS   = [6, 7]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("carbot_web")

# ── Log ring buffer ────────────────────────────────────────────────────────────
MAX_LOG = 300
log_ring: deque = deque(maxlen=MAX_LOG)
main_loop: Optional[asyncio.AbstractEventLoop] = None

def append_log(level: str, msg: str):
    entry = {
        "ts":    datetime.now().strftime("%H:%M:%S.%f")[:-3],
        "level": level,
        "msg":   msg,
    }
    log_ring.append(entry)
    # Also emit to connected WebSocket clients from the main app loop.
    if main_loop and not main_loop.is_closed():
        asyncio.run_coroutine_threadsafe(_broadcast_log(entry), main_loop)


# ── WebSocket manager ──────────────────────────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        self.active: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        self.active.remove(ws)

    async def broadcast(self, data: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.active.remove(ws)


ws_manager = ConnectionManager()


async def _broadcast_log(entry: dict):
    await ws_manager.broadcast({"type": "log", "entry": entry})


# ── TCP client to Jetson ───────────────────────────────────────────────────────
def _send_tcp(payload: dict, ip=JETSON_IP, port=JETSON_PORT, timeout=4.0) -> Optional[dict]:
    """Synchronous TCP send/receive to motion_server on Jetson."""
    try:
        with socket.create_connection((ip, port), timeout=timeout) as sock:
            msg = json.dumps(payload) + "\n"
            sock.sendall(msg.encode("utf-8"))
            sock.settimeout(timeout)
            buf = b""
            while b"\n" not in buf:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
            line = buf.split(b"\n")[0].decode("utf-8").strip()
            if line:
                return json.loads(line)
    except socket.timeout:
        append_log("ERROR", f"TCP timeout ({ip}:{port})")
    except ConnectionRefusedError:
        append_log("ERROR", f"Connection refused ({ip}:{port}) – is motion_server running?")
    except Exception as e:
        append_log("ERROR", f"TCP error: {e}")
    return None


async def send_cmd(payload: dict) -> Optional[dict]:
    """Run blocking TCP call in thread pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _send_tcp, payload)


# ── Auto-poller ────────────────────────────────────────────────────────────────
poll_task: Optional[asyncio.Task] = None
POLL_INTERVAL = 1.0   # seconds


async def _poller():
    while True:
        try:
            resp = await send_cmd({"cmd": "status"})
            if resp:
                await ws_manager.broadcast({"type": "status", "data": resp})
        except Exception:
            pass
        await asyncio.sleep(POLL_INTERVAL)


# ── Lifespan ───────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global poll_task, main_loop
    main_loop = asyncio.get_running_loop()
    poll_task = asyncio.create_task(_poller())
    append_log("INFO", f"CarBot Web Server started – target {JETSON_IP}:{JETSON_PORT}")
    yield
    poll_task.cancel()
    main_loop = None


# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(title="CarBot Web UI", lifespan=lifespan)


# ── Pydantic models ────────────────────────────────────────────────────────────
class PlayRequest(BaseModel):
    file: str
    loop: bool = False

class StopRequest(BaseModel):
    mode: str = "soft"

class RecordRequest(BaseModel):
    file:     str
    delay:    float = 0.5
    duration: float = 1.0
    speed:    int   = 200
    actuator: Optional[dict] = None

class ServoMoveRequest(BaseModel):
    servo_id: int
    value:    int        # raw 16-bit for abs; signed offset for rel
    speed:    int = 200

class TorqueRequest(BaseModel):
    servo_id: Optional[int] = None   # None = all
    enable:   bool = True

class ActuatorRequest(BaseModel):
    action:      str            # "extend" | "retract" | "stop"
    distance_mm: Optional[float] = None
    duration:    Optional[float] = None

class FileSaveRequest(BaseModel):
    path: str
    content: Any

class PlayFrameRequest(BaseModel):
    frame: Dict[str, Any]
    loop: bool = False


def _safe_file_path(rel_path: str) -> Path:
    if not rel_path or not rel_path.strip():
        raise HTTPException(status_code=400, detail="path is required")

    normalized = Path(rel_path.replace("\\", "/"))
    if normalized.is_absolute():
        raise HTTPException(status_code=400, detail="path must be relative")
    if ".." in normalized.parts:
        raise HTTPException(status_code=400, detail="path traversal is not allowed")
    if normalized.suffix.lower() != ".json":
        raise HTTPException(status_code=400, detail="only .json files are supported")

    resolved = (FILE_ROOT / normalized).resolve()
    root_resolved = FILE_ROOT.resolve()
    if root_resolved not in resolved.parents and resolved != root_resolved:
        raise HTTPException(status_code=400, detail="path is outside the allowed root")
    return resolved


def _list_json_files() -> List[str]:
    if not FILE_ROOT.exists():
        return []
    files = []
    for path in FILE_ROOT.rglob("*.json"):
        if any(part.startswith(".") for part in path.relative_to(FILE_ROOT).parts):
            continue
        files.append(path.relative_to(FILE_ROOT).as_posix())
    return sorted(files)


# ── REST endpoints ─────────────────────────────────────────────────────────────

@app.get("/api/status")
async def api_status():
    resp = await send_cmd({"cmd": "status"})
    if resp is None:
        return JSONResponse({"error": "No response from robot"}, status_code=503)
    append_log("INFO", f"Status: playing={resp.get('is_playing')}")
    return resp


@app.post("/api/play")
async def api_play(req: PlayRequest):
    payload = {"cmd": "play", "file": req.file, "loop": req.loop}
    resp = await send_cmd(payload)
    if resp is None:
        return JSONResponse({"error": "No response"}, status_code=503)
    action = f"loop" if req.loop else "play"
    if resp.get("status") == "started":
        append_log("INFO", f"▶ {action.upper()} → {req.file}")
    else:
        append_log("ERROR", f"Play failed: {resp.get('error','unknown')}")
    await ws_manager.broadcast({"type": "cmd", "cmd": "play", "resp": resp})
    return resp


@app.post("/api/stop")
async def api_stop(req: StopRequest):
    resp = await send_cmd({"cmd": "stop", "mode": req.mode})
    if resp is None:
        return JSONResponse({"error": "No response"}, status_code=503)
    append_log("INFO", f"■ STOP ({req.mode})")
    await ws_manager.broadcast({"type": "cmd", "cmd": "stop", "resp": resp})
    return resp


@app.post("/api/neutral")
async def api_neutral():
    resp = await send_cmd({"cmd": "neutral"})
    if resp is None:
        return JSONResponse({"error": "No response"}, status_code=503)
    append_log("INFO", "NEUTRAL – torque OFF")
    await ws_manager.broadcast({"type": "cmd", "cmd": "neutral", "resp": resp})
    return resp


@app.post("/api/freeze")
async def api_freeze():
    resp = await send_cmd({"cmd": "freeze"})
    if resp is None:
        return JSONResponse({"error": "No response"}, status_code=503)
    append_log("INFO", "FREEZE – torque ON")
    await ws_manager.broadcast({"type": "cmd", "cmd": "freeze", "resp": resp})
    return resp


@app.post("/api/record")
async def api_record(req: RecordRequest):
    payload = {
        "cmd":      "record",
        "file":     req.file,
        "delay":    req.delay,
        "duration": req.duration,
        "speed":    req.speed,
    }
    if req.actuator:
        payload["actuator"] = req.actuator
    resp = await send_cmd(payload)
    if resp is None:
        return JSONResponse({"error": "No response"}, status_code=503)
    if resp.get("status") == "recorded":
        actuator_note = ""
        if req.actuator:
            actuator_note = f" | actuator={req.actuator.get('action', 'unknown')}"
        append_log("INFO", f"⏺ Frame #{resp.get('frame_count')} → {req.file}{actuator_note}")
    else:
        append_log("ERROR", f"Record failed: {resp.get('error','unknown')}")
    await ws_manager.broadcast({"type": "cmd", "cmd": "record", "resp": resp})
    return resp


@app.post("/api/servo/move")
async def api_servo_move(req: ServoMoveRequest):
    """
    Sends a 'servo_move' command.
    The motion_server must support this – if yours doesn't yet, 
    this endpoint documents the protocol extension needed.
    """
    payload = {
        "cmd":      "servo_move",
        "servo_id": req.servo_id,
        "value":    req.value,
        "speed":    req.speed,
    }
    resp = await send_cmd(payload)
    if resp is None:
        return JSONResponse({"error": "No response"}, status_code=503)
    mode = "abs" if req.servo_id in ABS_IDS else "rel"
    append_log("INFO", f"SERVO {req.servo_id} [{mode}] → {req.value}  speed={req.speed}")
    await ws_manager.broadcast({"type": "cmd", "cmd": "servo_move", "resp": resp})
    return resp


@app.post("/api/servo/torque")
async def api_torque(req: TorqueRequest):
    payload = {
        "cmd":    "torque",
        "servo_id": req.servo_id,
        "enable": req.enable,
    }
    resp = await send_cmd(payload)
    if resp is None:
        return JSONResponse({"error": "No response"}, status_code=503)
    sid_str = f"S{req.servo_id}" if req.servo_id else "ALL"
    append_log("INFO", f"TORQUE {sid_str} → {'ON' if req.enable else 'OFF'}")
    return resp


@app.post("/api/actuator")
async def api_actuator(req: ActuatorRequest):
    payload = {
        "cmd":    "actuator",
        "action": req.action,
    }
    if req.distance_mm is not None:
        payload["distance_mm"] = req.distance_mm
    if req.duration is not None:
        payload["duration"] = req.duration

    resp = await send_cmd(payload)
    if resp is None:
        return JSONResponse({"error": "No response"}, status_code=503)
    append_log("INFO", f"ACTUATOR {req.action.upper()} dist={req.distance_mm} dur={req.duration}")
    await ws_manager.broadcast({"type": "cmd", "cmd": "actuator", "resp": resp})
    return resp


@app.post("/api/play_frame")
async def api_play_frame(req: PlayFrameRequest):
    payload = {"cmd": "play_frame", "frame": req.frame, "loop": req.loop}
    resp = await send_cmd(payload)
    if resp is None:
        return JSONResponse({"error": "No response"}, status_code=503)
    if resp.get("error"):
        append_log("ERROR", f"Play frame failed: {resp.get('error')}")
    else:
        append_log("INFO", f"FRAME PLAY {'LOOP' if req.loop else 'ONCE'}")
    await ws_manager.broadcast({"type": "cmd", "cmd": "play_frame", "resp": resp})
    return resp


@app.get("/api/files")
async def api_files():
    resp = await send_cmd({"cmd": "list_files"})
    if resp is None:
        return JSONResponse({"error": "No response"}, status_code=503)
    if resp.get("status") != "ok":
        return JSONResponse({"error": resp.get("error", "list_files failed")}, status_code=400)
    files = resp.get("files", [])
    append_log("INFO", f"FILES LIST (Jetson) → {len(files)} JSON file(s)")
    return {"files": files}


@app.get("/api/file")
async def api_file_get(path: str = Query(..., description="Relative path to a JSON file")):
    resp = await send_cmd({"cmd": "get_file", "path": path})
    if resp is None:
        return JSONResponse({"error": "No response"}, status_code=503)
    if resp.get("status") != "ok":
        err = resp.get("error", "get_file failed")
        code = 404 if "not found" in err.lower() else 400
        append_log("ERROR", f"FILE LOAD failed: {path} ({err})")
        return JSONResponse({"error": err}, status_code=code)

    append_log("INFO", f"FILE LOAD (Jetson) → {path}")
    return {"path": resp.get("path", path), "content": resp.get("content")}


@app.post("/api/file")
async def api_file_save(req: FileSaveRequest):
    resp = await send_cmd({"cmd": "save_file", "path": req.path, "content": req.content})
    if resp is None:
        return JSONResponse({"error": "No response"}, status_code=503)
    if resp.get("status") != "ok":
        err = resp.get("error", "save_file failed")
        append_log("ERROR", f"FILE SAVE failed: {req.path} ({err})")
        return JSONResponse({"error": err}, status_code=400)

    append_log("INFO", f"FILE SAVE (Jetson) → {req.path}")
    return {"status": "saved", "path": resp.get("path", req.path)}


@app.get("/api/logs")
async def api_logs():
    return list(log_ring)


@app.get("/api/config")
async def api_config():
    return {
        "jetson_ip":   JETSON_IP,
        "jetson_port": JETSON_PORT,
        "servo_ids":   SERVO_IDS,
        "abs_ids":     ABS_IDS,
        "rel_ids":     REL_IDS,
    }


# ── WebSocket ──────────────────────────────────────────────────────────────────
@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    # Send history of logs on connect
    await websocket.send_json({"type": "log_history", "entries": list(log_ring)})
    try:
        while True:
            data = await websocket.receive_text()
            # Client can push raw commands via WS too
            try:
                msg = json.loads(data)
                resp = await send_cmd(msg)
                await websocket.send_json({"type": "ws_resp", "resp": resp})
            except Exception as e:
                await websocket.send_json({"type": "error", "msg": str(e)})
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


# ── Serve HTML UI ──────────────────────────────────────────────────────────────
HTML_FILE = Path(__file__).parent / "carbot_ui.html"

@app.get("/", response_class=HTMLResponse)
async def root():
    if HTML_FILE.exists():
        return HTMLResponse(HTML_FILE.read_text(encoding="utf-8"))
    return HTMLResponse("<h2>CarBot UI</h2><p>Place <code>carbot_ui.html</code> next to this file.</p>")


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    web_ui_url = f"http://localhost:{WEB_PORT}"

    if AUTO_OPEN_BROWSER:
        def _open_ui():
            try:
                webbrowser.open_new_tab(web_ui_url)
                log.info(f"Opened browser: {web_ui_url}")
            except Exception as e:
                log.warning(f"Could not auto-open browser: {e}")

        # Slight delay so the server has time to start listening.
        threading.Timer(1.0, _open_ui).start()

    print(f"""
╔══════════════════════════════════════════════════╗
║          CarBot Web Server                       ║
║  Target  : {JETSON_IP}:{JETSON_PORT:<26}║
║  Web UI  : {web_ui_url:<36}║
║                                                  ║
║  Override env vars:                              ║
║    CARBOT_IP   CARBOT_PORT   WEB_PORT            ║
║    CARBOT_AUTO_OPEN_BROWSER=0   (disable)        ║
╚══════════════════════════════════════════════════╝
""")
    uvicorn.run(
        "carbot_web_server:app",
        host="0.0.0.0",
        port=WEB_PORT,
        reload=False,
        log_level="info",
    )
