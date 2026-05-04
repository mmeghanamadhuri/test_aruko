"""Aggregated robot health for the companion app (mirrors desktop health strip in spirit).

Builds rows from existing nina-link bridges without instantiating a second
``NinaService`` (which would fight SLAM/vision for hardware).
"""

from __future__ import annotations

import os
import re
import shutil
from typing import Any, Dict, List

from nina.link_daemon.config import LinkDaemonConfig
from nina.link_daemon.state import LinkCoordinator

_ROW_OK = "ok"
_ROW_WARN = "warn"
_ROW_ERR = "error"
_ROW_PENDING = "pending"


def _row(
    key: str, label: str, detail: str, status: str
) -> Dict[str, str]:
    return {
        "key": key,
        "label": label,
        "detail": detail[:2000],
        "status": status,
    }


def _cpu_line() -> str:
    try:
        with open("/proc/loadavg", encoding="utf-8") as fh:
            parts = fh.read().split()
        if len(parts) >= 3:
            return f"load {parts[0]} {parts[1]} {parts[2]}"
    except OSError:
        pass
    return "n/a"


def build_robot_health(
    cfg: LinkDaemonConfig, coordinator: LinkCoordinator
) -> Dict[str, Any]:
    rows: List[Dict[str, str]] = []

    try:
        role = coordinator.effective_wifi_role()
        rows.append(
            _row(
                "wifi",
                "Wi-Fi",
                f"role={role}",
                _ROW_OK if role != "unknown" else _ROW_WARN,
            )
        )
    except Exception as exc:  # noqa: BLE001
        rows.append(_row("wifi", "Wi-Fi", str(exc)[:400], _ROW_WARN))

    last_err = (coordinator.ps.last_error or "").strip()
    if last_err:
        rows.append(
            _row("daemon", "Link daemon (last error)", last_err[:500], _ROW_WARN)
        )

    if cfg.enable_slam_bridge:
        from nina.link_daemon import slam_bridge

        try:
            slam_bridge.ensure_bridge_started()
            br = slam_bridge.get_bridge()
            if br is None:
                rows.append(
                    _row("lidar", "RPLiDAR / SLAM", "bridge unavailable", _ROW_ERR)
                )
            else:
                st = br.status()
                msg = str(st.get("lidar_message", ""))
                lc = bool(st.get("lidar_connected", False))
                if lc:
                    stat = _ROW_OK
                elif "sim" in msg.lower():
                    stat = _ROW_WARN
                else:
                    stat = _ROW_ERR
                rows.append(_row("lidar", "RPLiDAR / SLAM", msg[:500], stat))
        except Exception as exc:  # noqa: BLE001
            rows.append(_row("slam", "RPLiDAR / SLAM", str(exc)[:500], _ROW_ERR))
    else:
        rows.append(
            _row(
                "slam",
                "RPLiDAR / SLAM",
                "SLAM bridge disabled (set NINA_LINK_ENABLE_SLAM_BRIDGE=1)",
                _ROW_PENDING,
            )
        )

    if cfg.enable_robot_bridge:
        from nina.link_daemon import robot_bridge

        st = robot_bridge.navigation_hw_status()
        conn = bool(st.get("connected", False))
        msg = str(st.get("message", ""))
        rows.append(
            _row(
                "bldc",
                "BLDC drive",
                msg[:500],
                _ROW_OK if conn else _ROW_ERR,
            )
        )
    else:
        rows.append(
            _row(
                "bldc",
                "BLDC drive",
                "Robot bridge disabled",
                _ROW_PENDING,
            )
        )

    if cfg.enable_vision_bridge:
        from nina.link_daemon import vision_http

        vp = vision_http.vision_status_payload()
        if vp.get("ok"):
            cam_open = bool(vp.get("camera_open"))
            msg = str(vp.get("message", "")).strip() or (
                "streaming" if cam_open else "camera not open"
            )
            rows.append(
                _row(
                    "camera",
                    "USB camera / vision",
                    msg[:500],
                    _ROW_OK if cam_open else _ROW_WARN,
                )
            )
        else:
            rows.append(
                _row(
                    "camera",
                    "USB camera / vision",
                    str(vp.get("message", "vision error"))[:500],
                    _ROW_ERR,
                )
            )
    else:
        rows.append(
            _row(
                "camera",
                "USB camera / vision",
                "Vision bridge disabled",
                _ROW_PENDING,
            )
        )

    if cfg.enable_depth_bridge:
        from nina.link_daemon import depth_bridge

        dp = depth_bridge.status_payload()
        open_ = bool(dp.get("camera_open") or dp.get("open"))
        msg = str(dp.get("message", ""))
        rows.append(
            _row(
                "depth",
                "Depth (RealSense)",
                msg[:500],
                _ROW_OK if open_ else _ROW_WARN,
            )
        )
    else:
        rows.append(
            _row(
                "depth",
                "Depth (RealSense)",
                "Depth bridge disabled",
                _ROW_PENDING,
            )
        )

    if cfg.enable_autonomy_bridge:
        from nina.link_daemon import autonomy_bridge

        try:
            sd = autonomy_bridge.status_dict()
            h_raw = sd.get("health")
            detail = ""
            if hasattr(h_raw, "as_dict"):
                h_d = h_raw.as_dict()
                bits = []
                for key in ("lidar", "ir", "depth"):
                    blk = h_d.get(key) or {}
                    if isinstance(blk, dict):
                        bits.append(
                            f"{key}:{blk.get('connected')} {blk.get('message', '')}"
                        )
                detail = "; ".join(bits)[:500]
            en = bool(sd.get("enabled"))
            rows.append(
                _row(
                    "autonomy",
                    "Autonomy",
                    detail or f"enabled={en} mode={sd.get('mode')}",
                    _ROW_OK if en else _ROW_PENDING,
                )
            )
        except Exception as exc:  # noqa: BLE001
            rows.append(_row("autonomy", "Autonomy", str(exc)[:400], _ROW_WARN))
    else:
        rows.append(
            _row(
                "autonomy",
                "Autonomy",
                "Autonomy bridge disabled",
                _ROW_PENDING,
            )
        )

    try:
        du = shutil.disk_usage("/")
        free_gb = du.free / (1024**3)
        total_gb = du.total / (1024**3)
        warn = free_gb < max(1.0, total_gb * 0.15)
        rows.append(
            _row(
                "disk",
                "Disk",
                f"{free_gb:.1f} GB free of {total_gb:.0f} GB",
                _ROW_WARN if warn else _ROW_OK,
            )
        )
    except Exception as exc:  # noqa: BLE001
        rows.append(_row("disk", "Disk", str(exc)[:200], _ROW_WARN))

    rows.append(_row("cpu", "CPU (load avg)", _cpu_line(), _ROW_OK))

    return {
        "ok": True,
        "rows": rows,
        "source": "nina-link aggregated health (bridges + host)",
    }


def safe_map_filename(name: str) -> str:
    """Basename only; alphanumeric + dot/hyphen; default ``nina_map.pgm``."""
    raw = os.path.basename((name or "").strip())
    if not raw or ".." in raw:
        return "nina_map.pgm"
    if not re.match(r"^[\w.\-]+$", raw):
        return "nina_map.pgm"
    lower = raw.lower()
    if not lower.endswith((".pgm", ".pnm")):
        raw = raw + ".pgm"
    return raw

