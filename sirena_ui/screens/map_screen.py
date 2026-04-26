"""Map / SLAM screen.

Shows the live BreezySLAM occupancy grid (or a simulation banner when
the lidar isn't connected), per-sensor health pills, the current
robot pose, and the single 'Autonomous nav' toggle that arms the
sensor stack + the autonomous pilot at the same time.

Wiring:

  * `service.slam`     - SlamWorker; emits snapshot/status updates as
                         soon as `start()` is called.
  * `service.autonomy` - AutonomyController; the toggle below switches
                         it on/off. When on, it also makes sure the
                         SLAM worker is running (lidar is shared).

The screen survives missing hardware: if the SLAM worker reports the
lidar is disconnected we keep the grid view in placeholder mode and
the pills clearly say 'simulation'.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from sirena_ui.widgets.common import (
    Breadcrumb,
    Card,
    CardTitle,
    MutedLabel,
    Pill,
    SectionLabel,
)
from sirena_ui.widgets.occupancy_grid_view import OccupancyGridView
from sirena_ui.workers.nina_service import NinaService


log = logging.getLogger("sirena_ui.map_screen")


class MapScreen(QWidget):
    def __init__(self, service: NinaService, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._slam = service.slam
        self._autonomy = service.autonomy

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(14)

        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(Breadcrumb("Nina", "Map (SLAM)"))
        top.addStretch(1)
        self._sensor_pill = Pill("Sensors idle", Pill.KIND_NEUTRAL)
        top.addWidget(self._sensor_pill)
        self._slam_pill = Pill("SLAM idle", Pill.KIND_NEUTRAL)
        top.addWidget(self._slam_pill)
        outer.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(16)
        outer.addLayout(body, stretch=1)

        body.addWidget(self._build_map_card(), stretch=62)
        body.addWidget(self._build_side_card(), stretch=38)

        self._wire_signals()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_map_card(self) -> Card:
        card = Card(padding=16, spacing=10)

        header = QHBoxLayout()
        card.add_layout(header)
        header.addWidget(CardTitle("Occupancy map"))
        header.addStretch(1)
        self._map_pill = Pill("waiting for first scan", Pill.KIND_NEUTRAL)
        header.addWidget(self._map_pill)

        self._grid = OccupancyGridView()
        card.add(self._grid, stretch=1)

        legend = QHBoxLayout()
        legend.setSpacing(12)
        card.add_layout(legend)
        for color, text in [
            ("#c8102e", "Nina"),
            ("#1c1c1e", "Wall"),
            ("#d1d1d6", "Free space"),
            ("#8e8e93", "Unknown"),
        ]:
            chip = QFrame()
            chip.setStyleSheet("background-color: transparent;")
            row = QHBoxLayout(chip)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            dot = QLabel("\u25CF")
            dot.setStyleSheet(
                f"color: {color}; font-size: 12px; background-color: transparent;"
            )
            row.addWidget(dot)
            label = QLabel(text)
            label.setStyleSheet(
                "color: #6e6e73; font-size: 12px; background-color: transparent;"
            )
            row.addWidget(label)
            legend.addWidget(chip)
        legend.addStretch(1)
        return card

    def _build_side_card(self) -> Card:
        card = Card(padding=20, spacing=12)

        # ---- Autonomous nav toggle (the headline control) ----
        card.add(SectionLabel("Autonomous nav"))
        self._autonomy_btn = QPushButton("Autonomous mode: OFF")
        self._autonomy_btn.setObjectName("primaryButton")
        self._autonomy_btn.setCursor(Qt.PointingHandCursor)
        self._autonomy_btn.setCheckable(True)
        self._autonomy_btn.toggled.connect(self._on_autonomy_toggle)
        card.add(self._autonomy_btn)

        self._autonomy_status = MutedLabel(
            "When ON: lidar + SLAM + obstacle avoidance start, "
            "and Nina drives herself while reactively avoiding obstacles."
        )
        self._autonomy_status.setWordWrap(True)
        card.add(self._autonomy_status)

        # ---- Mapping-only controls (SLAM without autonomy) ----
        card.add(SectionLabel("Mapping"))
        row1 = QHBoxLayout()
        row1.setSpacing(8)
        card.add_layout(row1)

        self._map_btn = QPushButton("Start mapping")
        self._map_btn.setObjectName("secondaryButton")
        self._map_btn.setCursor(Qt.PointingHandCursor)
        self._map_btn.setCheckable(True)
        self._map_btn.toggled.connect(self._on_map_toggle)
        row1.addWidget(self._map_btn)

        save_btn = QPushButton("Save map")
        save_btn.setObjectName("secondaryButton")
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.clicked.connect(self._on_save_map)
        row1.addWidget(save_btn)

        clear_btn = QPushButton("Clear")
        clear_btn.setObjectName("secondaryButton")
        clear_btn.setCursor(Qt.PointingHandCursor)
        clear_btn.clicked.connect(self._on_clear_map)
        row1.addWidget(clear_btn)

        # ---- Sensor health row ----
        card.add(SectionLabel("Sensor health"))
        chips_row = QHBoxLayout()
        chips_row.setSpacing(6)
        card.add_layout(chips_row)
        self._lidar_chip = Pill("Lidar -", Pill.KIND_NEUTRAL)
        self._depth_chip = Pill("Depth -", Pill.KIND_NEUTRAL)
        self._ir_chip = Pill("IR -", Pill.KIND_NEUTRAL)
        self._ultra_chip = Pill("Ultra -", Pill.KIND_NEUTRAL)
        for chip in (self._lidar_chip, self._depth_chip,
                     self._ir_chip, self._ultra_chip):
            chips_row.addWidget(chip)
        chips_row.addStretch(1)

        # ---- Pose readout ----
        card.add(SectionLabel("Pose"))
        self._pose_label = QLabel("x: \u2014\ny: \u2014\n\u03b8: \u2014")
        self._pose_label.setStyleSheet(
            "background-color: #f5f5f7; border-radius: 8px; padding: 10px;"
            " font-family: Menlo, monospace; color: #1c1c1e;"
        )
        card.add(self._pose_label)

        # ---- Pilot decision readout ----
        card.add(SectionLabel("Pilot"))
        self._pilot_label = QLabel("idle")
        self._pilot_label.setStyleSheet(
            "background-color: #f5f5f7; border-radius: 8px; padding: 10px;"
            " font-family: Menlo, monospace; color: #1c1c1e;"
        )
        self._pilot_label.setWordWrap(True)
        card.add(self._pilot_label)

        card.add_stretch()
        return card

    # ------------------------------------------------------------------
    # Signals
    # ------------------------------------------------------------------

    def _wire_signals(self) -> None:
        self._slam.snapshot_changed.connect(self._on_slam_snapshot_meta)
        self._slam.pose_changed.connect(self._on_pose)
        self._slam.status_changed.connect(self._on_slam_status)

        self._autonomy.enabled_changed.connect(self._on_autonomy_enabled)
        self._autonomy.pilot_state_changed.connect(self._on_pilot_state)
        self._autonomy.sensor_health_changed.connect(self._on_sensor_health)

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    def on_enter(self) -> None:
        # Start SLAM passively so the user sees a live map even if they
        # never turn on autonomy. If the lidar is missing the worker
        # surfaces that through status_changed and we render a clean
        # placeholder.
        self._slam.start()
        self._refresh_map_btn_state()

    def on_leave(self) -> None:
        # We don't close SLAM on leave - the autonomy controller may
        # still need it. The service-wide shutdown handles teardown.
        pass

    # ------------------------------------------------------------------
    # Slots: SLAM signals
    # ------------------------------------------------------------------

    def _on_slam_snapshot_meta(self, meta: dict) -> None:
        # The full grid bytes aren't on the signal; pull the latest
        # snapshot from the worker so the QImage stays in sync without
        # blowing up the Qt event queue.
        snap = self._slam.latest_snapshot()
        if snap is None:
            return
        self._grid.set_grid(
            snap.grid_bytes,
            snap.width,
            snap.height,
            snap.scale_mm_per_px,
        )
        self._grid.set_pose(
            snap.pose.x_mm, snap.pose.y_mm, snap.pose.theta_deg
        )
        when = meta.get("updated_at")
        if when:
            age = max(0.0, time.monotonic() - float(when))
            self._map_pill.setText(f"updated {age:.1f}s ago")
            self._map_pill.set_kind(Pill.KIND_OK)

    def _on_pose(self, pose: dict) -> None:
        x = pose.get("x_mm", 0.0)
        y = pose.get("y_mm", 0.0)
        theta = pose.get("theta_deg", 0.0)
        self._pose_label.setText(
            f"x: {x:>7.1f} mm\ny: {y:>7.1f} mm\n\u03b8: {theta:>6.1f}\u00b0"
        )

    def _on_slam_status(self, status: dict) -> None:
        connected = bool(status.get("lidar_connected"))
        message = str(status.get("lidar_message", ""))
        running = bool(status.get("running"))
        fallback = bool(status.get("slam_fallback"))
        slam_msg = str(status.get("slam_message", ""))

        if connected and running:
            self._lidar_chip.setText(f"Lidar OK")
            self._lidar_chip.set_kind(Pill.KIND_OK)
        elif running:
            self._lidar_chip.setText("Lidar sim")
            self._lidar_chip.set_kind(Pill.KIND_WARN)
        else:
            self._lidar_chip.setText("Lidar -")
            self._lidar_chip.set_kind(Pill.KIND_NEUTRAL)

        if not running:
            self._slam_pill.setText("SLAM idle")
            self._slam_pill.set_kind(Pill.KIND_NEUTRAL)
        elif fallback:
            self._slam_pill.setText(f"SLAM fallback - {slam_msg or 'no breezyslam'}")
            self._slam_pill.set_kind(Pill.KIND_WARN)
        else:
            self._slam_pill.setText(f"SLAM live - {status.get('scans_processed', 0)} scans")
            self._slam_pill.set_kind(Pill.KIND_OK)

        if not connected and running:
            self._grid.set_placeholder(
                f"Lidar simulation: {message}\n"
                "Connect the RPLIDAR A1 to /dev/ttyUSB0 to start mapping."
            )

    # ------------------------------------------------------------------
    # Slots: Autonomy signals
    # ------------------------------------------------------------------

    def _on_autonomy_enabled(self, on: bool) -> None:
        self._autonomy_btn.blockSignals(True)
        self._autonomy_btn.setChecked(on)
        self._autonomy_btn.setText(
            f"Autonomous mode: {'ON' if on else 'OFF'}"
        )
        self._autonomy_btn.blockSignals(False)
        if on:
            self._autonomy_status.setText(
                "Autonomous mode is active. Nina is steering herself; "
                "manual D-pad on the Drive screen is disabled."
            )
            self._sensor_pill.setText("Autonomy engaged")
            self._sensor_pill.set_kind(Pill.KIND_OK)
        else:
            self._autonomy_status.setText(
                "When ON: lidar + SLAM + obstacle avoidance start, "
                "and Nina drives herself while reactively avoiding obstacles."
            )
            self._sensor_pill.setText("Autonomy off")
            self._sensor_pill.set_kind(Pill.KIND_NEUTRAL)
        self._refresh_map_btn_state()

    def _on_pilot_state(self, state: dict) -> None:
        action = state.get("last_action", "idle")
        reason = state.get("last_reason", "")
        running = state.get("running", False)
        prefix = "running" if running else "stopped"
        self._pilot_label.setText(f"{prefix} \u2192 {action}\n{reason}")

    def _on_sensor_health(self, health: dict) -> None:
        self._update_chip(self._depth_chip, "Depth", health.get("depth", {}))
        self._update_chip(self._ir_chip, "IR", health.get("ir", {}))

        ultras = health.get("ultrasonic") or []
        connected_count = sum(1 for u in ultras if u.get("connected"))
        if not ultras:
            self._ultra_chip.setText("Ultra -")
            self._ultra_chip.set_kind(Pill.KIND_NEUTRAL)
        elif connected_count == len(ultras):
            self._ultra_chip.setText(f"Ultra {connected_count}/{len(ultras)}")
            self._ultra_chip.set_kind(Pill.KIND_OK)
        elif connected_count == 0:
            self._ultra_chip.setText("Ultra sim")
            self._ultra_chip.set_kind(Pill.KIND_WARN)
        else:
            self._ultra_chip.setText(f"Ultra {connected_count}/{len(ultras)}")
            self._ultra_chip.set_kind(Pill.KIND_WARN)

    @staticmethod
    def _update_chip(pill: Pill, label: str, payload: dict) -> None:
        connected = bool(payload.get("connected"))
        message = str(payload.get("message") or "")
        if connected:
            pill.setText(f"{label} OK")
            pill.set_kind(Pill.KIND_OK)
        elif message and ("not installed" in message
                          or "not present" in message
                          or "disabled" in message):
            pill.setText(f"{label} sim")
            pill.set_kind(Pill.KIND_WARN)
        else:
            pill.setText(f"{label} off")
            pill.set_kind(Pill.KIND_NEUTRAL)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _on_autonomy_toggle(self, on: bool) -> None:
        try:
            self._autonomy.set_enabled(on)
        except Exception as exc:
            log.exception("autonomy.set_enabled(%s) failed: %s", on, exc)
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.critical(
                self,
                "Autonomous mode failed",
                f"Could not toggle autonomy: {exc}",
            )

    def _on_map_toggle(self, on: bool) -> None:
        try:
            if on:
                self._slam.start()
                self._map_btn.setText("Stop mapping")
            else:
                # Don't yank the lidar out from under autonomy.
                if self._autonomy.is_enabled():
                    self._map_btn.blockSignals(True)
                    self._map_btn.setChecked(True)
                    self._map_btn.blockSignals(False)
                    self._map_btn.setText("Stop mapping")
                    return
                self._slam.stop()
                self._map_btn.setText("Start mapping")
        except Exception as exc:
            log.exception("slam start/stop failed: %s", exc)

    def _on_save_map(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save occupancy map",
            "nina_map.pgm", "PGM image (*.pgm)",
        )
        if not path:
            return
        ok = self._slam.save_map(Path(path))
        from PyQt5.QtWidgets import QMessageBox
        if ok:
            QMessageBox.information(self, "Map saved", f"Saved to:\n{path}")
        else:
            QMessageBox.warning(
                self, "Save failed",
                "No map data yet, or write failed - check the log.",
            )

    def _on_clear_map(self) -> None:
        # Reset the engine: stop -> start re-initialises the bytemap.
        was_auto = self._autonomy.is_enabled()
        try:
            if was_auto:
                self._autonomy.set_enabled(False)
            self._slam.stop()
            self._slam.start()
        finally:
            self._grid.clear()
            self._grid.set_placeholder(
                "Map cleared. Drive Nina around to rebuild it."
            )

    def _refresh_map_btn_state(self) -> None:
        running = bool(self._slam.status().get("running"))
        self._map_btn.blockSignals(True)
        self._map_btn.setChecked(running)
        self._map_btn.setText("Stop mapping" if running else "Start mapping")
        self._map_btn.blockSignals(False)
