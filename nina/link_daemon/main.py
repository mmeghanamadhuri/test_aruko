"""Entry point: `python -m nina.link_daemon.main` or uvicorn nina.link_daemon.api:create_app (factory)."""

from __future__ import annotations

import logging
import os
import sys
import threading
import time

import uvicorn

from nina.link_daemon.api import create_app
from nina.link_daemon.config import load_config
from nina.link_daemon.nm import NMBackend, mock_backend
from nina.link_daemon.nm import NMError
from nina.link_daemon.state import LinkCoordinator, UserMode

log = logging.getLogger("nina.link_daemon.main")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if raw in ("1", "true", "yes", "on", "y"):
        return True
    if raw in ("0", "false", "no", "off", "n"):
        return False
    return default


def _maybe_boot_ap(coordinator: LinkCoordinator) -> None:
    if not _env_bool("NINA_LINK_BOOT_AP", True):
        log.info("NINA_LINK_BOOT_AP disabled — skip Wi-Fi orchestration on startup")
        return
    if coordinator.nm.mock:
        try:
            coordinator.nm.start_hotspot(
                coordinator.cfg.ap_ssid,
                coordinator.cfg.ap_password,
            )
            coordinator.ps.ap_started = True
            coordinator.store.save(coordinator.ps)
        except NMError as e:
            log.warning("Mock boot AP: %s", e)
        return

    cfg = coordinator.cfg
    # Always prefer Nina AP on boot; STA only via app (connect-home / force_sta live action).
    if cfg.disable_wifi_autoconnect:
        try:
            coordinator.nm.disable_autoconnect_all_saved_wifi()
        except Exception as e:
            log.warning("disable_autoconnect_all_saved_wifi: %s", e)

    try:
        coordinator.nm.start_hotspot(cfg.ap_ssid, cfg.ap_password)
        coordinator.ps.ap_started = True
        coordinator.store.save(coordinator.ps)
        log.info("Boot AP started SSID=%s", cfg.ap_ssid)
    except NMError as e:
        coordinator.set_error(str(e))
        detail = (e.details or "").strip()
        if detail:
            log.error("Boot AP failed: %s — %s", e, detail)
        else:
            log.error("Boot AP failed: %s", e)
        _spawn_boot_ap_retry(coordinator)


def _spawn_boot_ap_retry(coordinator: LinkCoordinator) -> None:
    """If supplicant is slow past our single-shot wait, retry hotspot in the background."""

    def run() -> None:
        cfg = coordinator.cfg
        interval = 20.0
        max_attempts = 45  # spaced retries; each attempt uses a short NM wait (not full boot timeout).
        coordinator.nm.wifi_ready_timeout = min(
            45.0,
            float(cfg.wifi_ready_timeout_sec),
        )
        for attempt in range(1, max_attempts + 1):
            time.sleep(interval)
            try:
                coordinator.nm.start_hotspot(cfg.ap_ssid, cfg.ap_password)
                coordinator.ps.ap_started = True
                coordinator.store.save(coordinator.ps)
                coordinator.set_user_mode(UserMode.FORCE_AP)
                log.info(
                    "Boot AP background retry #%s succeeded SSID=%s",
                    attempt,
                    cfg.ap_ssid,
                )
                return
            except NMError as e:
                log.warning("Boot AP background retry #%s: %s", attempt, e)
        log.error("Boot AP background retries exhausted (%s attempts)", max_attempts)

    threading.Thread(target=run, name="nina-boot-ap-retry", daemon=True).start()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = load_config()
    nm: NMBackend = (
        mock_backend()
        if cfg.mock_nm
        else NMBackend(
            mock=False,
            disable_wifi_autoconnect=cfg.disable_wifi_autoconnect,
            wifi_ready_timeout=float(cfg.wifi_ready_timeout_sec),
            wifi_ready_poll=float(cfg.wifi_ready_poll_sec),
            hotspot_attempts=cfg.hotspot_attempts,
        )
    )
    coordinator = LinkCoordinator(cfg, nm)
    _maybe_boot_ap(coordinator)

    app = create_app(cfg, coordinator)
    uvicorn.run(
        app,
        host=cfg.host,
        port=cfg.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
