"""FastAPI application: REST endpoints for companion app and Sirena UI."""

from __future__ import annotations

import ipaddress
import logging
import secrets
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from fastapi import FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from nina.link_daemon import actions_bridge
from nina.link_daemon import actions_manifest
from nina.link_daemon import autonomy_bridge
from nina.link_daemon import depth_bridge
from nina.link_daemon import health_aggregator
from nina.link_daemon import slam_bridge
from nina.link_daemon.config import LinkDaemonConfig
from nina.link_daemon import manifest_audio
from nina.link_daemon import manifest_delete
from nina.link_daemon import media_static
from nina.link_daemon import record_bridge
from nina.link_daemon import robot_bridge
from nina.link_daemon import session_claim
from nina.link_daemon import host_control
from nina.link_daemon import vision_http
from nina.services.audio_generator import AudioGeneratorError
from nina.link_daemon.nm import NMError
from nina.link_daemon.state import LinkCoordinator, UserMode

log = logging.getLogger("nina.link_daemon.api")


def _bus_init_http_exception(exc: BaseException) -> HTTPException:
    """Map Dynamixel / ``build_app`` failures to JSON ``detail`` (companion + curl)."""
    if isinstance(exc, ModuleNotFoundError):
        return HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"Missing Python module: {exc}. On the Jetson: "
                "source .venv-link/bin/activate && pip install -r requirements-link.txt "
                "&& sudo systemctl restart nina-link"
            ),
        )
    if isinstance(exc, RuntimeError):
        return HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc).strip() or repr(exc),
        )
    if isinstance(exc, PermissionError):
        return HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Permission denied (serial/dialout?): {exc}",
        )
    if isinstance(exc, OSError):
        return HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"{type(exc).__name__}: {exc} — serial port may be busy; "
                "close Sirena UI or any other app using the Dynamixel bus, then retry."
            ),
        )
    return HTTPException(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=(str(exc).strip() or type(exc).__name__),
    )


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


class DriveInvertBody(BaseModel):
    """Flip left/right wheel polarity at runtime (matches Qt Drive Flip L/R)."""

    left: Optional[bool] = None
    right: Optional[bool] = None


class PlayActionBody(BaseModel):
    action: str = Field(..., min_length=1, max_length=160)


class AutonomyEnabledBody(BaseModel):
    """Toggle autonomous wander (same stack as Sirena UI when bridges are on)."""

    enabled: bool = True


class SlamSaveBody(BaseModel):
    """Save current SLAM occupancy grid as a PGM under ``nina/data/maps/``."""

    filename: str = Field(default="nina_map.pgm", max_length=120)


class AutonomyGoalBody(BaseModel):
    """World-frame goal point (millimetres, origin = SLAM map centre).

    The companion sends pixel + scale separately for sanity, but the
    daemon only consumes the millimetre coords - the planner runs in
    the same frame as the SLAM occupancy grid.
    """

    x_mm: float = Field(..., description="goal x in mm (origin = map centre, +x right)")
    y_mm: float = Field(..., description="goal y in mm (origin = map centre, +y forward)")


class RecordStartBody(BaseModel):
    """JSON may still use ``\"register\"`` for the manifest flag (alias)."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., min_length=1, max_length=64)
    seconds: float = Field(default=5.0, ge=0.5, le=120.0)
    hz: float = Field(default=20.0, ge=0.5, le=60.0)
    countdown: float = Field(default=3.0, ge=0.0, le=60.0)
    hold_after: bool = Field(default=False)
    register_manifest: bool = Field(default=True, alias="register")


class VisionOptionsBody(BaseModel):
    face: Optional[bool] = None
    objects: Optional[bool] = None
    object_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class VisionEnrollBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=160)
    target_samples: int = Field(default=8, ge=1, le=32)


class ActionAudioOffsetBody(BaseModel):
    action: str = Field(..., min_length=1, max_length=160)
    audio_offset: float = Field(default=0.0, ge=0.0, le=120.0)


class ActionNameBody(BaseModel):
    action: str = Field(..., min_length=1, max_length=160)


class ActionAudioGenerateBody(BaseModel):
    action: str = Field(..., min_length=1, max_length=160)
    text: str = Field(..., min_length=1, max_length=2000)
    lang: str = Field(default="en", min_length=2, max_length=16)
    tld: str = Field(default="com", min_length=2, max_length=16)
    audio_offset: float = Field(default=0.0, ge=0.0, le=120.0)


class DeleteManifestActionBody(BaseModel):
    action: str = Field(..., min_length=1, max_length=160)
    delete_recording: bool = Field(default=True)
    delete_audio: bool = Field(default=False)


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
            # Match sirena_ui.workers.drive_controller MIN_SPEED_PCT / MAX_SPEED_PCT.
            "drive_speed_min_percent": 15,
            "drive_speed_max_percent": 25,
            "drive_status_endpoint": "/v1/robot/drive/status",
            "drive_invert_endpoint": "/v1/robot/drive/invert",
            "actions_endpoint": "/v1/actions",
            "action_play_endpoint": "/v1/actions/play",
            "action_bridge_enabled": cfg.enable_action_bridge,
            "record_bridge_enabled": cfg.enable_record_bridge,
            "record_start_endpoint": "/v1/actions/record/start",
            "record_stop_endpoint": "/v1/actions/record/stop",
            "record_status_endpoint": "/v1/actions/record/status",
            "recordings_list_endpoint": "/v1/actions/recordings",
            "vision_stream_endpoint": "/v1/vision/stream",
            "vision_status_endpoint": "/v1/vision/status",
            "vision_options_endpoint": "/v1/vision/options",
            "vision_bridge_enabled": cfg.enable_vision_bridge,
            "actions_static_enabled": cfg.enable_actions_static,
            "media_file_endpoint": "/v1/media/file",
            "action_audio_info_endpoint": "/v1/actions/audio/info",
            "action_audio_offset_endpoint": "/v1/actions/audio/offset",
            "action_audio_clear_endpoint": "/v1/actions/audio/clear",
            "action_audio_generate_endpoint": "/v1/actions/audio/generate",
            "action_delete_endpoint": "/v1/actions/delete",
            "slam_status_endpoint": "/v1/slam/status",
            "slam_snapshot_endpoint": "/v1/slam/snapshot",
            "slam_occupancy_endpoint": "/v1/slam/occupancy",
            "slam_save_endpoint": "/v1/slam/save",
            "slam_bridge_enabled": cfg.enable_slam_bridge,
            "robot_health_endpoint": "/v1/robot/health",
            "depth_status_endpoint": "/v1/depth/status",
            "depth_stream_endpoint": "/v1/depth/stream",
            "depth_bridge_enabled": cfg.enable_depth_bridge,
            "autonomy_status_endpoint": "/v1/autonomy/status",
            "autonomy_enabled_endpoint": "/v1/autonomy/enabled",
            "autonomy_goal_endpoint": "/v1/autonomy/goal",
            "autonomy_bridge_enabled": cfg.enable_autonomy_bridge,
            "autonomy_supports_goto": cfg.enable_autonomy_bridge,
            "manifest_path": str(cfg.actions_manifest_path),
            "session_script_configured": bool(cfg.session_script),
            "message": (
                "POST /v1/robot/drive with direction+duration_ms when "
                "NINA_LINK_ENABLE_ROBOT_BRIDGE=1 on the Jetson."
                if cfg.enable_robot_bridge
                else "Enable NINA_LINK_ENABLE_ROBOT_BRIDGE on the Jetson for HTTP drive."
            ),
        }

    @app.get("/v1/robot/health")
    def robot_health_http() -> Dict[str, Any]:
        """Subsystem rows aggregated from bridges (companion Health screen)."""
        return health_aggregator.build_robot_health(cfg, coordinator)

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

    @app.get("/v1/robot/drive/status")
    def robot_drive_status_http() -> Dict[str, Any]:
        if not cfg.enable_robot_bridge:
            return {
                "ok": True,
                "bridge_enabled": False,
                "connected": False,
                "message": "Robot bridge disabled — set NINA_LINK_ENABLE_ROBOT_BRIDGE=1.",
                "invert_left": False,
                "invert_right": False,
            }
        st = robot_bridge.navigation_hw_status()
        st["bridge_enabled"] = True
        return st

    @app.post("/v1/robot/drive/invert")
    def robot_drive_invert(
        body: DriveInvertBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if body.left is None and body.right is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="Set at least one of left, right",
            )
        if not cfg.enable_robot_bridge:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Robot bridge disabled — set NINA_LINK_ENABLE_ROBOT_BRIDGE=1.",
            )
        return robot_bridge.set_wheel_invert(left=body.left, right=body.right)

    @app.post("/v1/system/poweroff")
    def system_poweroff_http(
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        """Shut down the Jetson host (requires passwordless sudo for poweroff — see docs)."""
        auth_mutate(authorization, request)
        return host_control.queue_poweroff()

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
        try:
            return actions_bridge.play_named_action(body.action)
        except Exception as exc:
            log.warning("POST /v1/actions/play init failed: %s", exc, exc_info=True)
            raise _bus_init_http_exception(exc) from exc

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
            register_manifest=body.register_manifest,
        )

    @app.post("/v1/actions/record/stop")
    def record_stop_http(
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
        return record_bridge.request_cancel_record()

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

    @app.get("/v1/actions/audio/info")
    def action_audio_info_http(
        action: str = Query(..., min_length=1, max_length=160),
    ) -> Dict[str, Any]:
        actions_root = cfg.actions_manifest_path.parent
        try:
            return manifest_audio.get_action_audio_info(
                cfg.actions_manifest_path, actions_root, action
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc

    def _require_actions_static_for_audio_edit() -> None:
        if not cfg.enable_actions_static:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Action audio editing disabled — set "
                    "NINA_LINK_ENABLE_ACTIONS_STATIC=1 on the Jetson "
                    "(manifest + audio files must be writable/servable)."
                ),
            )

    @app.post("/v1/actions/audio/offset")
    def action_audio_offset_http(
        body: ActionAudioOffsetBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        _require_actions_static_for_audio_edit()
        try:
            manifest_audio.set_action_audio_offset_only(
                cfg.actions_manifest_path,
                body.action,
                body.audio_offset,
            )
            return {"ok": True, "action": body.action, "audio_offset": body.audio_offset}
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/v1/actions/audio/clear")
    def action_audio_clear_http(
        body: ActionNameBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        _require_actions_static_for_audio_edit()
        try:
            manifest_audio.clear_action_audio_mapping(
                cfg.actions_manifest_path, body.action
            )
            return {"ok": True, "action": body.action}
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/v1/actions/audio/generate")
    def action_audio_generate_http(
        body: ActionAudioGenerateBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        _require_actions_static_for_audio_edit()
        actions_root = cfg.actions_manifest_path.parent
        try:
            out = manifest_audio.generate_action_audio_clip(
                cfg.actions_manifest_path,
                actions_root,
                body.action,
                body.text,
                lang=body.lang.strip(),
                tld=body.tld.strip(),
                offset=body.audio_offset,
            )
            return {
                "ok": True,
                "action": body.action,
                "saved_path": str(out),
                "audio_rel": f"audio/{body.action}.mp3",
            }
        except AudioGeneratorError as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/v1/actions/delete")
    def delete_manifest_action_http(
        body: DeleteManifestActionBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        _require_actions_static_for_audio_edit()
        actions_root = cfg.actions_manifest_path.parent
        try:
            return manifest_delete.delete_manifest_action(
                cfg.actions_manifest_path,
                actions_root,
                body.action.strip(),
                delete_recording=body.delete_recording,
                delete_audio=body.delete_audio,
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc

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

    @app.post("/v1/vision/enroll")
    def vision_enroll_http(
        body: VisionEnrollBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if not cfg.enable_vision_bridge:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, detail="Vision off"
            )
        try:
            return vision_http.start_enroll_face(
                body.name, target_samples=body.target_samples
            )
        except Exception as e:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR, str(e)
            ) from e

    @app.get("/v1/vision/enroll/status")
    def vision_enroll_status_http() -> Dict[str, Any]:
        if not cfg.enable_vision_bridge:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, detail="Vision off"
            )
        return vision_http.enroll_status_snapshot()

    @app.post("/v1/vision/announce")
    def vision_announce_http(
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if not cfg.enable_vision_bridge:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, detail="Vision off"
            )
        try:
            return vision_http.start_announce_objects()
        except Exception as e:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR, str(e)
            ) from e

    @app.get("/v1/vision/announce/status")
    def vision_announce_status_http() -> Dict[str, Any]:
        if not cfg.enable_vision_bridge:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, detail="Vision off"
            )
        return vision_http.announce_error_snapshot()

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

    @app.get("/v1/slam/status")
    def slam_status_http() -> Dict[str, Any]:
        if not cfg.enable_slam_bridge:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="SLAM bridge disabled — set NINA_LINK_ENABLE_SLAM_BRIDGE=1",
            )
        slam_bridge.ensure_bridge_started()
        br = slam_bridge.get_bridge()
        if br is None:
            return {"ok": False, "message": "slam unavailable"}
        out: Dict[str, Any] = {"ok": True, **br.status()}
        sj = br.snapshot_json()
        if sj:
            out["snapshot"] = sj
        return out

    @app.get("/v1/slam/snapshot")
    def slam_snapshot_http() -> Dict[str, Any]:
        if not cfg.enable_slam_bridge:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="SLAM bridge disabled",
            )
        slam_bridge.ensure_bridge_started()
        br = slam_bridge.get_bridge()
        if br is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "slam unavailable")
        sj = br.snapshot_json()
        if sj is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no SLAM snapshot yet")
        return sj

    @app.get("/v1/slam/occupancy")
    def slam_occupancy_http() -> Response:
        if not cfg.enable_slam_bridge:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="SLAM bridge disabled",
            )
        slam_bridge.ensure_bridge_started()
        br = slam_bridge.get_bridge()
        if br is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "slam unavailable")
        snap = br.latest_snapshot()
        data = br.occupancy_bytes()
        if snap is None or data is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, detail="no occupancy grid yet"
            )
        return Response(
            content=data,
            media_type="application/octet-stream",
            headers={
                "X-Slam-Width": str(snap.width),
                "X-Slam-Height": str(snap.height),
                "X-Slam-Scale-Mm-Per-Px": str(snap.scale_mm_per_px),
            },
        )

    @app.post("/v1/slam/save")
    def slam_save_http(
        body: SlamSaveBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if not cfg.enable_slam_bridge:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="SLAM bridge disabled",
            )
        fn = health_aggregator.safe_map_filename(body.filename)
        repo_root = Path(__file__).resolve().parents[2]
        out_dir = repo_root / "nina" / "data" / "maps"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / fn
        slam_bridge.ensure_bridge_started()
        br = slam_bridge.get_bridge()
        if br is None or not br.save_map(out_path):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="No SLAM map to save yet, or write failed",
            )
        return {"ok": True, "path": str(out_path), "filename": fn}

    @app.get("/v1/depth/status")
    def depth_status_http() -> Dict[str, Any]:
        if not cfg.enable_depth_bridge:
            return {
                "ok": False,
                "bridge_enabled": False,
                "message": "Depth bridge disabled",
            }
        return {"ok": True, "bridge_enabled": True, **depth_bridge.status_payload()}

    def _depth_mjpeg_iter() -> Iterator[bytes]:
        boundary = b"frame"
        for jpeg in depth_bridge.iter_depth_mjpeg(lambda: False, fps_cap=12.0):
            yield (
                b"--" + boundary + b"\r\n"
                b"Content-Type: image/jpeg\r\n\r\n"
                + jpeg
                + b"\r\n"
            )

    @app.get("/v1/depth/stream")
    def depth_stream_http() -> StreamingResponse:
        if not cfg.enable_depth_bridge:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Depth bridge disabled — set NINA_LINK_ENABLE_DEPTH_BRIDGE=1",
            )
        ok_open, dmsg = depth_bridge.acquire("stream_open_probe")
        if ok_open:
            depth_bridge.release("stream_open_probe")
        else:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"depth camera unavailable: {dmsg}",
            )
        return StreamingResponse(
            _depth_mjpeg_iter(),
            media_type="multipart/x-mixed-replace; boundary=frame",
        )

    @app.get("/v1/autonomy/status")
    def autonomy_status_http() -> Dict[str, Any]:
        if not cfg.enable_autonomy_bridge:
            return {
                "ok": True,
                "bridge_enabled": False,
                "message": "Autonomy bridge disabled",
            }
        return {"ok": True, "bridge_enabled": True, **autonomy_bridge.status_dict()}

    @app.post("/v1/autonomy/enabled")
    def autonomy_enabled_http(
        body: AutonomyEnabledBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        auth_mutate(authorization, request)
        if not cfg.enable_autonomy_bridge:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Autonomy bridge disabled — set NINA_LINK_ENABLE_AUTONOMY_BRIDGE=1",
            )
        return autonomy_bridge.set_enabled(body.enabled)

    @app.post("/v1/autonomy/goal")
    def autonomy_goal_set_http(
        body: AutonomyGoalBody,
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        """Arm the goto pilot to drive to (x_mm, y_mm).

        World frame = SLAM map frame: origin at map centre, +x right,
        +y forward. The companion gets the scale + pose from
        ``/v1/slam/snapshot`` and converts a tap on the occupancy
        bitmap to mm before POSTing here.
        """
        auth_mutate(authorization, request)
        if not cfg.enable_autonomy_bridge:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Autonomy bridge disabled — set "
                    "NINA_LINK_ENABLE_AUTONOMY_BRIDGE=1"
                ),
            )
        return autonomy_bridge.set_goal(body.x_mm, body.y_mm)

    @app.delete("/v1/autonomy/goal")
    def autonomy_goal_clear_http(
        request: Request,
        authorization: Optional[str] = Header(None),
    ) -> Dict[str, Any]:
        """Cancel an in-flight goto.

        If the goto was the reason autonomy turned on, this also
        disables autonomy (mirrors the Qt facade).
        """
        auth_mutate(authorization, request)
        if not cfg.enable_autonomy_bridge:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Autonomy bridge disabled",
            )
        return autonomy_bridge.clear_goal()

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
