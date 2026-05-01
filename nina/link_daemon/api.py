"""FastAPI application: REST endpoints for companion app and Sirena UI."""

from __future__ import annotations

import ipaddress
import logging
import secrets
from typing import Any, Dict, Optional

from fastapi import FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from nina.link_daemon.config import LinkDaemonConfig
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
            "drive": "preview",
            "message": "Drive commands will attach to NinaService in a future revision.",
        }

    return app
