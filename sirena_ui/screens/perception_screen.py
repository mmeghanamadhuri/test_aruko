"""Perception screen: live view of every forward-looking sensor.

Three side-by-side panes show what Nina sees right now:

  +---------+-----------+------------+
  | Lidar   | RGB       | Depth      |
  | (SLAM   | (USB cam) | (RealSense |
  |  grid)  |           |  D435 JET) |
  +---------+-----------+------------+
  | Autonomous mode toggle + status  |
  +----------------------------------+

The screen is intentionally read-only - the only control is the
autonomous-mode toggle (mirrored on Map and Drive screens). All three
sensor pipes are owned elsewhere:

  * Lidar / SLAM grid - `service.slam` (SlamWorker, started lazily).
  * RGB - `service.vision` (VisionWorker, refcount-acquired so the
    same feed the Drive / Vision screens use stays alive).
  * Depth - `service.autonomy` (AutonomyController owns the D435
    pipeline; `acquire_depth()` opens it for visualization without
    requiring the autonomy hot loop to be on).

Falls back gracefully when hardware is missing: each pane shows a
clear placeholder ("Lidar not connected", "USB camera not connected",
"Depth camera not connected") and the autonomy toggle still works
on whichever sensors did open. We don't lazy-load the screen with
"all hardware required" gating because this IS the screen the
operator opens to figure out which sensor isn't coming up.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
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


log = logging.getLogger("sirena_ui.perception_screen")


# Depth poll cadence. The realsense worker thread publishes at the
# camera's native rate (15 fps default), but pulling that many UI
# repaints across three panes saturates the Qt main thread on Jetson
# Nano. 8 Hz is fast enough that operator scene changes feel live and
# slow enough that we don't fight the RGB stream for paint cycles.
_DEPTH_POLL_HZ = 8


class PerceptionScreen(QWidget):
    def __init__(self, service: NinaService, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._slam = service.slam
        self._autonomy = service.autonomy

        # Lifecycle bookkeeping for the resources we acquire on enter
        # and release on leave. Tracked as flags (not refcounts) so a
        # double-enter / double-leave can't cause underflow on the
        # underlying worker refcounts.
        self._holds_camera = False
        self._holds_depth = False
        self._depth_open_ok = False
        self._depth_open_msg = ""

        self._depth_image_label: Optional[QLabel] = None
        self._depth_overlay_label: Optional[QLabel] = None
        self._depth_placeholder: Optional[QWidget] = None
        self._cam_image_label: Optional[QLabel] = None
        self._cam_placeholder: Optional[QWidget] = None
        self._grid: Optional[OccupancyGridView] = None

        # We hold a strong reference to the BGR888 buffer that backs
        # the latest depth QImage so QImage's "buffer must outlive
        # me" requirement is satisfied even when the next poll
        # replaces the buffer mid-paint.
        self._last_depth_buf: Optional[bytes] = None

        outer = QVBoxLayout(self)
        # 10 / 8 trim from the dev default 20 / 14 to fit the 1024 x
        # 600 panel without scrollbars.
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(Breadcrumb("Nina", "Perception"))
        top.addStretch(1)
        self._auto_pill = Pill("Autonomous: OFF", Pill.KIND_NEUTRAL)
        top.addWidget(self._auto_pill)
        self._lidar_chip = Pill("Lidar -", Pill.KIND_NEUTRAL)
        top.addWidget(self._lidar_chip)
        self._cam_chip = Pill("Cam -", Pill.KIND_NEUTRAL)
        top.addWidget(self._cam_chip)
        self._depth_chip = Pill("Depth -", Pill.KIND_NEUTRAL)
        top.addWidget(self._depth_chip)
        outer.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(8)
        outer.addLayout(body, stretch=1)
        body.addWidget(self._build_lidar_card(), stretch=1)
        body.addWidget(self._build_camera_card(), stretch=1)
        body.addWidget(self._build_depth_card(), stretch=1)

        outer.addLayout(self._build_footer())

        self._wire_signals()

        # Poll timer for depth - we don't want a per-frame Qt signal
        # crossing the thread boundary at 15+ Hz from the realsense
        # worker, so we sample at _DEPTH_POLL_HZ from the GUI side
        # via QTimer (also lets us throttle independently of camera
        # FPS without touching the driver).
        self._depth_timer = QTimer(self)
        self._depth_timer.setInterval(int(1000.0 / _DEPTH_POLL_HZ))
        self._depth_timer.timeout.connect(self._poll_depth)

    # ------------------------------------------------------------------
    # Layout builders
    # ------------------------------------------------------------------

    def _build_lidar_card(self) -> Card:
        card = Card(padding=8, spacing=4)
        header = QHBoxLayout()
        header.setSpacing(6)
        card.add_layout(header)
        header.addWidget(CardTitle("LiDAR"))
        header.addStretch(1)
        self._lidar_pill = Pill("waiting", Pill.KIND_NEUTRAL)
        header.addWidget(self._lidar_pill)

        # Wrap the grid in a cardSubtle viewport, exactly like the
        # RGB and Depth panes. Without this wrapper the grid was
        # the only direct child in its column, and the Qt layout
        # treated its sizeHint as authoritative - on the 1/3-width
        # Perception column that translated to a tiny 200x200 grid
        # in the top-left of the card while RGB and Depth filled
        # their viewports normally. Wrapping puts all three panes
        # on the same min-height (220) and Expanding policy contract
        # so the layout treats the lidar pane consistently with the
        # others.
        viewport = QFrame()
        viewport.setObjectName("cardSubtle")
        viewport.setMinimumHeight(220)
        viewport.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        v = QVBoxLayout(viewport)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self._grid = OccupancyGridView(viewport)
        v.addWidget(self._grid, stretch=1)
        card.add(viewport, stretch=1)
        # Trailing description label was eating ~30 px of vertical
        # space the grid actually needed; the title + pill in the
        # header carry the meaning. Drop it here (Map screen still
        # has the full legend right under the grid).
        return card

    def _build_camera_card(self) -> Card:
        card = Card(padding=8, spacing=4)
        header = QHBoxLayout()
        header.setSpacing(6)
        card.add_layout(header)
        header.addWidget(CardTitle("RGB camera"))
        header.addStretch(1)
        self._cam_pill = Pill("waiting", Pill.KIND_NEUTRAL)
        header.addWidget(self._cam_pill)

        viewport = QFrame()
        viewport.setObjectName("cardSubtle")
        viewport.setMinimumHeight(220)
        viewport.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        v = QVBoxLayout(viewport)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        placeholder = QWidget(viewport)
        placeholder.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        ph = QVBoxLayout(placeholder)
        ph.setContentsMargins(0, 0, 0, 0)
        ph.addStretch(1)
        glyph = QLabel("\u25CE", placeholder)
        glyph.setStyleSheet(
            "color: #c4c4c8; font-size: 56px; background-color: transparent;"
        )
        glyph.setAlignment(Qt.AlignCenter)
        ph.addWidget(glyph)
        msg = QLabel("USB camera not connected", placeholder)
        msg.setStyleSheet(
            "color: #8e8e93; font-size: 12px; background-color: transparent;"
        )
        msg.setAlignment(Qt.AlignCenter)
        ph.addWidget(msg)
        ph.addStretch(1)
        v.addWidget(placeholder, stretch=1)
        self._cam_placeholder = placeholder

        feed = QLabel(viewport)
        feed.setAlignment(Qt.AlignCenter)
        feed.setStyleSheet("background-color: transparent;")
        feed.setMinimumSize(240, 180)
        feed.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        feed.hide()
        v.addWidget(feed, stretch=1)
        self._cam_image_label = feed

        card.add(viewport, stretch=1)
        card.add(MutedLabel(
            "Live USB camera. Same feed the Vision screen processes."
        ))
        return card

    def _build_depth_card(self) -> Card:
        card = Card(padding=8, spacing=4)
        header = QHBoxLayout()
        header.setSpacing(6)
        card.add_layout(header)
        header.addWidget(CardTitle("Depth (D435)"))
        header.addStretch(1)
        self._depth_pill = Pill("waiting", Pill.KIND_NEUTRAL)
        header.addWidget(self._depth_pill)

        viewport = QFrame()
        viewport.setObjectName("cardSubtle")
        viewport.setMinimumHeight(220)
        viewport.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        v = QVBoxLayout(viewport)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        placeholder = QWidget(viewport)
        placeholder.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        ph = QVBoxLayout(placeholder)
        ph.setContentsMargins(0, 0, 0, 0)
        ph.addStretch(1)
        glyph = QLabel("\u25C9", placeholder)
        glyph.setStyleSheet(
            "color: #c4c4c8; font-size: 56px; background-color: transparent;"
        )
        glyph.setAlignment(Qt.AlignCenter)
        ph.addWidget(glyph)
        msg = QLabel("Depth camera not connected", placeholder)
        msg.setStyleSheet(
            "color: #8e8e93; font-size: 12px; background-color: transparent;"
        )
        msg.setAlignment(Qt.AlignCenter)
        ph.addWidget(msg)
        ph.addStretch(1)
        v.addWidget(placeholder, stretch=1)
        self._depth_placeholder = placeholder

        feed = QLabel(viewport)
        feed.setAlignment(Qt.AlignCenter)
        feed.setStyleSheet("background-color: transparent;")
        feed.setMinimumSize(240, 180)
        feed.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        feed.hide()
        v.addWidget(feed, stretch=1)
        self._depth_image_label = feed

        card.add(viewport, stretch=1)

        # Numeric overlay - shows the SAME forward / left / right
        # minima the autonomy stack consumes. Operators use this to
        # cross-check "why is the bot turning right?" against the
        # actual depth values.
        overlay = QLabel("\u2014")
        overlay.setStyleSheet(
            "background-color: #f5f5f7; border-radius: 8px; padding: 6px;"
            " font-family: Menlo, monospace; color: #1c1c1e;"
            " font-size: 12px;"
        )
        overlay.setAlignment(Qt.AlignCenter)
        overlay.setWordWrap(False)
        card.add(overlay)
        self._depth_overlay_label = overlay
        return card

    def _build_footer(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        self._autonomy_btn = QPushButton("Autonomous mode: OFF")
        self._autonomy_btn.setObjectName("primaryButton")
        self._autonomy_btn.setCursor(Qt.PointingHandCursor)
        self._autonomy_btn.setCheckable(True)
        self._autonomy_btn.setMinimumHeight(34)
        self._autonomy_btn.setMaximumHeight(34)
        self._autonomy_btn.toggled.connect(self._on_autonomy_toggle)
        row.addWidget(self._autonomy_btn)

        self._autonomy_status = MutedLabel(
            "Autonomy is off. The depth panel above is open for "
            "visualization, but Nina won't drive herself until the "
            "toggle is ON."
        )
        row.addWidget(self._autonomy_status, stretch=1)
        return row

    # ------------------------------------------------------------------
    # Signal wiring
    # ------------------------------------------------------------------

    def _wire_signals(self) -> None:
        try:
            self._service.vision.frame_ready.connect(self._on_camera_frame)
            self._service.vision.status_changed.connect(self._on_camera_status)
        except Exception:
            log.debug("PerceptionScreen: VisionWorker signals unavailable")
        try:
            self._slam.snapshot_changed.connect(self._on_slam_snapshot_meta)
            self._slam.status_changed.connect(self._on_slam_status)
        except Exception:
            log.debug("PerceptionScreen: SlamWorker signals unavailable")
        try:
            self._autonomy.enabled_changed.connect(self._on_autonomy_enabled)
            self._autonomy.sensor_health_changed.connect(self._on_sensor_health)
        except Exception:
            log.debug("PerceptionScreen: AutonomyController signals unavailable")

    # ------------------------------------------------------------------
    # Lifecycle hooks (called by MainWindow.navigate)
    # ------------------------------------------------------------------

    def on_enter(self) -> None:
        # 1) RGB - reuse the shared VisionWorker via refcount.
        if not self._holds_camera:
            try:
                self._service.vision.acquire()
                self._holds_camera = True
            except Exception as exc:
                log.warning("vision.acquire failed: %s", exc)

        # 2) Lidar / SLAM - passive; Map screen also calls start().
        try:
            self._slam.start()
        except Exception as exc:
            log.warning("slam.start failed: %s", exc)

        # 3) Depth - acquire through the autonomy controller's
        # refcount, then turn on colorization. Done in this order so
        # that even if open() fails we don't leave colorization on
        # for some other future caller.
        if not self._holds_depth:
            try:
                ok, msg = self._autonomy.acquire_depth()
                self._holds_depth = True
                self._depth_open_ok = ok
                self._depth_open_msg = msg
            except Exception as exc:
                ok, msg = False, f"depth: {exc}"
                self._depth_open_ok = False
                self._depth_open_msg = msg
                log.warning("autonomy.acquire_depth failed: %s", exc)
            self._update_depth_pill(self._depth_open_ok, self._depth_open_msg)

        try:
            self._autonomy.set_depth_visualization_enabled(True)
        except Exception:
            pass

        # Start the depth poll loop AFTER acquire so we don't generate
        # a flurry of "no frame yet" placeholder paints.
        self._depth_timer.start()

        # Sync footer + chip state with current reality in case the
        # operator toggled autonomy from a different screen.
        self._on_autonomy_enabled(self._autonomy.is_enabled())

    def on_leave(self) -> None:
        self._depth_timer.stop()
        try:
            self._autonomy.set_depth_visualization_enabled(False)
        except Exception:
            pass
        if self._holds_depth:
            try:
                self._autonomy.release_depth()
            finally:
                self._holds_depth = False
                self._depth_open_ok = False
        if self._holds_camera:
            try:
                self._service.vision.release()
            finally:
                self._holds_camera = False
        # Drop the cached buffer so we're not pinning a few MB of
        # depth data while the screen is off.
        self._last_depth_buf = None

    # ------------------------------------------------------------------
    # Slots: lidar / SLAM
    # ------------------------------------------------------------------

    def _on_slam_snapshot_meta(self, meta: dict) -> None:
        snap = self._slam.latest_snapshot()
        if snap is None or self._grid is None:
            return
        self._grid.set_grid(
            snap.grid_bytes,
            snap.width,
            snap.height,
            snap.scale_mm_per_px,
        )
        self._grid.set_pose(
            snap.pose.x_mm, snap.pose.y_mm, snap.pose.theta_deg,
        )
        when = meta.get("updated_at")
        if when:
            age = max(0.0, time.monotonic() - float(when))
            self._lidar_pill.setText(f"updated {age:.1f}s ago")
            self._lidar_pill.set_kind(Pill.KIND_OK)

    def _on_slam_status(self, status: dict) -> None:
        connected = bool(status.get("lidar_connected"))
        running = bool(status.get("running"))
        if connected and running:
            self._lidar_chip.setText("Lidar OK")
            self._lidar_chip.set_kind(Pill.KIND_OK)
        elif running:
            self._lidar_chip.setText("Lidar sim")
            self._lidar_chip.set_kind(Pill.KIND_WARN)
            if self._grid is not None:
                model = status.get("lidar_model") or ""
                if "S2E" in model.upper() or model == "":
                    hint = (
                        "Plug the Slamtec S2E into the Jetson Ethernet port "
                        "and verify `ping 192.168.11.2`."
                    )
                else:
                    hint = "Connect the RPLIDAR A1 to /dev/ttyUSB0."
                self._grid.set_placeholder(
                    f"Lidar simulation - no scans yet.\n{hint}"
                )
        else:
            self._lidar_chip.setText("Lidar -")
            self._lidar_chip.set_kind(Pill.KIND_NEUTRAL)

    # ------------------------------------------------------------------
    # Slots: RGB camera
    # ------------------------------------------------------------------

    def _on_camera_frame(self, image: QImage) -> None:
        if self._cam_image_label is None:
            return
        if self._cam_placeholder is not None and self._cam_placeholder.isVisible():
            self._cam_placeholder.hide()
            self._cam_image_label.show()
        target = self._cam_image_label.size()
        if target.width() <= 0 or target.height() <= 0:
            self._cam_image_label.setPixmap(QPixmap.fromImage(image))
            return
        pix = QPixmap.fromImage(image).scaled(
            target,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self._cam_image_label.setPixmap(pix)

    def _on_camera_status(self, status: dict) -> None:
        camera_open = bool(status.get("camera_open", False))
        message = str(status.get("message", "") or "")
        if camera_open:
            self._cam_pill.setText("Live")
            self._cam_pill.set_kind(Pill.KIND_OK)
            self._cam_chip.setText("Cam OK")
            self._cam_chip.set_kind(Pill.KIND_OK)
        else:
            self._cam_pill.setText(message or "not connected")
            self._cam_pill.set_kind(Pill.KIND_NEUTRAL)
            self._cam_chip.setText("Cam -")
            self._cam_chip.set_kind(Pill.KIND_NEUTRAL)
            if self._cam_image_label is not None:
                self._cam_image_label.clear()
                self._cam_image_label.hide()
            if self._cam_placeholder is not None:
                self._cam_placeholder.show()

    # ------------------------------------------------------------------
    # Slots: autonomy
    # ------------------------------------------------------------------

    def _on_autonomy_enabled(self, on: bool) -> None:
        self._autonomy_btn.blockSignals(True)
        self._autonomy_btn.setChecked(on)
        self._autonomy_btn.setText(
            f"Autonomous mode: {'ON' if on else 'OFF'}"
        )
        self._autonomy_btn.blockSignals(False)
        self._auto_pill.setText(
            f"Autonomous: {'ON' if on else 'OFF'}"
        )
        self._auto_pill.set_kind(Pill.KIND_OK if on else Pill.KIND_NEUTRAL)
        if on:
            self._autonomy_status.setText(
                "Autonomy is ACTIVE. Nina is steering herself based "
                "on the lidar + depth data shown above."
            )
        else:
            self._autonomy_status.setText(
                "Autonomy is off. The depth panel above is open for "
                "visualization, but Nina won't drive herself until "
                "the toggle is ON."
            )

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

    def _on_sensor_health(self, health: dict) -> None:
        depth = health.get("depth", {}) if isinstance(health, dict) else {}
        connected = bool(depth.get("connected"))
        message = str(depth.get("message") or "")
        if connected:
            self._depth_chip.setText("Depth OK")
            self._depth_chip.set_kind(Pill.KIND_OK)
        elif message:
            self._depth_chip.setText("Depth sim")
            self._depth_chip.set_kind(Pill.KIND_WARN)
        else:
            self._depth_chip.setText("Depth -")
            self._depth_chip.set_kind(Pill.KIND_NEUTRAL)

    # ------------------------------------------------------------------
    # Depth poll loop
    # ------------------------------------------------------------------

    def _poll_depth(self) -> None:
        """Pull the latest colorized depth + summary from the
        autonomy controller and paint it.

        Runs at _DEPTH_POLL_HZ from the GUI thread; cheap when no
        new frame has arrived (returns None and we bail).
        """
        if self._depth_image_label is None or self._depth_overlay_label is None:
            return
        try:
            payload = self._autonomy.latest_depth_visualization()
        except Exception as exc:
            log.debug("latest_depth_visualization failed: %s", exc)
            return
        if payload is None:
            return
        w, h, buf, frame = payload
        # Pin the buffer for the lifetime of the pixmap conversion.
        # See the `_last_depth_buf` docstring for why this matters.
        self._last_depth_buf = buf
        try:
            img = QImage(buf, w, h, w * 3, QImage.Format_BGR888)
        except Exception as exc:
            log.debug("QImage build failed (w=%s h=%s buf_len=%s): %s",
                      w, h, len(buf), exc)
            return

        if (
            self._depth_placeholder is not None
            and self._depth_placeholder.isVisible()
        ):
            self._depth_placeholder.hide()
            self._depth_image_label.show()

        target = self._depth_image_label.size()
        if target.width() > 0 and target.height() > 0:
            pix = QPixmap.fromImage(img).scaled(
                target,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            self._depth_image_label.setPixmap(pix)
        else:
            self._depth_image_label.setPixmap(QPixmap.fromImage(img))

        self._depth_pill.setText("Live")
        self._depth_pill.set_kind(Pill.KIND_OK)
        self._render_depth_overlay(frame)

    def _render_depth_overlay(self, frame) -> None:
        """Paint the forward / left / right minima below the depth
        image. These are the SAME numbers the autonomy stack reads
        from each frame (forward_min_mm, left_min_mm, right_min_mm),
        so the operator can verify "the bot turned right because
        forward was 400 mm" against ground truth."""
        if self._depth_overlay_label is None:
            return
        if frame is None:
            self._depth_overlay_label.setText(
                "F: \u2014   L: \u2014   R: \u2014"
            )
            return
        fwd = self._fmt_mm(getattr(frame, "forward_min_mm", None))
        lft = self._fmt_mm(getattr(frame, "left_min_mm", None))
        rgt = self._fmt_mm(getattr(frame, "right_min_mm", None))
        self._depth_overlay_label.setText(
            f"F: {fwd}   L: {lft}   R: {rgt}"
        )

    @staticmethod
    def _fmt_mm(value) -> str:
        if value is None:
            return "\u2014"
        try:
            mm = int(value)
        except (TypeError, ValueError):
            return "\u2014"
        if mm >= 1000:
            return f"{mm / 1000.0:.2f} m"
        return f"{mm} mm"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _update_depth_pill(self, ok: bool, message: str) -> None:
        if ok:
            self._depth_pill.setText("opening")
            self._depth_pill.set_kind(Pill.KIND_NEUTRAL)
            self._depth_chip.setText("Depth OK")
            self._depth_chip.set_kind(Pill.KIND_OK)
        else:
            label = message or "not connected"
            # Trim long error strings so the pill doesn't blow out
            # the header row.
            if len(label) > 32:
                label = label[:29] + "..."
            self._depth_pill.setText(label)
            self._depth_pill.set_kind(Pill.KIND_WARN)
            self._depth_chip.setText("Depth -")
            self._depth_chip.set_kind(Pill.KIND_NEUTRAL)
