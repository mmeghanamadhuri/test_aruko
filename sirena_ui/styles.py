"""Brand styles + a Qt stylesheet shared by the app."""

from pathlib import Path

# Sirena palette
BRAND_RED = "#c8102e"
BRAND_RED_DARK = "#9b0c23"
BRAND_RED_HOVER = "#dc2741"
BRAND_WHITE = "#ffffff"
BRAND_BG = "#f5f5f7"
BRAND_PANEL = "#ffffff"
BRAND_BORDER = "#e3e3e6"
BRAND_TEXT = "#1c1c1e"
BRAND_MUTED = "#6e6e73"
BRAND_SUCCESS = "#2ecc71"
BRAND_DANGER = "#e74c3c"


ASSETS_DIR = Path(__file__).resolve().parent / "assets"


def asset_path(name: str) -> str:
    return str(ASSETS_DIR / name)


STYLESHEET = f"""
QWidget {{
    background-color: {BRAND_BG};
    color: {BRAND_TEXT};
    font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
    font-size: 14px;
}}

QFrame#headerBar {{
    background-color: {BRAND_RED};
    color: {BRAND_WHITE};
    border: none;
}}

QLabel#headerTitle {{
    color: {BRAND_WHITE};
    font-size: 22px;
    font-weight: 600;
    background-color: transparent;
}}

QLabel#headerSubtitle {{
    color: {BRAND_WHITE};
    font-size: 14px;
    background-color: transparent;
}}

QPushButton#headerBack {{
    background-color: transparent;
    color: {BRAND_WHITE};
    border: none;
    font-size: 22px;
    padding: 4px 12px;
}}
QPushButton#headerBack:hover {{
    color: #ffd9df;
}}

QFrame#footerBar {{
    background-color: {BRAND_PANEL};
    color: {BRAND_MUTED};
    border-top: 1px solid {BRAND_BORDER};
}}

QFrame#card {{
    background-color: {BRAND_PANEL};
    border: 1px solid {BRAND_BORDER};
    border-radius: 14px;
}}

QFrame#cardDisabled {{
    background-color: #ececef;
    border: 2px dashed #c4c4c8;
    border-radius: 14px;
}}

QLabel.cardTitle {{
    background-color: transparent;
    font-size: 18px;
    font-weight: 600;
}}

QLabel.cardMuted {{
    background-color: transparent;
    color: {BRAND_MUTED};
    font-size: 13px;
}}

QPushButton#primary {{
    background-color: {BRAND_RED};
    color: {BRAND_WHITE};
    border: none;
    border-radius: 22px;
    padding: 10px 22px;
    font-weight: 600;
}}
QPushButton#primary:hover {{
    background-color: {BRAND_RED_HOVER};
}}
QPushButton#primary:pressed {{
    background-color: {BRAND_RED_DARK};
}}
QPushButton#primary:disabled {{
    background-color: #d8d8db;
    color: #9a9a9f;
}}

QPushButton#stopButton {{
    background-color: {BRAND_DANGER};
    color: {BRAND_WHITE};
    border: none;
    border-radius: 28px;
    padding: 16px 24px;
    font-size: 18px;
    font-weight: 700;
    letter-spacing: 1px;
}}
QPushButton#stopButton:hover {{
    background-color: #ff6f5e;
}}

QPushButton#startButton {{
    background-color: {BRAND_RED};
    color: {BRAND_WHITE};
    border: none;
    border-radius: 28px;
    padding: 16px 24px;
    font-size: 18px;
    font-weight: 700;
    letter-spacing: 1px;
}}
QPushButton#startButton:hover {{
    background-color: {BRAND_RED_HOVER};
}}
QPushButton#startButton:pressed {{
    background-color: {BRAND_RED_DARK};
}}
QPushButton#startButton:disabled {{
    background-color: #d8d8db;
    color: #9a9a9f;
}}

QPushButton#playButton {{
    background-color: {BRAND_RED};
    color: {BRAND_WHITE};
    border: none;
    border-radius: 22px;
    min-width: 44px;
    min-height: 44px;
    font-size: 18px;
    font-weight: 700;
}}
QPushButton#playButton:hover {{
    background-color: {BRAND_RED_HOVER};
}}
QPushButton#playButton:disabled {{
    background-color: #d8d8db;
    color: #9a9a9f;
}}

QPushButton#tabButton {{
    background-color: #ececef;
    color: {BRAND_MUTED};
    border: none;
    padding: 14px 24px;
    font-size: 16px;
    font-weight: 600;
}}
QPushButton#tabButton:checked {{
    background-color: {BRAND_RED};
    color: {BRAND_WHITE};
}}

QLineEdit, QSpinBox, QDoubleSpinBox {{
    background-color: {BRAND_PANEL};
    border: 1px solid {BRAND_BORDER};
    border-radius: 8px;
    padding: 6px 10px;
    selection-background-color: {BRAND_RED};
    selection-color: {BRAND_WHITE};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 1px solid {BRAND_RED};
}}

QListWidget {{
    background-color: transparent;
    border: none;
}}
QListWidget::item {{
    background: transparent;
    padding: 0px;
    margin: 0px;
}}
QListWidget::item:selected {{
    background: transparent;
}}

QProgressBar {{
    background-color: #e6e6e9;
    border: none;
    border-radius: 6px;
    text-align: center;
    height: 12px;
}}
QProgressBar::chunk {{
    background-color: {BRAND_RED};
    border-radius: 6px;
}}

QScrollArea {{
    background: transparent;
    border: none;
}}
"""
