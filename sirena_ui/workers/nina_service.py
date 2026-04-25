"""
Thin facade around the Nina hardware controllers used by the UI.

The UI creates exactly one `NinaService`, lazily initializes the
Dynamixel bus on first use, and shares it across the playback and
record workers. All bus access is serialized via `bus_lock` so a
playback worker and a record worker can never race on the serial port.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Dict, List, Optional

from nina.config.settings import NinaSettings, load_settings
from nina.controllers.action_runner import ActionRunner
from nina.controllers.dynamixel_manager import DynamixelManager


DEFAULT_MOTOR_IDS: List[int] = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]


class NinaService:
    def __init__(self, settings: Optional[NinaSettings] = None) -> None:
        if settings is None:
            repo_root = Path(__file__).resolve().parents[2]
            settings = load_settings(repo_root)
        self.settings = settings
        self.dxl = DynamixelManager(
            serial_port=settings.serial_port,
            baudrate=settings.baudrate,
            expected_motor_ids=DEFAULT_MOTOR_IDS,
        )
        self.action_runner = ActionRunner(
            manifest_path=settings.manifest_path,
            actions_dir=settings.actions_dir,
            dxl=self.dxl,
        )
        self.bus_lock = threading.RLock()
        self._bus_ready = False
        self._motor_count = len(DEFAULT_MOTOR_IDS)

    @property
    def expected_motor_count(self) -> int:
        return self._motor_count

    def ensure_bus(self) -> Dict[str, object]:
        """Initialize the bus once, run a non-fatal health check, enable torque."""
        with self.bus_lock:
            if not self._bus_ready:
                self.dxl.initialize_bus()
                self._bus_ready = True
            health = self.dxl.run_health_check()
            self.dxl.set_torque_all(True)
            return {
                "connected": health.connected,
                "detected": health.detected_motors,
                "expected": health.expected_motors,
                "detail": health.detail,
            }

    def shutdown(self) -> None:
        with self.bus_lock:
            try:
                self.dxl.close()
            finally:
                self._bus_ready = False

    def list_actions(self) -> Dict[str, str]:
        return self.action_runner.list_actions()

    def action_path(self, name: str) -> Path:
        return self.settings.actions_dir / self.list_actions()[name]
