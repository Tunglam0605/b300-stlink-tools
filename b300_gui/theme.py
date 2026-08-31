"""Professional High-Performance Industrial Design System & Theme Manager for B300 GUI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from PySide6.QtCore import QObject, QSettings, Signal
from PySide6.QtWidgets import QApplication, QWidget


@dataclass(frozen=True)
class ThemePalette:
    name: str
    is_dark: bool

    # Backgrounds
    canvas: str
    surface: str
    surface_raised: str
    surface_sunken: str
    input_bg: str
    terminal_bg: str

    # Borders
    border: str
    border_strong: str
    border_active: str
    border_muted: str

    # Typography
    text: str
    text_secondary: str
    text_muted: str
    text_on_accent: str

    # Tech Accents & Syntax Highlighting
    primary: str
    primary_hover: str
    primary_light: str
    accent_cyan: str
    accent_purple: str
    accent_amber: str

    # Status
    success: str
    success_hover: str
    success_light: str
    danger: str
    danger_hover: str
    danger_light: str
    warning: str
    warning_light: str


DARK_PALETTE = ThemePalette(
    name="dark",
    is_dark=True,
    canvas="#0B0F17",           # Deep obsidian slate
    surface="#121A26",          # Elevated dark card
    surface_raised="#1A2637",   # Active panel / header surface
    surface_sunken="#070B10",   # Depressed container
    input_bg="#0E1522",         # Input fields
    terminal_bg="#05080C",      # Terminal console

    border="#1E2B3E",           # Subdued structural border
    border_strong="#2C3D56",    # Control border
    border_active="#38BDF8",    # Active focus border (Electric Sky)
    border_muted="#162030",     # Subtle separator

    text="#F1F5F9",             # Slate 100 high contrast
    text_secondary="#94A3B8",   # Slate 400 secondary
    text_muted="#64748B",       # Slate 500 captions
    text_on_accent="#FFFFFF",   # High-contrast text on bright backgrounds

    primary="#0EA5E9",          # Vibrant Sky 500
    primary_hover="#38BDF8",    # Electric Sky 400
    primary_light="#0C253C",    # Sky tint for badges
    accent_cyan="#38BDF8",      # Electric Cyan for hex addresses
    accent_purple="#C084FC",    # Electric Purple for memory sizes
    accent_amber="#FBBF24",     # Electric Amber for registers

    success="#10B981",          # Emerald 500
    success_hover="#34D399",    # Emerald 400
    success_light="#064E3B",    # Dark emerald tint
    danger="#EF4444",           # Red 500
    danger_hover="#F87171",     # Red 400
    danger_light="#450A0A",     # Dark red tint
    warning="#F59E0B",          # Amber 500
    warning_light="#451A03",    # Dark amber tint
)


LIGHT_PALETTE = ThemePalette(
    name="light",
    is_dark=False,
    canvas="#F8FAFC",           # Clean Slate 50 canvas
    surface="#FFFFFF",          # Pure white card surface
    surface_raised="#F1F5F9",   # Slate 100 header
    surface_sunken="#E2E8F0",   # Depressed surface
    input_bg="#FFFFFF",         # Pure white input
    terminal_bg="#0F172A",      # Slate 900 IDE terminal

    border="#E2E8F0",           # Slate 200 light border
    border_strong="#CBD5E1",    # Slate 300 control border
    border_active="#0284C7",    # Sky 600 active border
    border_muted="#F1F5F9",     # Slate 100 separator

    text="#0F172A",             # Slate 900 typography
    text_secondary="#334155",   # Slate 700 secondary
    text_muted="#64748B",       # Slate 500 captions
    text_on_accent="#FFFFFF",   # White text on colored pills

    primary="#0284C7",          # Sky 600
    primary_hover="#0369A1",    # Sky 700
    primary_light="#E0F2FE",    # Sky 100 badge
    accent_cyan="#0284C7",      # Deep Sky for hex addresses
    accent_purple="#7C3AED",    # Deep Purple for memory sizes
    accent_amber="#D97706",     # Amber for registers

    success="#059669",          # Emerald 600
    success_hover="#047857",    # Emerald 700
    success_light="#D1FAE5",    # Emerald 100
    danger="#DC2626",           # Red 600
    danger_hover="#B91C1C",     # Red 700
    danger_light="#FEE2E2",     # Red 100
    warning="#D97706",          # Amber 600
    warning_light="#FEF3C7",    # Amber 100
)


def generate_stylesheet(p: ThemePalette) -> str:
    """Generate comprehensive modern industrial QSS stylesheet with rich typography and syntax highlighting."""
    return f"""
    /* Global Base */
    QMainWindow, QDialog, QWidget {{
        background-color: {p.canvas};
        color: {p.text};
        font-family: "Segoe UI", -apple-system, BlinkMacSystemFont, "Roboto", "Helvetica Neue", Arial, sans-serif;
        font-size: 13px;
    }}

    QDialog {{
        background-color: {p.surface};
    }}

    /* Tooltips */
    QToolTip {{
        background-color: {p.surface_raised};
        color: {p.text};
        border: 1px solid {p.border_strong};
        border-radius: 5px;
        padding: 6px 10px;
        font-size: 12px;
        font-weight: 500;
    }}

    /* Scrollbars */
    QScrollBar:vertical {{
        background: transparent;
        width: 8px;
        margin: 0px;
    }}
    QScrollBar::handle:vertical {{
        background: {p.border_strong};
        min-height: 24px;
        border-radius: 4px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {p.primary};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
    }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: transparent;
    }}

    QScrollBar:horizontal {{
        background: transparent;
        height: 8px;
        margin: 0px;
    }}
    QScrollBar::handle:horizontal {{
        background: {p.border_strong};
        min-width: 24px;
        border-radius: 4px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: {p.primary};
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0px;
    }}
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
        background: transparent;
    }}

    /* GroupBoxes & Structural Cards */
    QGroupBox {{
        background-color: {p.surface};
        border: 1px solid {p.border};
        border-radius: 8px;
        margin-top: 22px;
        padding: 12px 10px 10px 10px;
        font-weight: 700;
        font-size: 12px;
        color: {p.text};
    }}

    QGroupBox::title {{
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 10px;
        top: 2px;
        padding: 2px 8px;
        background-color: {p.surface};
        color: {p.accent_cyan};
        font-weight: 700;
        letter-spacing: 0.5px;
        border: 1px solid {p.border};
        border-radius: 4px;
    }}

    QFrame#cardSurface, QFrame#operatorCard, QFrame#rndCard {{
        background-color: {p.surface};
        border: 1px solid {p.border};
        border-radius: 8px;
    }}

    QFrame#cardSurface:hover, QFrame#operatorCard:hover {{
        border: 1px solid {p.border_strong};
    }}

    QFrame#sidebarPanel {{
        background-color: {p.surface};
        border-right: 1px solid {p.border};
    }}

    QFrame#headerBar {{
        background-color: {p.surface};
        border-bottom: 1px solid {p.border};
    }}

    QFrame#headerRibbon {{
        background-color: {p.surface_raised};
        border: 1px solid {p.border};
        border-radius: 8px;
        padding: 8px 12px;
    }}

    QFrame#pageContextHeader {{
        background-color: {p.surface};
        border: 1px solid {p.border};
        border-radius: 8px;
        margin-bottom: 4px;
    }}

    /* Rich Typography Hierarchy */
    QLabel#pageContextTitle {{
        font-size: 15px;
        font-weight: 800;
        color: {p.text};
        letter-spacing: 0.3px;
    }}

    QLabel#pageContextSubtitle {{
        font-size: 12px;
        color: {p.text_secondary};
        line-height: 1.4;
    }}

    QLabel#eyebrowLabel {{
        color: {p.primary};
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 0.7px;
        text-transform: uppercase;
    }}

    QLabel#navSectionTitle {{
        color: {p.text_muted};
        font-size: 10px;
        font-weight: 800;
        letter-spacing: 0.8px;
        padding: 4px 8px;
        text-transform: uppercase;
    }}

    QLabel#headerBrandTitle, QLabel#brandLogo {{
        font-size: 15px;
        font-weight: 900;
        color: {p.primary};
        letter-spacing: 0.8px;
    }}

    /* Syntax Highlighting Labels for Technical Data */
    QLabel#monoText, QLabel#hexInfoLabel {{
        font-family: "Cascadia Code", "JetBrains Mono", "Consolas", "Courier New", monospace;
        font-size: 12px;
        font-weight: 600;
        color: {p.text};
    }}

    QLabel#monoAddress {{
        font-family: "Cascadia Code", "JetBrains Mono", "Consolas", monospace;
        font-size: 12px;
        font-weight: 700;
        color: {p.accent_cyan};
    }}

    QLabel#monoCrc {{
        font-family: "Cascadia Code", "JetBrains Mono", "Consolas", monospace;
        font-size: 12px;
        font-weight: 700;
        color: {p.success};
    }}

    QLabel#monoSize {{
        font-family: "Cascadia Code", "JetBrains Mono", "Consolas", monospace;
        font-size: 12px;
        font-weight: 700;
        color: {p.accent_purple};
    }}

    QLabel#monoRegister {{
        font-family: "Cascadia Code", "JetBrains Mono", "Consolas", monospace;
        font-size: 12px;
        font-weight: 700;
        color: {p.accent_amber};
    }}

    QLabel#statusBanner {{
        background-color: {p.surface_raised};
        color: {p.text};
        border: 1px solid {p.border};
        border-radius: 6px;
        padding: 6px 12px;
        font-size: 12px;
        font-weight: 600;
    }}

    /* Buttons */
    QPushButton {{
        background-color: {p.surface_raised};
        color: {p.text};
        border: 1px solid {p.border_strong};
        border-radius: 6px;
        padding: 6px 14px;
        font-weight: 600;
        font-size: 12px;
        min-height: 18px;
    }}

    QPushButton:hover {{
        background-color: {p.border_strong};
        border-color: {p.primary};
        color: {p.text};
    }}

    QPushButton:pressed {{
        background-color: {p.surface_sunken};
    }}

    QPushButton:disabled {{
        background-color: {p.canvas};
        color: {p.text_muted};
        border-color: {p.border_muted};
    }}

    QPushButton#primaryButton {{
        background-color: {p.primary};
        color: {p.text_on_accent};
        border: 1px solid {p.primary_hover};
        border-radius: 6px;
        font-weight: 700;
    }}

    QPushButton#primaryButton:hover {{
        background-color: {p.primary_hover};
        border-color: {p.primary_hover};
    }}

    QPushButton#operatorFlashBtn {{
        background-color: {p.success};
        color: {p.text_on_accent};
        border: 1px solid {p.success_hover};
        border-radius: 8px;
        font-size: 14px;
        font-weight: 800;
        letter-spacing: 0.5px;
        padding: 10px 20px;
    }}

    QPushButton#operatorFlashBtn:hover {{
        background-color: {p.success_hover};
    }}

    QPushButton#operatorFlashBtn:disabled {{
        background-color: {p.surface_raised};
        color: {p.text_muted};
        border-color: {p.border};
    }}

    QPushButton#dangerButton {{
        background-color: {p.danger_light};
        color: {p.danger};
        border: 1px solid {p.danger};
        border-radius: 6px;
        font-weight: 600;
    }}

    QPushButton#dangerButton:hover {{
        background-color: {p.danger};
        color: {p.text_on_accent};
    }}

    QPushButton#ghostButton {{
        background-color: transparent;
        color: {p.text_secondary};
        border: 1px solid {p.border};
        border-radius: 6px;
        padding: 6px 12px;
    }}

    QPushButton#ghostButton:hover {{
        background-color: {p.surface_raised};
        color: {p.text};
        border-color: {p.border_strong};
    }}

    QPushButton#navButton {{
        background-color: transparent;
        color: {p.text_secondary};
        border: 1px solid transparent;
        border-radius: 6px;
        padding: 8px 12px;
        text-align: left;
        font-weight: 600;
        font-size: 12px;
    }}

    QPushButton#navButton:hover {{
        background-color: {p.surface_raised};
        color: {p.text};
    }}

    QPushButton#navButton:checked {{
        background-color: {p.surface_raised};
        color: {p.primary};
        font-weight: 700;
        border-left: 3px solid {p.primary};
    }}

    QPushButton#navUtilityButton {{
        background-color: transparent;
        color: {p.text_secondary};
        border: 1px solid {p.border};
        border-radius: 6px;
        padding: 7px 12px;
        text-align: left;
        font-size: 12px;
        font-weight: 500;
    }}

    QPushButton#navUtilityButton:hover {{
        background-color: {p.surface_raised};
        color: {p.text};
    }}

    QPushButton#compactNavBtn {{
        background-color: transparent;
        color: {p.text_secondary};
        border: 1px solid transparent;
        border-radius: 8px;
        font-family: "Cascadia Code", "JetBrains Mono", monospace;
        font-size: 11px;
        font-weight: 700;
        margin: 2px 4px;
        padding: 4px;
    }}

    QPushButton#compactNavBtn:hover {{
        background-color: {p.surface_raised};
        color: {p.text};
        border-color: {p.border};
    }}

    QPushButton#compactNavBtn[active="true"], QPushButton#compactNavBtn:checked {{
        background-color: {p.primary_light};
        color: {p.primary};
        border: 1px solid {p.primary};
        font-weight: 800;
    }}

    QPushButton#sidebarToggleBtn {{
        background-color: {p.surface_raised};
        color: {p.text_secondary};
        border: 1px solid {p.border};
        border-radius: 6px;
        font-size: 11px;
        font-weight: 700;
    }}

    QPushButton#sidebarToggleBtn:hover {{
        background-color: {p.border_strong};
        color: {p.text};
    }}

    /* Form Inputs */
    QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox {{
        background-color: {p.input_bg};
        color: {p.text};
        border: 1px solid {p.border_strong};
        border-radius: 6px;
        padding: 6px 10px;
        selection-background-color: {p.primary};
        selection-color: {p.text_on_accent};
    }}

    QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus {{
        border: 1px solid {p.border_active};
        background-color: {p.surface};
    }}

    QLineEdit:read-only, QPlainTextEdit:read-only {{
        background-color: {p.surface_raised};
        color: {p.text_secondary};
    }}

    /* ComboBox */
    QComboBox {{
        background-color: {p.input_bg};
        color: {p.text};
        border: 1px solid {p.border_strong};
        border-radius: 6px;
        padding: 6px 10px;
        min-height: 20px;
    }}

    QComboBox:hover {{
        border-color: {p.primary};
    }}

    QComboBox:on {{
        border-color: {p.border_active};
    }}

    QComboBox::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 20px;
        border-left: 1px solid {p.border};
    }}

    QComboBox QAbstractItemView {{
        background-color: {p.surface};
        color: {p.text};
        border: 1px solid {p.border_strong};
        border-radius: 6px;
        selection-background-color: {p.surface_raised};
        selection-color: {p.primary};
        padding: 4px;
    }}

    /* Precision Hex & Memory Table */
    QTableWidget {{
        background-color: {p.surface};
        color: {p.text};
        border: 1px solid {p.border};
        border-radius: 8px;
        gridline-color: {p.border_muted};
        alternate-background-color: {p.surface_raised};
    }}

    QHeaderView::section {{
        background-color: {p.surface_raised};
        color: {p.accent_cyan};
        font-weight: 800;
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        padding: 6px 10px;
        border: none;
        border-bottom: 1px solid {p.border};
        border-right: 1px solid {p.border_muted};
    }}

    QTableWidget::item:selected {{
        background-color: {p.surface_raised};
        color: {p.primary};
    }}

    /* Terminal & Log Viewers */
    QPlainTextEdit#terminalView, QPlainTextEdit#logView {{
        background-color: {p.terminal_bg};
        color: {p.accent_cyan};
        font-family: "Cascadia Code", "JetBrains Mono", "Consolas", monospace;
        font-size: 12px;
        border: 1px solid {p.border};
        border-radius: 6px;
        padding: 8px;
    }}

    /* Progress Bar */
    QProgressBar {{
        background-color: {p.surface_sunken};
        border: 1px solid {p.border};
        border-radius: 4px;
        text-align: center;
        color: {p.text};
        font-weight: 700;
        font-size: 11px;
        min-height: 14px;
        max-height: 14px;
    }}

    QProgressBar::chunk {{
        background-color: {p.primary};
        border-radius: 3px;
    }}

    /* Vivid Glowing Status Pills */
    QLabel#statusPillSuccess {{
        background-color: {p.success_light};
        color: {p.success};
        border: 1px solid {p.success};
        border-radius: 12px;
        padding: 3px 10px;
        font-weight: 800;
        font-size: 11px;
        letter-spacing: 0.3px;
    }}

    QLabel#statusPillDanger {{
        background-color: {p.danger_light};
        color: {p.danger};
        border: 1px solid {p.danger};
        border-radius: 12px;
        padding: 3px 10px;
        font-weight: 800;
        font-size: 11px;
        letter-spacing: 0.3px;
    }}

    QLabel#statusPillWarning {{
        background-color: {p.warning_light};
        color: {p.warning};
        border: 1px solid {p.warning};
        border-radius: 12px;
        padding: 3px 10px;
        font-weight: 800;
        font-size: 11px;
        letter-spacing: 0.3px;
    }}

    QLabel#statusPillNeutral {{
        background-color: {p.surface_raised};
        color: {p.text_secondary};
        border: 1px solid {p.border_strong};
        border-radius: 12px;
        padding: 3px 10px;
        font-weight: 700;
        font-size: 11px;
    }}

    QLabel#statusPillSky {{
        background-color: {p.primary_light};
        color: {p.accent_cyan};
        border: 1px solid {p.accent_cyan};
        border-radius: 12px;
        padding: 3px 10px;
        font-weight: 800;
        font-size: 11px;
    }}

    QLabel#statusPillPurple {{
        background-color: {p.surface_raised};
        color: {p.accent_purple};
        border: 1px solid {p.accent_purple};
        border-radius: 12px;
        padding: 3px 10px;
        font-weight: 800;
        font-size: 11px;
    }}

    /* Segmented Control */
    QFrame#segmentedControl {{
        background-color: {p.surface_sunken};
        border: 1px solid {p.border};
        border-radius: 8px;
        padding: 2px;
    }}

    QPushButton#segmentBtn {{
        background-color: transparent;
        color: {p.text_secondary};
        border: none;
        border-radius: 6px;
        padding: 5px 14px;
        font-size: 12px;
        font-weight: 600;
    }}

    QPushButton#segmentBtn:hover {{
        color: {p.text};
    }}

    QPushButton#segmentBtn[active="true"] {{
        background-color: {p.surface};
        color: {p.primary};
        font-weight: 800;
        border: 1px solid {p.border};
    }}

    /* Stepper Steps */
    QFrame#stepCard {{
        background-color: {p.surface_raised};
        border: 1px solid {p.border};
        border-radius: 8px;
        padding: 6px;
    }}

    QFrame#stepCard[state="active"] {{
        background-color: {p.primary_light};
        border: 1px solid {p.primary};
    }}

    QFrame#stepCard[state="success"] {{
        background-color: {p.success_light};
        border: 1px solid {p.success};
    }}

    QFrame#stepCard[state="error"] {{
        background-color: {p.danger_light};
        border: 1px solid {p.danger};
    }}

    QLabel#stepBadge {{
        background-color: {p.surface_sunken};
        color: {p.text_secondary};
        border: 1px solid {p.border_strong};
        border-radius: 10px;
        font-family: "Cascadia Code", "JetBrains Mono", monospace;
        font-size: 10px;
        font-weight: 800;
    }}

    QFrame#stepCard[state="active"] QLabel#stepBadge {{
        background-color: {p.primary};
        color: {p.text_on_accent};
        border: 1px solid {p.primary_hover};
    }}

    QFrame#stepCard[state="success"] QLabel#stepBadge {{
        background-color: {p.success};
        color: {p.text_on_accent};
        border: 1px solid {p.success_hover};
    }}

    QFrame#stepCard[state="error"] QLabel#stepBadge {{
        background-color: {p.danger};
        color: {p.text_on_accent};
        border: 1px solid {p.danger_hover};
    }}

    /* Pass / Fail Banner */
    QFrame#passFailBanner {{
        border-radius: 8px;
        padding: 12px 16px;
    }}

    QFrame#passFailBanner[variant="pass"] {{
        background-color: {p.success_light};
        border: 2px solid {p.success};
    }}

    QFrame#passFailBanner[variant="fail"] {{
        background-color: {p.danger_light};
        border: 2px solid {p.danger};
    }}

    QFrame#passFailBanner[variant="info"] {{
        background-color: {p.primary_light};
        border: 2px solid {p.primary};
    }}
    """


class ThemeManager(QObject):
    """Singleton Theme Manager providing dynamic Dark/Light palette and stylesheet."""

    theme_changed = Signal(str)

    _instance: ThemeManager | None = None

    def __init__(self) -> None:
        super().__init__()
        self._settings = QSettings("TungLamAutomation", "B300-STLink")
        self._mode = str(self._settings.value("theme_mode", "dark"))
        if self._mode not in ("dark", "light"):
            self._mode = "dark"

    @classmethod
    def instance(cls) -> ThemeManager:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def current_mode(self) -> str:
        return self._mode

    @property
    def is_dark(self) -> bool:
        return self._mode == "dark"

    @property
    def palette(self) -> ThemePalette:
        return DARK_PALETTE if self._mode == "dark" else LIGHT_PALETTE

    def set_theme(self, mode: str) -> None:
        if mode not in ("dark", "light"):
            return
        if self._mode != mode:
            self._mode = mode
            self._settings.setValue("theme_mode", mode)
            self.apply()
            self.theme_changed.emit(self._mode)

    def toggle_theme(self) -> str:
        new_mode = "light" if self._mode == "dark" else "dark"
        self.set_theme(new_mode)
        return new_mode

    def apply(self) -> None:
        app = QApplication.instance()
        if app:
            app.setStyleSheet(generate_stylesheet(self.palette))
