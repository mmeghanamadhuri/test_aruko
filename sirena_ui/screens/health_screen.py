"""Health Check screen: donut + subsystem rows + Run all checks."""

from __future__ import annotations

from datetime import datetime

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
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
from sirena_ui.widgets.donut_gauge import DonutGauge
from sirena_ui.workers.health_collector import (
    STATUS_ERROR,
    STATUS_OK,
    STATUS_PENDING,
    STATUS_WARN,
    collect,
)
from sirena_ui.workers.nina_service import NinaService


class HealthScreen(QWidget):
    def __init__(self, service: NinaService, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._last_run: datetime | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(14)

        outer.addWidget(Breadcrumb("Nina", "Health"))

        outer.addWidget(self._build_hero())

        rows_card = Card(padding=20, spacing=10)
        outer.addWidget(rows_card, stretch=1)
        rows_card.add(CardTitle("Subsystems"))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        rows_card.add(scroll, stretch=1)
        self._rows_host = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_host)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(2)
        self._rows_layout.addStretch(1)
        scroll.setWidget(self._rows_host)

    # ---------- hero ----------

    def _build_hero(self) -> Card:
        card = Card(padding=20, spacing=14, hero=True)
        h = QHBoxLayout()
        h.setSpacing(20)
        card.add_layout(h)

        self._gauge = DonutGauge(0, 0, 0, 1)
        h.addWidget(self._gauge)

        text = QVBoxLayout()
        text.setSpacing(4)
        h.addLayout(text, stretch=1)
        self._summary_title = QLabel("Run a check to see status")
        self._summary_title.setStyleSheet(
            "color: #1c1c1e; font-size: 22px; font-weight: 700;"
            " background-color: transparent;"
        )
        text.addWidget(self._summary_title)
        self._summary_sub = MutedLabel("Last run \u2014 \u00b7 0 checks")
        text.addWidget(self._summary_sub)
        text.addStretch(1)

        cta = QVBoxLayout()
        cta.setSpacing(8)
        h.addLayout(cta)
        run = QPushButton("Run all checks")
        run.setObjectName("primaryButton")
        run.setCursor(Qt.PointingHandCursor)
        run.clicked.connect(self.refresh)
        cta.addWidget(run)
        export = QPushButton("Export report")
        export.setObjectName("secondaryButton")
        export.setCursor(Qt.PointingHandCursor)
        export.clicked.connect(self._on_export)
        cta.addWidget(export)

        return card

    # ---------- lifecycle ----------

    def on_enter(self) -> None:
        if self._last_run is None:
            self.refresh()

    def refresh(self) -> None:
        rows = collect(self._service)
        ok = sum(1 for r in rows if r.is_ok)
        warn = sum(1 for r in rows if r.is_warn)
        err = sum(1 for r in rows if r.is_error)
        pending = sum(1 for r in rows if r.status == STATUS_PENDING)
        total = len(rows)

        self._gauge.set_counts(ok, warn, err, total)
        if err > 0:
            self._summary_title.setText("Action required")
        elif warn > 0:
            self._summary_title.setText("System degraded")
        elif pending > 0:
            self._summary_title.setText("Partial integration")
        else:
            self._summary_title.setText("System healthy")

        now = datetime.now()
        self._last_run = now
        self._summary_sub.setText(
            f"Last run \u00b7 {now.strftime('%H:%M')} \u00b7 {total} checks"
        )

        self._render_rows(rows)

    def _render_rows(self, rows) -> None:
        # Clear existing rows (keep stretch at the end)
        for i in reversed(range(self._rows_layout.count() - 1)):
            item = self._rows_layout.takeAt(i)
            if item is None:
                continue
            w = item.widget()
            if w is not None:
                w.deleteLater()

        for idx, row in enumerate(rows):
            self._rows_layout.insertWidget(self._rows_layout.count() - 1, _RowWidget(row, idx))

    def _on_export(self) -> None:
        from PyQt5.QtWidgets import QMessageBox
        QMessageBox.information(
            self,
            "Export report",
            "Health-report export will save a JSON snapshot to disk in a"
            " future build.",
        )


class _RowWidget(QFrame):
    def __init__(self, row, index: int, parent=None) -> None:
        super().__init__(parent)
        bg = "#ffffff" if index % 2 == 0 else "#fafafc"
        self.setStyleSheet(f"background-color: {bg}; border-radius: 6px;")
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 10, 12, 10)
        h.setSpacing(12)

        glyph = QLabel(row.glyph)
        glyph.setFixedWidth(28)
        glyph.setStyleSheet(
            "color: #6e6e73; font-size: 18px; background-color: transparent;"
        )
        h.addWidget(glyph)

        label = QLabel(row.label)
        label.setFixedWidth(180)
        label.setStyleSheet(
            "color: #1c1c1e; font-weight: 600; background-color: transparent;"
        )
        h.addWidget(label)

        detail = QLabel(row.detail)
        detail.setStyleSheet(
            "color: #6e6e73; background-color: transparent;"
        )
        detail.setWordWrap(True)
        h.addWidget(detail, stretch=1)

        kind, label_text = _status_to_pill(row.status)
        h.addWidget(Pill(label_text, kind))

        view = QPushButton("View logs")
        view.setFlat(True)
        view.setStyleSheet(
            "color: #c8102e; background: transparent; border: none;"
            " text-decoration: underline; padding: 0 6px;"
        )
        view.setCursor(Qt.PointingHandCursor)
        h.addWidget(view)


def _status_to_pill(status: str):
    if status == STATUS_OK:
        return Pill.KIND_OK, "OK"
    if status == STATUS_WARN:
        return Pill.KIND_WARN, "Warning"
    if status == STATUS_ERROR:
        return Pill.KIND_ERROR, "Error"
    return Pill.KIND_NEUTRAL, "Pending"
