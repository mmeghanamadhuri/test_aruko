"""NetworkManager access via nmcli with timeouts and structured errors."""

from __future__ import annotations

import logging
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

log = logging.getLogger("nina.link_daemon.nm")


class NMError(RuntimeError):
    """Raised when nmcli fails; ``details`` holds stderr / parsed hints."""

    def __init__(self, message: str, *, details: str = "") -> None:
        super().__init__(message)
        self.details = details


@dataclass
class SavedNetwork:
    """One saved Wi-Fi connection profile."""

    id: str  # noqa: A003 — NM uses "id" as profile name
    uuid: str
    ssid: str
    autoconnect: bool = True


@dataclass
class NMBackend:
    """Real nmcli or in-memory mock."""

    mock: bool = False
    #: New profiles from add_wifi_connection get connection.autoconnect no when True.
    disable_wifi_autoconnect: bool = True
    wifi_ready_timeout: float = 120.0
    wifi_ready_poll: float = 2.0
    hotspot_attempts: int = 5
    _mock_profiles: List[SavedNetwork] = field(default_factory=list)
    _mock_ap_active: bool = False
    _mock_sta_connected: bool = False
    _mock_current_ssid: Optional[str] = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _nmcli(self, args: List[str], timeout: float = 45.0) -> str:
        cmd = ["nmcli", *args]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if proc.returncode != 0:
            raise NMError(
                proc.stderr.strip() or "nmcli failed",
                details=proc.stderr.strip(),
            )
        return proc.stdout

    def run_lines(self, args: List[str], timeout: float = 45.0) -> List[str]:
        """Run nmcli -t and split lines."""
        if self.mock:
            return []
        out = self._nmcli(["-t", *args], timeout)
        return [ln.strip() for ln in out.splitlines() if ln.strip()]

    def device_status(self) -> Dict[str, str]:
        """Return primary wifi device name and state."""
        if self.mock:
            with self._lock:
                dev = "wlan0"
                if self._mock_ap_active:
                    return {"device": dev, "state": "connected", "mode": "ap"}
                if self._mock_sta_connected and self._mock_current_ssid:
                    return {
                        "device": dev,
                        "state": "connected",
                        "mode": "sta",
                        "ssid": self._mock_current_ssid,
                    }
                return {"device": dev, "state": "disconnected", "mode": "unknown"}

        lines = self.run_lines(["-f", "DEVICE,TYPE,STATE,CONNECTION", "device"])
        wifi_dev = None
        for ln in lines:
            parts = ln.split(":")
            if len(parts) >= 4 and parts[1] == "wifi":
                wifi_dev = parts[0]
                break
        if not wifi_dev:
            # Fall back: first wifi row
            for ln in lines:
                parts = ln.split(":")
                if len(parts) >= 2 and "wifi" in parts[1]:
                    wifi_dev = parts[0]
                    break
        if not wifi_dev:
            return {"device": "", "state": "unknown", "mode": "unknown"}
        # Only GENERAL.* fields belong to `nmcli device show`; 802-11-wireless.mode
        # is a connection property and breaks older nmcli with "invalid field".
        detail = self.run_lines(
            ["-f", "GENERAL.STATE,GENERAL.CONNECTION", "device", "show", wifi_dev],
            timeout=15,
        )
        state = "unknown"
        conn_name = ""
        for ln in detail:
            if ln.startswith("GENERAL.STATE:"):
                state = ln.split(":", 1)[1].strip()
            elif ln.startswith("GENERAL.CONNECTION:"):
                conn_name = ln.split(":", 1)[1].strip()
        mode_from_conn = ""
        if conn_name and conn_name != "--":
            mode_from_conn = self._connection_wifi_mode(conn_name)
        if mode_from_conn == "ap":
            m = "ap"
        elif mode_from_conn:
            m = "sta"
        else:
            m = "unknown"
        return {
            "device": wifi_dev,
            "state": state,
            "mode": m,
            "connection": conn_name,
        }

    def list_saved_wifi(self) -> List[SavedNetwork]:
        if self.mock:
            with self._lock:
                return list(self._mock_profiles)

        raw = subprocess.run(
            [
                "nmcli",
                "-t",
                "-f",
                "NAME,UUID,TYPE,AUTOCONNECT",
                "connection",
                "show",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if raw.returncode != 0:
            log.warning("nmcli connection show failed: %s", raw.stderr)
            return []
        out: List[SavedNetwork] = []
        for ln in raw.stdout.splitlines():
            parts = ln.split(":")
            if len(parts) < 4:
                continue
            name, uuid, typ, autoconn = parts[0], parts[1], parts[2], parts[3]
            if typ != "802-11-wireless":
                continue
            ssid = self._connection_ssid(uuid)
            out.append(
                SavedNetwork(
                    id=name,
                    uuid=uuid,
                    ssid=ssid or name,
                    autoconnect=autoconn.lower() == "yes",
                )
            )
        return out

    def _connection_ssid(self, uuid: str) -> str:
        r = subprocess.run(
            [
                "nmcli",
                "-g",
                "802-11-wireless.ssid",
                "connection",
                "show",
                uuid,
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if r.returncode != 0:
            return ""
        return r.stdout.strip()

    def _connection_wifi_mode(self, uuid: str) -> str:
        r = subprocess.run(
            [
                "nmcli",
                "-g",
                "802-11-wireless.mode",
                "connection",
                "show",
                uuid,
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if r.returncode != 0:
            return ""
        return (r.stdout.strip() or "infrastructure").lower()

    def add_wifi_connection(
        self,
        ssid: str,
        password: str,
        *,
        id_hint: Optional[str] = None,
    ) -> SavedNetwork:
        """Create or update a WPA2 personal profile."""
        if self.mock:
            with self._lock:
                cid = id_hint or f"wifi-{ssid}"
                u = f"mock-uuid-{len(self._mock_profiles)}"
                sn = SavedNetwork(
                    id=cid,
                    uuid=u,
                    ssid=ssid,
                    autoconnect=not self.disable_wifi_autoconnect,
                )
                self._mock_profiles = [p for p in self._mock_profiles if p.ssid != ssid]
                self._mock_profiles.append(sn)
                return sn

        name = id_hint or f"nina-{re.sub(r'[^a-zA-Z0-9_-]', '-', ssid)[:24]}"
        args = [
            "connection",
            "add",
            "type",
            "wifi",
            "con-name",
            name,
            "ssid",
            ssid,
            "wifi-sec.key-mgmt",
            "wpa-psk",
            "wifi-sec.psk",
            password,
        ]
        if self.disable_wifi_autoconnect:
            args += ["connection.autoconnect", "no"]
        proc = subprocess.run(
            ["nmcli", *args],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if proc.returncode != 0:
            raise NMError(
                "Failed to save Wi-Fi profile",
                details=proc.stderr.strip() or proc.stdout.strip(),
            )
        # Resolve UUID
        saved = self.list_saved_wifi()
        for p in saved:
            if p.id == name:
                return p
        raise NMError("Profile added but not listed", details=proc.stdout)

    def delete_connection(self, uuid_or_id: str) -> None:
        if self.mock:
            with self._lock:
                self._mock_profiles = [
                    p
                    for p in self._mock_profiles
                    if p.uuid != uuid_or_id and p.id != uuid_or_id
                ]
            return
        proc = subprocess.run(
            ["nmcli", "connection", "delete", uuid_or_id],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if proc.returncode != 0:
            raise NMError(
                "Failed to delete profile",
                details=proc.stderr.strip(),
            )

    def activate_connection(self, uuid_or_id: str, timeout: float = 90.0) -> None:
        if self.mock:
            with self._lock:
                for p in self._mock_profiles:
                    if p.uuid == uuid_or_id or p.id == uuid_or_id:
                        self._mock_sta_connected = True
                        self._mock_ap_active = False
                        self._mock_current_ssid = p.ssid
                        return
            raise NMError("Unknown profile")

        proc = subprocess.run(
            ["nmcli", "connection", "up", uuid_or_id],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if proc.returncode != 0:
            err = proc.stderr.strip() or proc.stdout.strip()
            if _looks_like_bad_password(err):
                raise NMError(
                    "Wi-Fi authentication failed (check password)",
                    details=err,
                )
            raise NMError("Could not connect", details=err)

    def disable_autoconnect_all_saved_wifi(self) -> None:
        """Set connection.autoconnect no on every saved 802-11 profile (STAs won't auto-join on boot)."""
        if self.mock:
            return
        for p in self.list_saved_wifi():
            subprocess.run(
                [
                    "nmcli",
                    "connection",
                    "modify",
                    p.uuid,
                    "connection.autoconnect",
                    "no",
                ],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )

    def _connection_id(self, uuid: str) -> str:
        r = subprocess.run(
            ["nmcli", "-g", "connection.id", "connection", "show", uuid],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if r.returncode != 0:
            return ""
        return r.stdout.strip()

    def active_wifi_station_info(self) -> Dict[str, Optional[str]]:
        """Return SSID + NM profile name when connected as STA (not hotspot/AP profile)."""
        if self.mock:
            with self._lock:
                if self._mock_sta_connected and self._mock_current_ssid:
                    return {
                        "ssid": self._mock_current_ssid,
                        "profile_name": "mock-wifi",
                    }
                return {"ssid": None, "profile_name": None}

        raw = subprocess.run(
            ["nmcli", "-t", "-f", "UUID,TYPE", "connection", "show", "--active"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if raw.returncode != 0:
            return {"ssid": None, "profile_name": None}

        for ln in raw.stdout.splitlines():
            if ":" not in ln:
                continue
            uuid, typ = ln.split(":", 1)
            typ = typ.strip()
            if typ != "802-11-wireless":
                continue
            mode = self._connection_wifi_mode(uuid)
            if mode == "ap":
                continue
            ssid = self._connection_ssid(uuid).strip()
            prof = self._connection_id(uuid).strip()
            return {
                "ssid": ssid or None,
                "profile_name": prof or None,
            }

        return {"ssid": None, "profile_name": None}

    def start_hotspot(
        self,
        ssid: str,
        password: str,
        *,
        ifname: Optional[str] = None,
        timeout: float = 60.0,
    ) -> None:
        """Bring up an AP using NetworkManager hotspot (Jetson / Ubuntu)."""
        if self.mock:
            with self._lock:
                self._mock_ap_active = True
                self._mock_sta_connected = False
                self._mock_current_ssid = ssid
            return

        dev = self.wait_for_wifi_ready(ifname=ifname)
        if not dev:
            raise NMError("No Wi-Fi device found for hotspot")

        attempts = max(1, int(self.hotspot_attempts))
        last_details = ""
        for attempt in range(attempts):
            # Drop any STA session so the radio can become an AP.
            self.disconnect_device(timeout=min(timeout, 30.0))

            proc = subprocess.run(
                [
                    "nmcli",
                    "device",
                    "wifi",
                    "hotspot",
                    "ifname",
                    dev,
                    "ssid",
                    ssid,
                    "password",
                    password,
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            if proc.returncode == 0:
                return
            last_details = proc.stderr.strip() or proc.stdout.strip()
            if attempt < attempts - 1:
                time.sleep(max(0.5, float(self.wifi_ready_poll)))

        raise NMError(
            "Hotspot failed (is NetworkManager managing Wi-Fi?)",
            details=last_details,
        )

    def disconnect_device(self, *, timeout: float = 30.0) -> None:
        if self.mock:
            with self._lock:
                self._mock_sta_connected = False
                self._mock_ap_active = False
                self._mock_current_ssid = None
            return
        dev = self._wifi_device_name()
        if not dev:
            return
        subprocess.run(
            ["nmcli", "device", "disconnect", dev],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    def _wifi_device_name(self) -> str:
        st = self.device_status()
        return st.get("device") or ""

    def _general_state(self, dev: str) -> str:
        if not dev or self.mock:
            return ""
        r = subprocess.run(
            ["nmcli", "-g", "GENERAL.STATE", "device", "show", dev],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if r.returncode != 0:
            return ""
        return (r.stdout or "").strip()

    @staticmethod
    def _nm_state_ready_for_hotspot(state: str) -> bool:
        """NM 10=unmanaged, 20=unavailable (supplicant not ready); need a later state."""
        if not state:
            return False
        return not (state.startswith("10 ") or state.startswith("20 "))

    def wait_for_wifi_ready(self, *, ifname: Optional[str] = None) -> str:
        """Block until Wi-Fi is past unmanaged/unavailable or timeout."""
        if self.mock:
            return ifname or "wlan0"
        deadline = time.monotonic() + max(5.0, float(self.wifi_ready_timeout))
        interval = max(0.5, float(self.wifi_ready_poll))
        last_state = ""
        while True:
            dev = (ifname or "").strip() or self._wifi_device_name()
            if dev:
                last_state = self._general_state(dev)
                if self._nm_state_ready_for_hotspot(last_state):
                    return dev
            if time.monotonic() >= deadline:
                hint = last_state or "no Wi-Fi device"
                raise NMError(
                    "Wi-Fi not ready for hotspot (supplicant still starting?)",
                    details=hint,
                )
            time.sleep(interval)

    def get_ipv4_address(self, interface_hint: Optional[str] = None) -> Optional[str]:
        if self.mock:
            with self._lock:
                if self._mock_ap_active:
                    return "192.168.4.1"
                if self._mock_sta_connected:
                    return "192.168.1.100"
            return "127.0.0.1"

        iface = interface_hint or self._wifi_device_name()
        if not iface:
            return None
        r = subprocess.run(
            ["nmcli", "-g", "IP4.ADDRESS", "device", "show", iface],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if r.returncode != 0:
            return None
        for ln in r.stdout.splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("/"):
                # Often "192.168.1.5/24"
                return ln.split("/")[0]
        return None


def _looks_like_bad_password(stderr: str) -> bool:
    s = stderr.lower()
    return any(
        x in s
        for x in (
            "secrets",
            "802-1x",
            "psk",
            "authentication failed",
            "no secrets",
        )
    )


def mock_backend() -> NMBackend:
    return NMBackend(mock=True)
