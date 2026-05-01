"""Entry point: `python -m nina.link_daemon.main` or uvicorn nina.link_daemon.api:create_app (factory)."""

from __future__ import annotations

import logging
import os
import sys

import uvicorn

from nina.link_daemon.api import create_app
from nina.link_daemon.config import load_config
from nina.link_daemon.nm import NMBackend, mock_backend
from nina.link_daemon.nm import NMError
from nina.link_daemon.state import LinkCoordinator

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
        log.error("Boot AP failed: %s", e)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = load_config()
    nm: NMBackend = (
        mock_backend()
        if cfg.mock_nm
        else NMBackend(mock=False, disable_wifi_autoconnect=cfg.disable_wifi_autoconnect)
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
