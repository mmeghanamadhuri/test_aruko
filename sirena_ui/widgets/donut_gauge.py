"""Tiny donut chart used by the Health screen."""

from __future__ import annotations

from PyQt5.QtCore import Qt, QRect, QRectF
from PyQt5.QtGui import QColor, QPainter, QPen, QBrush, QPixmap
from PyQt5.QtWidgets import QWidget

from sirena_ui.styles import asset_path


class DonutGauge(QWidget):
    """Donut showing OK / Warn / Error counts with Nina's photo in the hole."""

    def __init__(self, ok: int = 0, warn: int = 0, err: int = 0, total: int = 0, parent=None) -> None:
        super().__init__(parent)
        self._ok = ok
        self._warn = warn
        self._err = err
        self._total = max(total, ok + warn + err) or 1
        self.setFixedSize(180, 180)
        self._photo = QPixmap(asset_path("nina.png"))

    def set_counts(self, ok: int, warn: int, err: int, total: int) -> None:
        self._ok = ok
        self._warn = warn
        self._err = err
        self._total = max(total, ok + warn + err) or 1
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        pad = 10
        rect = QRect(pad, pad, self.width() - 2 * pad, self.height() - 2 * pad)

        # Background ring
        pen = QPen(QColor("#ececef"), 14, Qt.SolidLine, Qt.FlatCap)
        p.setPen(pen)
        p.drawArc(rect, 0, 360 * 16)

        # Filled arcs (Qt's drawArc uses 1/16 of a degree). We start at the
        # top (90 deg) and sweep clockwise, so spans must be negative.
        start = 90 * 16
        arcs = [
            (self._ok, "#2ecc71"),
            (self._warn, "#f5a623"),
            (self._err, "#e74c3c"),
        ]
        for count, color in arcs:
            if count <= 0:
                continue
            span = -int(round(360 * 16 * (count / self._total)))
            pen.setColor(QColor(color))
            p.setPen(pen)
            p.drawArc(rect, start, span)
            start += span

        # Nina photo in the hole
        if not self._photo.isNull():
            inner_pad = 30
            inner = rect.adjusted(inner_pad, inner_pad, -inner_pad, -inner_pad)
            p.setBrush(QBrush(QColor("#ffffff")))
            p.setPen(Qt.NoPen)
            p.drawEllipse(inner)
            scaled = self._photo.scaledToHeight(
                inner.height() - 4, Qt.SmoothTransformation
            )
            x = inner.center().x() - scaled.width() // 2
            y = inner.center().y() - scaled.height() // 2
            # Clip the pixmap to a circle so the photo respects the donut hole.
            p.save()
            from PyQt5.QtGui import QPainterPath
            path = QPainterPath()
            path.addEllipse(QRectF(inner))
            p.setClipPath(path)
            p.drawPixmap(x, y, scaled)
            p.restore()

        # Big label inside ring
        ok_total = f"{self._ok}/{self._total}"
        p.setPen(QColor("#1c1c1e"))
        font = p.font()
        font.setBold(True)
        font.setPointSize(11)
        p.setFont(font)
        text_rect = QRect(rect.x(), rect.bottom() - 26, rect.width(), 22)
        p.drawText(text_rect, Qt.AlignCenter, ok_total)
