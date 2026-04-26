"""Small reusable building blocks shared by every Nina screen."""

from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class Card(QFrame):
    """White rounded card with a comfortable inner padding."""

    def __init__(
        self,
        *,
        padding: int = 20,
        spacing: int = 12,
        subtle: bool = False,
        hero: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        if hero:
            self.setObjectName("cardHero")
        elif subtle:
            self.setObjectName("cardSubtle")
        else:
            self.setObjectName("card")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(padding, padding, padding, padding)
        self._layout.setSpacing(spacing)

    def layout(self) -> QVBoxLayout:  # type: ignore[override]
        return self._layout

    def add(self, widget: QWidget, stretch: int = 0) -> None:
        self._layout.addWidget(widget, stretch=stretch)

    def add_layout(self, layout, stretch: int = 0) -> None:
        self._layout.addLayout(layout, stretch=stretch)

    def add_stretch(self, stretch: int = 1) -> None:
        self._layout.addStretch(stretch)


class CardTitle(QLabel):
    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setProperty("class", "cardTitle")
        self.setStyleSheet("font-size: 18px; font-weight: 600;")


class SectionLabel(QLabel):
    """Small all-caps section header, used inside cards."""

    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(text.upper(), parent)
        self.setProperty("class", "sectionLabel")
        self.setStyleSheet(
            "color: #8e8e93; font-size: 11px; font-weight: 700;"
            " letter-spacing: 1.5px;"
        )


class MutedLabel(QLabel):
    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setProperty("class", "cardMuted")
        self.setStyleSheet("color: #6e6e73; font-size: 13px;")


class Pill(QLabel):
    """Small colored status pill (OK / Warn / Error / Neutral)."""

    KIND_OK = "pillOk"
    KIND_WARN = "pillWarn"
    KIND_ERROR = "pillError"
    KIND_NEUTRAL = "pillNeutral"

    def __init__(self, text: str, kind: str = KIND_NEUTRAL, parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setObjectName(kind)
        self.setAlignment(Qt.AlignCenter)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)

    def set_kind(self, kind: str) -> None:
        self.setObjectName(kind)
        self.style().unpolish(self)
        self.style().polish(self)


class HRule(QFrame):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.HLine)
        self.setFrameShadow(QFrame.Plain)
        self.setStyleSheet("color: #e3e3e6; background-color: #e3e3e6; max-height: 1px;")


class Breadcrumb(QWidget):
    """Muted "Nina / Drive" style breadcrumb above the body content."""

    def __init__(self, *parts: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        sep = " / "
        text = sep.join(parts)
        label = QLabel(text)
        label.setStyleSheet("color: #8e8e93; font-size: 13px;")
        h.addWidget(label)
        h.addStretch(1)
        self._label = label

    def set_parts(self, *parts: str) -> None:
        self._label.setText(" / ".join(parts))
