"""FastAPI application: REST endpoints for companion app and Sirena UI."""

from __future__ import annotations

import ipaddress
import logging
import secrets
from typing import Any, Dict, Iterator, Optional

from fastapi import FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from nina.link_daemon import actions_bridge
from nina.link_daemon import actions_manifest
from nina.link_daemon.config import LinkDaemonConfig
from nina.link_daemon import media_static
from nina.link_daemon import record_bridge
from nina.link_daemon import robot_bridge
from nina.link_daemon import session_claim
from nina.link_daemon import vision_http
from nina.link_daemon.nm import NMError
from nina.link_daemon.state import LinkCoordinator, UserMode

log = logging.getLogger("nina.link_daemon.api")


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    if request.client:
        return request.client.host
    return ""


def _is_loopback(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return ip == "127.0.0.1"


class ModeBody(BaseModel):
    mode: str = Field(
        ...,
        description="boot_default | force_ap | force_sta",
    )


class HomeWifiBody(BaseModel):
    ssid: str = Field(..., min_length=1, max_length=128)
    password: str = Field(default="", max_length=128)


class PairBody(BaseModel):
    pin: str = Field(..., min_length=4, max_length=12)


class DriveBody(BaseModel):
    direction: str = Field(
        ...,
        description="forward | back | left | right | stop",
    )
    duration_ms: int = Field(default=280, ge=50, le=5000)
    speed_percent: Optional[int] = Field(default=None, ge=5, le=100)


class PlayActionBody(BaseModel):
    action: str = Field(..., min_length=1, max_length=160)


class RecordStartBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    seconds: float = Field(default=5.0, ge=0.5, le=120.0)
    hz: float = Field(default=20.0, ge=0.5, le=60.0)
    countdown: float = Field(default=3.0, ge=0.0, le=60.0)
    hold_after: bool = Field(default=False)
    register: bool = Field(default=True)


class VisionOptionsBody(BaseModel):
    face: Optional[bool] = None
    objects: Optional[bool] = None
    object_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)


def create_app(cfg: LinkDaemonConfig, coordinator: LinkCoordinator) -> FastAPI:
    app = FastAPI(title="Nina Link Daemon", version="1.0.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def ensure_pairing_pin() -> None:
        if coordinator.ps.pairing_pin:
            return
        coordinator.ps.pairing_pin = f"{secrets.randbelow(1000000):06d}"
        coordinator.store.save(coordinator.ps)

    @app.middleware("http")
    async def touch_clients(request: Request, call_next):
        coordinator.record_http_client(_client_ip(request))
        return await call_next(request)

    def auth_mutate(authorization: Optional[str], request: Request) -> None:
        """Localhost is always trusted. Fleet token or session token otherwise."""
        ip = _client_ip(request)
        if _is_loopback(ip):
            return
        if cfg.token:
            token_ok = authorization == f"Bearer {cfg.token}"
            sess = coordinator.ps.session_token
            sess_ok = bool(sess and authorization == f"Bearer {sess}")
            if not (token_ok or sess_ok):
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unauthorized")
            return
        # No fleet token: permit remote mutation (trusted AP / onboarding LAN).
        return

    @app.get("/health")
    def health() -> Dict[str, Any]:
        return {"ok": True, "service": "nina-link", "mock_nm": cfg.mock_nm}

    @app.get("/v1/status")
    def get_status(request: Request) -> Dict[str, Any]:
        ensure_pairing_pin()
        ip = _client_ip(request)
        loop = _is_loopback(ip)
        role = coordinator.effective_wifi_role()
        ipv4 = coordinator.nm.get_ipv4_address()
        try:
            saved = coordinator.saved_networks_public()
        except Exception as e:
            log.exception("saved networks")
            saved = []
            coordinator.set_error(str(e))

        wait_left = coordinator.boot_wait_remaining_sec()
        um = coordinator.user_mode_enum()

        sta_info: Dict[str, Optional[str]] = {"ssid": None, "profile_name": None}
        try:
            sta_info = coordinator.nm.active_wifi_station_info()
        except Exception:
            log.exception("active_wifi_station_info")

        body: Dict[str, Any] = {
            "wifi_role": role,
            "ipv4": ipv4,
            "user_mode": um.value,
            "boot_wait_remaining_sec": wait_left,
            "client_seen": coordinator.ps.client_seen,
            "saved_networks": saved,
            "last_error": coordinator.ps.last_error,
            "paired": bool(coordinator.ps.session_token),
            "ap_ssid": cfg.ap_ssid,
            "active_sta_ssid": sta_info.get("ssid"),
            "active_sta_profile": sta_info.get("profile_name"),
        }
        if loop and coordinator.ps.pairing_pin:
            body["pairing_pin"] = coordinator.ps.pairing_pin
        if loop:
            body["session_token_present"] = bool(coordinator.ps.session_token)
        return body

    @app.post("/v1/pair")
    def pair(body: PairBody) -> Dict[str, str]:
        ensure_pairing_pin()
        pin = coordinator.ps.pairing_pin or ""
        if not pin or body.pin.strip() != pin:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid PIN")
        token = coordinator.issue_session_token()
        return {"token": token, "token_type": "Bearer"}

    @app.post("/v1/mode")
    def set_mode(
        body: ModeBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        mode_map = {
            "boot_default": UserMode.BOOT_DEFAULT,
            "force_ap": UserMode.FORCE_AP,
            "force_sta": UserMode.FORCE_STA,
        }
        if body.mode not in mode_map:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid mode")
        um = mode_map[body.mode]
        coordinator.set_user_mode(um)
        try:
            if um.value == "force_ap":
                coordinator.nm.start_hotspot(cfg.ap_ssid, cfg.ap_password)
                coordinator.ps.ap_started = True
                coordinator.store.save(coordinator.ps)
            elif um.value == "force_sta":
                saved = coordinator.refresh_saved_networks()
                if not saved:
                    coordinator.set_error("No saved Wi-Fi profiles")
                    return {
                        "ok": False,
                        "user_mode": body.mode,
                        "message": "Save home Wi-Fi credentials first.",
                    }
                coordinator.nm.activate_connection(saved[0].uuid)
            coordinator.clear_error()
        except NMError as e:
            coordinator.set_error(str(e))
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                {"message": str(e), "details": e.details},
            ) from e
        return {"ok": True, "user_mode": body.mode}

    @app.post("/v1/wifi/home-credentials")
    def save_home_wifi(
        body: HomeWifiBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        try:
            sn = coordinator.nm.add_wifi_connection(
                body.ssid,
                body.password,
                id_hint=None,
            )
            coordinator.clear_error()
            return {"ok": True, "profile": {"id": sn.id, "uuid": sn.uuid, "ssid": sn.ssid}}
        except NMError as e:
            coordinator.set_error(str(e))
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                {"message": str(e), "details": e.details},
            ) from e

    @app.post("/v1/wifi/connect-home")
    def connect_home(
        request: Request,
        authorization: Optional[str] = Header(None),
        ssid: Optional[str] = Query(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        saved = coordinator.refresh_saved_networks()
        if not saved:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "No saved Wi-Fi profiles")
        chosen = saved[0]
        if ssid:
            for p in saved:
                if p.ssid == ssid:
                    chosen = p
                    break
        try:
            coordinator.nm.activate_connection(chosen.uuid)
            coordinator.set_user_mode(UserMode.FORCE_STA)
            coordinator.clear_error()
            return {"ok": True, "connected_profile": chosen.ssid}
        except NMError as e:
            coordinator.set_error(str(e))
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                {"message": str(e), "details": e.details},
            ) from e

    @app.post("/v1/wifi/start-ap")
    def start_ap(
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        try:
            coordinator.nm.start_hotspot(cfg.ap_ssid, cfg.ap_password)
            coordinator.ps.ap_started = True
            coordinator.store.save(coordinator.ps)
            coordinator.set_user_mode(UserMode.FORCE_AP)
            coordinator.clear_error()
            return {"ok": True, "ssid": cfg.ap_ssid}
        except NMError as e:
            coordinator.set_error(str(e))
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                {"message": str(e), "details": e.details},
            ) from e

    @app.delete("/v1/wifi/saved/{profile_id}")
    def delete_saved(
        profile_id: str,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, bool]:
        auth_mutate(authorization, request)
        try:
            coordinator.nm.delete_connection(profile_id)
            coordinator.clear_error()
            return {"ok": True}
        except NMError as e:
            coordinator.set_error(str(e))
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                {"message": str(e), "details": e.details},
            ) from e

    @app.get("/v1/robot/capabilities")
    def capabilities() -> Dict[str, Any]:
        return {
            "drive": "momentary" if cfg.enable_robot_bridge else "disabled",
            "robot_bridge_enabled": cfg.enable_robot_bridge,
            "drive_endpoint": "/v1/robot/drive",
            "default_duration_ms": cfg.robot_drive_default_duration_ms,
            "default_speed_percent": cfg.robot_drive_speed_percent,
            "actions_endpoint": "/v1/actions",
            "action_play_endpoint": "/v1/actions/play",
            "action_bridge_enabled": cfg.enable_action_bridge,
            "record_bridge_enabled": cfg.enable_record_bridge,
            "record_start_endpoint": "/v1/actions/record/start",
            "record_status_endpoint": "/v1/actions/record/status",
            "recordings_list_endpoint": "/v1/actions/recordings",
            "vision_stream_endpoint": "/v1/vision/stream",
            "vision_status_endpoint": "/v1/vision/status",
            "vision_options_endpoint": "/v1/vision/options",
            "vision_bridge_enabled": cfg.enable_vision_bridge,
            "actions_static_enabled": cfg.enable_actions_static,
            "media_file_endpoint": "/v1/media/file",
            "manifest_path": str(cfg.actions_manifest_path),
            "session_script_configured": bool(cfg.session_script),
            "message": (
                "POST /v1/robot/drive with direction+duration_ms when "
                "NINA_LINK_ENABLE_ROBOT_BRIDGE=1 on the Jetson."
                if cfg.enable_robot_bridge
                else "Enable NINA_LINK_ENABLE_ROBOT_BRIDGE on the Jetson for HTTP drive."
            ),
        }

    @app.post("/v1/robot/drive")
    def robot_drive(
        body: DriveBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if not cfg.enable_robot_bridge:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Robot bridge disabled — set NINA_LINK_ENABLE_ROBOT_BRIDGE=1 on the Jetson "
                    "(do not run desktop Drive at the same time)."
                ),
            )
        direction = body.direction.strip().lower()
        allowed = frozenset({"forward", "back", "left", "right", "stop"})
        if direction not in allowed:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f"direction must be one of {sorted(allowed)}",
            )
        speed = (
            body.speed_percent
            if body.speed_percent is not None
            else cfg.robot_drive_speed_percent
        )
        duration_ms = body.duration_ms or cfg.robot_drive_default_duration_ms
        return robot_bridge.momentary_drive(
            direction=direction,
            duration_ms=duration_ms,
            speed_percent=speed,
        )

    @app.post("/v1/robot/emergency-stop")
    def robot_emergency_stop(
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if not cfg.enable_robot_bridge:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Robot bridge disabled",
            )
        return robot_bridge.emergency_stop()

    @app.get("/v1/actions")
    def list_actions_http() -> Dict[str, Any]:
        path = cfg.actions_manifest_path
        actions = actions_manifest.load_manifest_actions(path)
        return {"actions": actions, "manifest_path": str(path)}

    @app.post("/v1/actions/play")
    def play_action_http(
        body: PlayActionBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if not cfg.enable_action_bridge:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Action bridge disabled — set NINA_LINK_ENABLE_ACTION_BRIDGE=1 on the Jetson "
                    "(stop Sirena UI / other bus users first)."
                ),
            )
        return actions_bridge.play_named_action(body.action)

    @app.get("/v1/actions/recordings")
    def list_recordings_http() -> Dict[str, Any]:
        items = actions_manifest.list_recordings_on_disk(cfg.actions_manifest_path)
        return {"recordings": items}

    @app.get("/v1/actions/record/status")
    def record_status_http() -> Dict[str, Any]:
        return record_bridge.get_record_status()

    @app.post("/v1/actions/record/start")
    def record_start_http(
        body: RecordStartBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if not cfg.enable_record_bridge:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Record bridge disabled — set NINA_LINK_ENABLE_RECORD_BRIDGE=1 on the Jetson "
                    "(stop Sirena UI / other bus users first)."
                ),
            )
        return record_bridge.queue_record_session(
            name=body.name,
            seconds=body.seconds,
            hz=body.hz,
            countdown=body.countdown,
            hold_after=body.hold_after,
            register_manifest=body.register,
        )

    @app.get("/v1/media/file")
    def media_file_http(relative: str = Query(..., min_length=1, max_length=512)):
        if not cfg.enable_actions_static:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Static media disabled — set NINA_LINK_ENABLE_ACTIONS_STATIC=1 on the Jetson."
                ),
            )
        root = cfg.actions_manifest_path.parent
        path = media_static.resolve_safe_media_path(root, relative)
        return FileResponse(
            path,
            media_type=media_static.guess_content_type(path),
            filename=path.name,
        )

    @app.get("/v1/vision/status")
    def vision_status_http() -> Dict[str, Any]:
        if not cfg.enable_vision_bridge:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Vision bridge disabled — set NINA_LINK_ENABLE_VISION_BRIDGE=1 on the Jetson."
                ),
            )
        err = vision_http.vision_pipeline_error()
        if err:
            return {"ok": False, "message": err}
        return vision_http.vision_status_payload()

    @app.post("/v1/vision/options")
    def vision_options_http(
        body: VisionOptionsBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if not cfg.enable_vision_bridge:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Vision bridge disabled",
            )
        try:
            return vision_http.set_vision_options(
                face=body.face,
                object_=body.objects,
                object_confidence=body.object_confidence,
            )
        except Exception as e:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e)) from e

    @app.post("/v1/vision/open")
    def vision_open_http(
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if not cfg.enable_vision_bridge:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Vision off")
        try:
            return vision_http.open_camera_if_needed()
        except Exception as e:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e)) from e

    @app.post("/v1/vision/stop")
    def vision_stop_http(
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, str]:
        auth_mutate(authorization, request)
        if not cfg.enable_vision_bridge:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Vision off")
        vision_http.close_camera()
        return {"ok": "true"}

    @app.get("/v1/vision/detections")
    def vision_detections_http() -> Dict[str, Any]:
        if not cfg.enable_vision_bridge:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Vision off")
        try:
            dets = vision_http.last_detections_json()
            return {"detections": dets}
        except Exception as e:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e)) from e

    def _mjpeg_iter() -> Iterator[bytes]:
        boundary = b"frame"
        for jpeg in vision_http.iter_mjpeg_frames(lambda: False, fps_cap=15.0):
            yield (
                b"--" + boundary + b"\r\n"
                b"Content-Type: image/jpeg\r\n\r\n"
                + jpeg
                + b"\r\n"
            )

    @app.get("/v1/vision/stream")
    def vision_stream_http() -> StreamingResponse:
        if not cfg.enable_vision_bridge:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Vision bridge disabled — set NINA_LINK_ENABLE_VISION_BRIDGE=1 "
                    "(install OpenCV + sirena_ui vision deps on the Jetson)."
                ),
            )
        err = vision_http.vision_pipeline_error()
        if err:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=err)
        return StreamingResponse(
            _mjpeg_iter(),
            media_type="multipart/x-mixed-replace; boundary=frame",
        )

    @app.post("/v1/session/claim")
    def session_claim_http(
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if not cfg.session_script:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Configure NINA_LINK_SESSION_SCRIPT on the Jetson (executable helper)."
                ),
            )
        return session_claim.invoke_script(cfg.session_script, "claim")

    @app.post("/v1/session/release")
    def session_release_http(
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if not cfg.session_script:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="No script")
        return session_claim.invoke_script(cfg.session_script, "release")

    return app
