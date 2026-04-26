"""
Lightweight stub for the BLDC drive controller.

The actual hardware integration (UART/CAN/PWM) is being landed
separately. This stub exposes the same surface the UI expects,
keeps an in-process state, and emits signals so the future
real driver can be a drop-in replacement.
"""

from __future__ import annotations

from PyQt5.QtCore import QObject, pyqtSignal


class DriveStub(QObject):
    state_changed = pyqtSignal(dict)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._state = {
            "connected": False,
            "speed_pct": 15,
            "direction": "idle",
            "brake": True,
            "reverse": False,
            "heading_deg": 0,
            "distance_m": 0.0,
        }

    @property
    def connected(self) -> bool:
        return bool(self._state["connected"])

    def state(self) -> dict:
        return dict(self._state)

    # ---- mutators ----

    def set_speed(self, pct: int) -> None:
        self._state["speed_pct"] = max(0, min(100, int(pct)))
        self.state_changed.emit(self.state())

    def set_brake(self, on: bool) -> None:
        self._state["brake"] = bool(on)
        if on:
            self._state["direction"] = "idle"
        self.state_changed.emit(self.state())

    def set_reverse(self, on: bool) -> None:
        self._state["reverse"] = bool(on)
        self.state_changed.emit(self.state())

    def drive(self, direction: str) -> None:
        if self._state["brake"]:
            return
        self._state["direction"] = direction
        self.state_changed.emit(self.state())

    def stop(self) -> None:
        self._state["direction"] = "idle"
        self.state_changed.emit(self.state())
