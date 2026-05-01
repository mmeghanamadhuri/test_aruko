"""Persistent state and coordination: boot window, client seen, user mode preference."""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from nina.link_daemon.config import LinkDaemonConfig
from nina.link_daemon.nm import NMBackend, NMError, SavedNetwork

log = logging.getLogger("nina.link_daemon.state")


class UserMode(str, Enum):
    """High-level user / policy intent (not the same as radio mode)."""

    BOOT_DEFAULT = "boot_default"  # AP first, respect boot timer messaging
    FORCE_AP = "force_ap"
    FORCE_STA = "force_sta"


@dataclass
class PersistedState:
    version: int = 1
    user_mode: str = UserMode.BOOT_DEFAULT.value
    session_token: Optional[str] = None
    pairing_pin: Optional[str] = None
    boot_started_monotonic: float = 0.0
    client_seen: bool = False
    client_ips: List[str] = field(default_factory=list)
    last_error: str = ""
    ap_started: bool = False

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> "PersistedState":
        return cls(
            version=int(data.get("version", 1)),
            user_mode=str(data.get("user_mode", UserMode.BOOT_DEFAULT.value)),
            session_token=data.get("session_token"),
            pairing_pin=data.get("pairing_pin"),
            boot_started_monotonic=float(data.get("boot_started_monotonic", 0)),
            client_seen=bool(data.get("client_seen", False)),
            client_ips=list(data.get("client_ips", [])),
            last_error=str(data.get("last_error", "")),
            ap_started=bool(data.get("ap_started", False)),
        )


class StateStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    def load(self) -> PersistedState:
        with self._lock:
            if not self._path.exists():
                return PersistedState(
                    boot_started_monotonic=time.monotonic(),
                )
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                return PersistedState.from_json(raw)
            except (OSError, json.JSONDecodeError) as e:
                log.warning("State load failed: %s — using defaults", e)
                return PersistedState(boot_started_monotonic=time.monotonic())

    def save(self, state: PersistedState) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state.to_json(), indent=2), encoding="utf-8")
            tmp.replace(self._path)


class LinkCoordinator:
    """Owns NM backend, persisted flags, and recent client IPs from HTTP."""

    def __init__(self, cfg: LinkDaemonConfig, nm: NMBackend) -> None:
        self.cfg = cfg
        self.nm = nm
        self.store = StateStore(cfg.state_path)
        self.ps = self.store.load()
        self._http_lock = threading.Lock()
        self._recent_client_ips: Set[str] = set()
        if self.ps.boot_started_monotonic <= 0:
            self.ps.boot_started_monotonic = time.monotonic()
            self.store.save(self.ps)

    def record_http_client(self, ip: Optional[str]) -> None:
        if not ip or ip in ("127.0.0.1", "::1"):
            return
        with self._http_lock:
            self._recent_client_ips.add(ip)
            self.ps.client_seen = True
            if ip not in self.ps.client_ips:
                self.ps.client_ips.append(ip)
                self.ps.client_ips = self.ps.client_ips[-16:]
            self.store.save(self.ps)

    def boot_wait_remaining_sec(self) -> int:
        elapsed = time.monotonic() - self.ps.boot_started_monotonic
        left = int(self.cfg.ap_wait_sec - elapsed)
        return max(0, left)

    def user_mode_enum(self) -> UserMode:
        try:
            return UserMode(self.ps.user_mode)
        except ValueError:
            return UserMode.BOOT_DEFAULT

    def set_user_mode(self, mode: UserMode) -> None:
        self.ps.user_mode = mode.value
        self.ps.last_error = ""
        self.store.save(self.ps)

    def set_session_token(self, token: Optional[str]) -> None:
        self.ps.session_token = token
        self.store.save(self.ps)

    def issue_session_token(self) -> str:
        tok = str(uuid.uuid4())
        self.ps.session_token = tok
        self.store.save(self.ps)
        return tok

    def clear_error(self) -> None:
        self.ps.last_error = ""
        self.store.save(self.ps)

    def set_error(self, msg: str) -> None:
        self.ps.last_error = msg[:2000]
        self.store.save(self.ps)

    def effective_wifi_role(self) -> str:
        """Return ``ap``, ``sta``, or ``unknown`` from NM."""
        try:
            st = self.nm.device_status()
            return st.get("mode", "unknown")
        except NMError as e:
            log.warning("device_status: %s", e)
            return "unknown"

    def refresh_saved_networks(self) -> List[SavedNetwork]:
        try:
            return self.nm.list_saved_wifi()
        except NMError as e:
            self.set_error(str(e))
            return []

    def saved_networks_public(self) -> List[Dict[str, Any]]:
        out = []
        for p in self.refresh_saved_networks():
            out.append(
                {
                    "id": p.id,
                    "uuid": p.uuid,
                    "ssid": p.ssid,
                    "autoconnect": p.autoconnect,
                }
            )
        return out
