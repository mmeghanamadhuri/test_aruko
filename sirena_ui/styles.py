"""Brand styles + a Qt stylesheet shared by the app.

The v2 theme follows the latest Sirena identity work for the 10.1"
Nina app: red accent, dark charcoal sidebar/footer, and a light
"cloud" canvas with white cards. New screens reuse the tokens below
so the look stays consistent across Home, Drive, Vision, Map,
Actions, Settings, and Health.
"""

from pathlib import Path

# ---- Sirena palette --------------------------------------------------

BRAND_RED = "#c8102e"
BRAND_RED_DARK = "#9b0c23"
BRAND_RED_HOVER = "#dc2741"
BRAND_RED_TINT = "#fbe7eb"

BRAND_WHITE = "#ffffff"
BRAND_CLOUD = "#f5f5f7"
BRAND_PANEL = "#ffffff"
BRAND_BORDER = "#e3e3e6"

BRAND_TEXT = "#1c1c1e"          # near-black for headings
BRAND_BLACK = "#1c1c1e"
BRAND_MUTED = "#6e6e73"
BRAND_GREY = "#8e8e93"           # dividers, secondary chips

BRAND_CHARCOAL = "#2c2c2e"       # sidebar + footer surface
BRAND_CHARCOAL_HOVER = "#3a3a3c" # nav row hover
BRAND_CHARCOAL_ACTIVE = "#3a3a3c"
BRAND_CHARCOAL_TEXT = "#ffffff"

BRAND_SUCCESS = "#2ecc71"
BRAND_WARNING = "#f5a623"
BRAND_DANGER = "#e74c3c"

# Convenience aliases used throughout the legacy widgets
BRAND_BG = BRAND_CLOUD


ASSETS_DIR = Path(__file__).resolve().parent / "assets"


def asset_path(name: str) -> str:
    return str(ASSETS_DIR / name)


STYLESHEET = f"""
QWidget {{
    background-color: {BRAND_CLOUD};
    color: {BRAND_TEXT};
    font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
    font-size: 14px;
}}

/* ---------- Header bar ---------- */

QFrame#headerBar {{
    background-color: {BRAND_RED};
    color: {BRAND_WHITE};
    border: none;
}}
QFrame#headerBar QLabel {{
    background-color: transparent;
    color: {BRAND_WHITE};
}}
QLabel#headerTitle {{
    color: {BRAND_WHITE};
    font-size: 20px;
    font-weight: 600;
    background-color: transparent;
}}
QLabel#headerSubtitle {{
    color: {BRAND_WHITE};
    font-size: 13px;
    background-color: transparent;
}}
QPushButton#headerBack, QPushButton#headerTray {{
    background-color: transparent;
    color: {BRAND_WHITE};
    border: none;
    font-size: 14px;
    padding: 4px 10px;
}}
QPushButton#headerBack:hover, QPushButton#headerTray:hover {{
    color: #ffd9df;
}}

/* ---------- Sidebar (charcoal) ---------- */

QFrame#sidebar {{
    background-color: {BRAND_CHARCOAL};
    border: none;
}}
QFrame#sidebar QLabel {{
    background-color: transparent;
    color: {BRAND_CHARCOAL_TEXT};
}}
QLabel#sidebarFooter {{
    color: #9a9a9f;
    font-size: 11px;
}}
QPushButton#navRow {{
    background-color: transparent;
    color: {BRAND_CHARCOAL_TEXT};
    border: none;
    border-left: 3px solid transparent;
    text-align: left;
    padding: 12px 16px 12px 17px;
    font-size: 14px;
    font-weight: 500;
}}
QPushButton#navRow:hover {{
    background-color: {BRAND_CHARCOAL_HOVER};
}}
QPushButton#navRow:checked {{
    background-color: {BRAND_CHARCOAL_ACTIVE};
    color: {BRAND_RED};
    border-left: 3px solid {BRAND_RED};
    font-weight: 600;
}}

/* Sub-sidebar used inside the Settings screen */

QFrame#subSidebar {{
    background-color: {BRAND_PANEL};
    border: 1px solid {BRAND_BORDER};
    border-radius: 14px;
}}
QPushButton#subNavRow {{
    background-color: transparent;
    color: {BRAND_TEXT};
    border: none;
    border-left: 3px solid transparent;
    text-align: left;
    padding: 12px 16px 12px 17px;
    font-size: 14px;
}}
QPushButton#subNavRow:hover {{
    background-color: {BRAND_CLOUD};
}}
QPushButton#subNavRow:checked {{
    background-color: {BRAND_RED_TINT};
    border-left: 3px solid {BRAND_RED};
    color: {BRAND_RED};
    font-weight: 600;
}}

/* ---------- Footer ---------- */

QFrame#footerBar {{
    background-color: {BRAND_CHARCOAL};
    color: {BRAND_CHARCOAL_TEXT};
    border: none;
}}
QFrame#footerBar QLabel {{
    background-color: transparent;
    color: {BRAND_CHARCOAL_TEXT};
    font-size: 12px;
}}
QLabel.footerMuted {{
    color: #c7c7cc;
    font-size: 12px;
    background-color: transparent;
}}

/* ---------- Cards ---------- */

QFrame#card, QFrame.card {{
    background-color: {BRAND_PANEL};
    border: 1px solid {BRAND_BORDER};
    border-radius: 14px;
}}
QFrame#cardSubtle {{
    background-color: {BRAND_CLOUD};
    border: 1px solid {BRAND_BORDER};
    border-radius: 12px;
}}
QFrame#cardDisabled {{
    background-color: #ececef;
    border: 2px dashed #c4c4c8;
    border-radius: 14px;
}}
QFrame#cardHero {{
    background-color: {BRAND_PANEL};
    border: 1px solid {BRAND_BORDER};
    border-radius: 18px;
}}

QLabel.cardTitle {{
    background-color: transparent;
    font-size: 18px;
    font-weight: 600;
}}
QLabel.cardMuted, QLabel#cardMuted {{
    background-color: transparent;
    color: {BRAND_MUTED};
    font-size: 13px;
}}
QLabel.sectionLabel {{
    background-color: transparent;
    color: {BRAND_GREY};
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1.5px;
    text-transform: uppercase;
}}

/* ---------- Buttons ---------- */

QPushButton#primary, QPushButton#primaryButton {{
    background-color: {BRAND_RED};
    color: {BRAND_WHITE};
    border: none;
    border-radius: 18px;
    padding: 10px 20px;
    font-weight: 600;
}}
QPushButton#primary:hover, QPushButton#primaryButton:hover {{
    background-color: {BRAND_RED_HOVER};
}}
QPushButton#primary:pressed, QPushButton#primaryButton:pressed {{
    background-color: {BRAND_RED_DARK};
}}
QPushButton#primary:disabled, QPushButton#primaryButton:disabled {{
    background-color: #d8d8db;
    color: #9a9a9f;
}}

QPushButton#secondary, QPushButton#secondaryButton {{
    background-color: {BRAND_PANEL};
    color: {BRAND_RED};
    border: 1px solid {BRAND_RED};
    border-radius: 16px;
    padding: 8px 16px;
    font-weight: 600;
}}
QPushButton#secondary:hover, QPushButton#secondaryButton:hover {{
    background-color: {BRAND_RED_TINT};
}}
QPushButton#secondary:disabled, QPushButton#secondaryButton:disabled {{
    background-color: #ececef;
    color: #9a9a9f;
    border-color: #d8d8db;
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

/* Sub-tab pills used inside actions screen */

QPushButton#tabButton, QPushButton#subTabButton {{
    background-color: transparent;
    color: {BRAND_MUTED};
    border: none;
    border-bottom: 3px solid transparent;
    padding: 10px 18px;
    font-size: 14px;
    font-weight: 600;
}}
QPushButton#tabButton:hover, QPushButton#subTabButton:hover {{
    color: {BRAND_TEXT};
}}
QPushButton#tabButton:checked, QPushButton#subTabButton:checked {{
    color: {BRAND_RED};
    border-bottom: 3px solid {BRAND_RED};
}}

/* Round D-pad and large square buttons used on the Drive screen */

QPushButton#dpadButton {{
    background-color: {BRAND_PANEL};
    color: {BRAND_TEXT};
    border: 1px solid {BRAND_BORDER};
    border-radius: 16px;
    font-size: 18px;
    font-weight: 600;
    min-width: 96px;
    min-height: 96px;
}}
QPushButton#dpadButton:hover {{
    background-color: {BRAND_RED_TINT};
    border-color: {BRAND_RED};
}}
QPushButton#dpadButton:pressed {{
    background-color: {BRAND_RED_TINT};
    border-color: {BRAND_RED_DARK};
}}
QPushButton#dpadButton:disabled {{
    color: #b0b0b5;
    background-color: #f0f0f3;
    border-color: #d8d8db;
}}
QPushButton#dpadStop {{
    background-color: {BRAND_RED};
    color: {BRAND_WHITE};
    border: none;
    border-radius: 48px;
    font-size: 18px;
    font-weight: 800;
    letter-spacing: 1px;
    min-width: 96px;
    min-height: 96px;
}}
QPushButton#dpadStop:hover {{
    background-color: {BRAND_RED_HOVER};
}}
QPushButton#dpadStop:pressed {{
    background-color: {BRAND_RED_DARK};
}}

/* Toggle-style pills for Brake / Reverse / Recognition switches */

QPushButton#togglePill {{
    background-color: #ececef;
    color: {BRAND_TEXT};
    border: none;
    border-radius: 16px;
    padding: 8px 18px;
    font-weight: 600;
}}
QPushButton#togglePill:checked {{
    background-color: {BRAND_RED};
    color: {BRAND_WHITE};
}}

/* ---------- Inputs ---------- */

QLineEdit, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit {{
    background-color: {BRAND_PANEL};
    border: 1px solid {BRAND_BORDER};
    border-radius: 8px;
    padding: 6px 10px;
    selection-background-color: {BRAND_RED};
    selection-color: {BRAND_WHITE};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QTextEdit:focus, QPlainTextEdit:focus {{
    border: 1px solid {BRAND_RED};
}}

QComboBox {{
    background-color: {BRAND_PANEL};
    border: 1px solid {BRAND_BORDER};
    border-radius: 8px;
    padding: 6px 10px;
}}
QComboBox:focus {{
    border: 1px solid {BRAND_RED};
}}
QComboBox QAbstractItemView {{
    background-color: {BRAND_PANEL};
    selection-background-color: {BRAND_RED_TINT};
    selection-color: {BRAND_TEXT};
    border: 1px solid {BRAND_BORDER};
}}

QSlider::groove:horizontal {{
    border: none;
    height: 6px;
    background: #e6e6e9;
    border-radius: 3px;
}}
QSlider::sub-page:horizontal {{
    background: {BRAND_RED};
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: {BRAND_PANEL};
    border: 2px solid {BRAND_RED};
    width: 18px;
    height: 18px;
    margin: -7px 0;
    border-radius: 9px;
}}

/* ---------- Lists / scroll ---------- */

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

/* ---------- Status pills ---------- */

QLabel#pillOk {{
    background-color: #e7f7ee;
    color: #1f8a4c;
    border-radius: 10px;
    padding: 2px 10px;
    font-weight: 600;
    font-size: 12px;
}}
QLabel#pillWarn {{
    background-color: #fff5e0;
    color: #a86a00;
    border-radius: 10px;
    padding: 2px 10px;
    font-weight: 600;
    font-size: 12px;
}}
QLabel#pillError {{
    background-color: #fde7e9;
    color: {BRAND_RED};
    border-radius: 10px;
    padding: 2px 10px;
    font-weight: 600;
    font-size: 12px;
}}
QLabel#pillNeutral {{
    background-color: #ececef;
    color: {BRAND_MUTED};
    border-radius: 10px;
    padding: 2px 10px;
    font-weight: 600;
    font-size: 12px;
}}

/* ---------- Dialogs ---------- */

QDialog {{
    background-color: {BRAND_CLOUD};
}}
"""
