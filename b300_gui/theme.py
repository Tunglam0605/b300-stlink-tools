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
    canvas="#0A0E17",           # Deep dark canvas (harmonious with TungLam ESP32 root #0A0E17)
    surface="#131A2A",          # Elevated dark card (TungLam ESP32 panel #131A2A)
    surface_raised="#192237",   # Active panel / sub-card / hover (TungLam ESP32 panel_alt #192237)
    surface_sunken="#0D1420",   # Depressed container / input bg (TungLam ESP32 input #0D1420)
    input_bg="#0D1420",         # Input fields
    terminal_bg="#080C14",      # Terminal console

    border="#2A3A52",           # Harmonious structural slate border (TungLam ESP32 border #2A3A52)
    border_strong="#3B4F6E",    # Control border
    border_active="#10B981",    # Active focus border (TungLam ESP32 Emerald #10B981)
    border_muted="#1C2738",     # Subtle separator

    text="#F1F5F9",             # Slate 100 crisp white high contrast
    text_secondary="#94A3B8",   # Slate 400 calm secondary / labels
    text_muted="#64748B",       # Slate 500 captions
    text_on_accent="#FFFFFF",   # High-contrast text on bright backgrounds

    primary="#10B981",          # Emerald 500 - Tung Lam Signature Brand Accent
    primary_hover="#34D399",    # Emerald 400
    primary_light="#064E3B",    # Dark emerald tint for chips/badges
    accent_cyan="#38BDF8",      # Electric Sky strictly for memory hex offsets
    accent_purple="#C084FC",    # Electric Purple for memory sizes
    accent_amber="#FBBF24",     # Electric Amber for registers

    success="#10B981",          # Emerald 500 (TungLam ESP32 accent #10B981)
    success_hover="#34D399",    # Emerald 400
    success_light="#064E3B",    # Dark emerald tint (TungLam ESP32 accent_soft #064E3B)
    danger="#EF4444",           # Red 500
    danger_hover="#F87171",     # Red 400
    danger_light="#450A0A",     # Dark red tint
    warning="#F59E0B",          # Amber 500
    warning_light="#451A03",    # Dark amber tint
)


LIGHT_PALETTE = ThemePalette(
    name="light",
    is_dark=False,
    canvas="#F3F7FB",           # Clean Slate canvas (TungLam ESP32 light root #F3F7FB)
    surface="#FFFFFF",          # Pure white card surface
    surface_raised="#F8FAFC",   # Slate 100 header / hover
    surface_sunken="#E2E8F0",   # Depressed surface
    input_bg="#FFFFFF",         # Pure white input
    terminal_bg="#0F172A",      # Slate 900 IDE terminal

    border="#D7E2EE",           # Slate 200 light border (TungLam ESP32 light border #D7E2EE)
    border_strong="#CBD5E1",    # Slate 300 control border
    border_active="#059669",    # Emerald 600 active border
    border_muted="#F1F5F9",     # Slate 100 separator

    text="#1E293B",             # Slate 800 typography
    text_secondary="#526477",   # Slate 600 secondary / labels
    text_muted="#64748B",       # Slate 500 captions
    text_on_accent="#FFFFFF",   # White text on colored pills

    primary="#059669",          # Emerald 600 - Tung Lam Signature Brand Accent
    primary_hover="#047857",    # Emerald 700
    primary_light="#D1FAE5",    # Soft emerald tint
    accent_cyan="#0284C7",      # Sky 600 for hex addresses
    accent_purple="#7C3AED",    # Purple 600 for sizes
    accent_amber="#D97706",     # Amber 600 for registers

    success="#059669",          # Emerald 600
    success_hover="#047857",    # Emerald 700
    success_light="#D1FAE5",    # Soft emerald tint
    danger="#DC2626",           # Red 600
    danger_hover="#B91C1C",     # Red 700
    danger_light="#FEE2E2",     # Soft red tint
    warning="#D97706",          # Amber 600
    warning_light="#FEF3C7",    # Soft amber tint
)


def generate_stylesheet(p: ThemePalette) -> str:
    """Generate comprehensive modern industrial QSS stylesheet with rich typography and syntax highlighting."""
    return f"""
    /* Global Base */
    QMainWindow, QDialog, QWidget#centralContainer, QWidget#mainWorkArea, QStackedWidget, QWidget#operatorContainer {{
        background-color: {p.canvas};
    }}

    QWidget {{
        color: {p.text};
        font-family: "Segoe UI", -apple-system, BlinkMacSystemFont, "Roboto", "Helvetica Neue", Arial, sans-serif;
        font-size: 13px;
    }}

    QDialog {{
        background-color: {p.surface};
    }}

    /* All Labels are transparent by default — prevents ugly black boxes on cards */
    QLabel {{
        background: transparent;
        background-color: transparent;
        color: {p.text};
    }}

    QCheckBox, QRadioButton {{
        background: transparent;
        color: {p.text};
    }}

    QScrollArea,
    QScrollArea > QWidget > QWidget {{
        background: transparent;
        border: none;
    }}

    QTabWidget::pane {{
        background: transparent;
        border: none;
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
        background: {p.border};
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
        background: {p.border};
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
        border-radius: 10px;
        margin-top: 14px;
        padding-top: 16px;
        padding-bottom: 12px;
        padding-left: 14px;
        padding-right: 14px;
        font-weight: 700;
        font-size: 13px;
        color: {p.text};
    }}

    QGroupBox:hover {{
        border: 1px solid {p.border_strong};
    }}

    QGroupBox::title {{
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 12px;
        top: 0px;
        padding: 3px 10px;
        background-color: {p.surface_raised};
        color: {p.text};
        font-weight: 800;
        font-size: 11px;
        letter-spacing: 0.5px;
        border: 1px solid {p.border};
        border-radius: 6px;
    }}

    QFrame#Card, QFrame#cardSurface, QFrame#headerRibbon, QFrame#operatorCard, QFrame#rndCard, QFrame#collapsibleCard {{
        background-color: {p.surface};
        border: 1px solid {p.border};
        border-radius: 8px;
    }}

    QFrame#Card:hover, QFrame#cardSurface:hover, QFrame#headerRibbon:hover, QFrame#operatorCard:hover {{
        border: 1px solid {p.border_strong};
    }}

    QFrame#collapsibleHeader {{
        background-color: {p.surface_raised};
        border-bottom: 1px solid {p.border};
        border-top-left-radius: 7px;
        border-top-right-radius: 7px;
        padding: 5px 8px;
    }}

    QPushButton#collapseToggleBtn {{
        border: none;
        background: transparent;
        color: {p.primary};
        font-size: 11px;
        font-weight: 700;
        padding: 0;
    }}

    QPushButton#collapseToggleBtn:hover {{
        background: {p.primary_light};
        border-radius: 4px;
    }}

    QLabel#collapsibleTitle {{
        font-size: 11px;
        font-weight: 700;
        color: {p.text};
    }}

    QLabel#collapsibleSubtitle {{
        font-size: 10px;
        color: {p.text_muted};
    }}

    /* All Labels placed inside Cards/GroupBoxes must be 100% transparent */
    QFrame#Card QLabel,
    QFrame#cardSurface QLabel,
    QFrame#operatorCard QLabel,
    QFrame#rndCard QLabel,
    QFrame#stepCard QLabel,
    QFrame#pageContextHeader QLabel,
    QFrame#headerRibbon QLabel,
    QFrame#collapsibleCard QLabel,
    QFrame#collapsibleHeader QLabel,
    QFrame#statCard QLabel,
    QGroupBox QLabel {{
        background: transparent;
        background-color: transparent;
    }}

    /* Cockpit Tools KPI Stat Cards */
    QFrame#statCard {{
        background-color: {p.surface};
        border: 1px solid {p.border};
        border-radius: 8px;
    }}

    QFrame#statCard:hover {{
        border: 1px solid {p.primary};
    }}

    QLabel#statIconBadge {{
        border-radius: 8px;
        font-size: 16px;
        background-color: {p.surface_raised};
        border: 1px solid {p.border};
    }}

    QLabel#statCardTitle {{
        color: {p.text_muted};
        font-size: 9.5px;
        font-weight: 800;
        letter-spacing: 0.8px;
        background: transparent;
    }}

    QLabel#statCardValue {{
        color: {p.text};
        font-size: 13px;
        font-weight: 800;
        letter-spacing: 0.2px;
        background: transparent;
    }}

    QLabel#statCardSubtitle {{
        color: {p.text_secondary};
        font-size: 10px;
        font-weight: 500;
        background: transparent;
    }}

    QLabel#CardTitle {{
        font-size: 11.5px;
        font-weight: 800;
        color: {p.text};
        letter-spacing: 0.5px;
        background: transparent;
    }}

    QLabel#fieldLabel {{
        color: {p.text_secondary};
        font-size: 11px;
        font-weight: 600;
        background: transparent;
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
        border-radius: 10px;
        padding: 10px 14px;
    }}

    QFrame#pageContextHeader {{
        background-color: {p.surface};
        border: 1px solid {p.border};
        border-radius: 10px;
        margin-bottom: 4px;
    }}

    /* Rich Typography Hierarchy */
    QLabel#pageContextTitle {{
        font-size: 15px;
        font-weight: 800;
        color: {p.text};
        letter-spacing: 0.3px;
        background: transparent;
    }}

    QLabel#pageContextSubtitle {{
        font-size: 12px;
        color: {p.text_secondary};
        line-height: 1.4;
        background: transparent;
    }}

    QLabel#eyebrowLabel {{
        color: {p.text_secondary};
        font-size: 10px;
        font-weight: 800;
        letter-spacing: 0.8px;
        text-transform: uppercase;
        background: transparent;
    }}

    QLabel#navSectionTitle {{
        color: {p.text_muted};
        font-size: 10px;
        font-weight: 800;
        letter-spacing: 0.8px;
        padding: 4px 8px;
        text-transform: uppercase;
        background: transparent;
    }}

    QLabel#headerBrandTitle, QLabel#brandLogo {{
        font-size: 15px;
        font-weight: 900;
        color: {p.text};
        letter-spacing: 0.8px;
        background: transparent;
    }}

    /* Syntax Highlighting Labels for Technical Data */
    QLabel#monoText, QLabel#hexInfoLabel {{
        font-family: "Cascadia Code", "JetBrains Mono", "Consolas", "Courier New", monospace;
        font-size: 12px;
        font-weight: 600;
        color: {p.text};
        background: transparent;
    }}

    QLabel#monoAddress {{
        font-family: "Cascadia Code", "JetBrains Mono", "Consolas", monospace;
        font-size: 12px;
        font-weight: 700;
        color: {p.accent_cyan};
        background: transparent;
    }}

    QLabel#monoCrc {{
        font-family: "Cascadia Code", "JetBrains Mono", "Consolas", monospace;
        font-size: 12px;
        font-weight: 700;
        color: {p.success};
        background: transparent;
    }}

    QLabel#monoSize {{
        font-family: "Cascadia Code", "JetBrains Mono", "Consolas", monospace;
        font-size: 12px;
        font-weight: 700;
        color: {p.accent_purple};
        background: transparent;
    }}

    QLabel#monoRegister {{
        font-family: "Cascadia Code", "JetBrains Mono", "Consolas", monospace;
        font-size: 12px;
        font-weight: 700;
        color: {p.accent_amber};
        background: transparent;
    }}

    QLabel#targetSummaryBox, QLabel#imageSummaryBox, QLabel#recommendedFlashFlow {{
        color: {p.text_muted};
        font-size: 11px;
        font-weight: 500;
        padding: 2px 0;
        background: transparent;
    }}

    QFrame#pageContextHeader {{
        background-color: {p.surface};
        border: 1px solid {p.border};
        border-radius: 8px;
        padding: 3px 10px;
    }}

    QLabel#statusBanner {{
        background-color: {p.surface_raised};
        color: {p.text};
        border: 1px solid {p.border};
        border-radius: 6px;
        padding: 2px 10px;
        font-size: 11px;
        font-weight: 600;
    }}

    QLabel#flashPlanBadge {{
        background-color: {p.surface_raised};
        color: {p.text_secondary};
        border: 1px solid {p.border};
        border-radius: 6px;
        padding: 5px 10px;
        font-weight: 600;
        font-size: 11px;
    }}

    /* Buttons */
    QPushButton {{
        background-color: {p.surface_raised};
        color: {p.text};
        border: 1px solid {p.border};
        border-radius: 7px;
        padding: 6px 14px;
        font-weight: 700;
        font-size: 12px;
        min-height: 20px;
    }}

    QPushButton:hover {{
        background-color: {p.surface};
        border-color: {p.primary};
        color: {p.text};
    }}

    QPushButton:pressed {{
        background-color: {p.surface_sunken};
        border-color: {p.border_active};
        color: {p.primary};
    }}

    QPushButton:focus {{
        border: 2px solid {p.border_active};
        padding: 5px 13px;
    }}

    QPushButton:disabled {{
        background-color: {p.canvas};
        color: {p.text_muted};
        border-color: {p.border_muted};
    }}

    QPushButton#primaryButton,
    QPushButton#flashButton,
    QPushButton#operatorFlashBtn,
    QPushButton[variant="primary"] {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #059669, stop:1 #10B981);
        color: #FFFFFF;
        border: 1px solid #10B981;
        border-radius: 7px;
        font-weight: 800;
        font-size: 12px;
        padding: 7px 18px;
    }}

    QPushButton#primaryButton:hover,
    QPushButton#flashButton:hover,
    QPushButton#operatorFlashBtn:hover,
    QPushButton[variant="primary"]:hover {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #047857, stop:1 #059669);
        border-color: #34D399;
    }}

    QPushButton#primaryButton:disabled,
    QPushButton#flashButton:disabled,
    QPushButton#operatorFlashBtn:disabled,
    QPushButton[variant="primary"]:disabled {{
        background-color: {p.surface_raised};
        color: {p.text_muted};
        border-color: {p.border};
    }}

    QPushButton#dryRunButton {{
        background-color: {p.surface};
        color: {p.text};
        border: 1px solid {p.border};
        border-radius: 7px;
        padding: 7px 16px;
        font-weight: 700;
        font-size: 12px;
    }}

    QPushButton#dryRunButton:hover {{
        background-color: {p.surface_raised};
        border-color: {p.border_strong};
    }}

    QPushButton#dangerButton, QPushButton[variant="danger"] {{
        background-color: {p.danger_light};
        color: {p.danger};
        border: 1px solid {p.danger};
        border-radius: 7px;
        font-weight: 700;
    }}

    QPushButton#dangerButton:hover, QPushButton[variant="danger"]:hover {{
        background-color: {p.danger};
        color: {p.text_on_accent};
    }}

    QPushButton#ghostButton, QPushButton[variant="outline"] {{
        background-color: transparent;
        color: {p.text_secondary};
        border: 1px solid {p.border};
        border-radius: 7px;
        padding: 6px 12px;
    }}

    QPushButton#ghostButton:hover, QPushButton[variant="outline"]:hover {{
        background-color: {p.surface_raised};
        color: {p.text};
        border-color: {p.border_strong};
    }}

    QPushButton#navButton {{
        background-color: transparent;
        color: {p.text_secondary};
        border: 1px solid transparent;
        border-radius: 7px;
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
        border-radius: 7px;
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
        border-radius: 7px;
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
        border: 1px solid {p.border};
        border-radius: 7px;
        padding: 7px 11px;
        selection-background-color: {p.primary};
        selection-color: {p.text_on_accent};
    }}

    QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus {{
        border: 1px solid {p.border_active};
        background-color: {p.surface_raised};
    }}

    QLineEdit:read-only, QPlainTextEdit:read-only {{
        background-color: {p.surface_raised};
        color: {p.text_secondary};
        border: 1px solid {p.border_muted};
    }}

    /* ComboBox */
    QComboBox {{
        background-color: {p.input_bg};
        color: {p.text};
        border: 1px solid {p.border};
        border-radius: 7px;
        padding: 6px 10px;
        min-height: 22px;
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
        width: 22px;
        border-left: 1px solid {p.border};
    }}

    QComboBox QAbstractItemView {{
        background-color: {p.surface};
        color: {p.text};
        border: 1px solid {p.border_strong};
        border-radius: 7px;
        selection-background-color: {p.surface_raised};
        selection-color: {p.primary};
        padding: 4px;
    }}

    /* Popup Menus & Context Menus (E.g. Help Menu, Context Actions) */
    QMenu {{
        background-color: {p.surface};
        color: {p.text};
        border: 1px solid {p.border_strong};
        border-radius: 8px;
        padding: 5px;
    }}

    QMenu::item {{
        background-color: transparent;
        color: {p.text};
        padding: 6px 24px 6px 12px;
        border-radius: 5px;
        font-size: 11.5px;
        font-weight: 500;
    }}

    QMenu::item:selected {{
        background-color: {p.primary};
        color: {p.text_on_accent};
        font-weight: 700;
    }}

    QMenu::item:disabled {{
        color: {p.text_muted};
    }}

    QMenu::separator {{
        height: 1px;
        background-color: {p.border};
        margin: 4px 6px;
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
        color: {p.text_secondary};
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

    /* Modern Industrial Tab Bars */
    QTabWidget::pane {{
        border: 1px solid {p.border};
        border-radius: 8px;
        background-color: {p.surface};
        top: -1px;
    }}

    QTabBar::tab {{
        background-color: {p.surface_sunken};
        color: {p.text_secondary};
        border: 1px solid {p.border};
        border-bottom: none;
        border-top-left-radius: 7px;
        border-top-right-radius: 7px;
        padding: 6px 16px;
        margin-right: 4px;
        font-weight: 700;
        font-size: 11px;
    }}

    QTabBar::tab:hover {{
        background-color: {p.surface_raised};
        color: {p.text};
    }}

    QTabBar::tab:selected {{
        background-color: {p.surface};
        color: {p.primary};
        border-color: {p.border};
        border-top: 2px solid {p.primary};
    }}

    /* Terminal & Log Viewers */
    QPlainTextEdit#terminalView, QPlainTextEdit#logView {{
        background-color: {p.terminal_bg};
        color: {p.accent_cyan};
        font-family: "Cascadia Code", "JetBrains Mono", "Consolas", monospace;
        font-size: 12px;
        border: 1px solid {p.border};
        border-radius: 8px;
        padding: 10px;
    }}

    /* Progress Bar */
    QProgressBar {{
        background-color: {p.surface_sunken};
        border: 1px solid {p.border};
        border-radius: 5px;
        text-align: center;
        color: {p.text};
        font-weight: 700;
        font-size: 11px;
        min-height: 14px;
        max-height: 14px;
    }}

    QProgressBar::chunk {{
        background-color: {p.primary};
        border-radius: 4px;
    }}

    /* Reading & Documentation Panels */
    QTextBrowser, QTextBrowser#whatsNewNotes {{
        background-color: {p.surface_raised};
        color: {p.text};
        border: 1px solid {p.border};
        border-radius: 8px;
        padding: 12px 14px;
        font-size: 13px;
        line-height: 1.5;
        selection-background-color: {p.primary};
        selection-color: {p.text_on_accent};
    }}

    QTextBrowser:focus, QTextBrowser#whatsNewNotes:focus {{
        border: 1px solid {p.border};
        outline: none;
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
        border: 1px solid {p.border_muted};
        border-radius: 8px;
        padding: 6px 8px;
    }}

    QFrame#stepCard:hover {{
        border: 1px solid {p.border};
        background-color: {p.surface_raised};
    }}

    QFrame#stepCard[state="idle"] {{
        background-color: {p.surface_raised};
        border: 1px solid {p.border_muted};
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

    QLabel#stepBadge,
    QFrame#stepCard QLabel#stepBadge {{
        background-color: {p.surface_sunken};
        color: {p.text_secondary};
        border: 1px solid {p.border};
        border-radius: 11px;
        min-width: 22px;
        max-width: 22px;
        min-height: 22px;
        max-height: 22px;
        font-family: "Cascadia Code", "JetBrains Mono", monospace;
        font-size: 10px;
        font-weight: 800;
        qproperty-alignment: AlignCenter;
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

    QLabel#stepTitle {{
        color: {p.text};
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 0.3px;
        background: transparent;
    }}

    QLabel#stepSubtitle {{
        color: {p.text_muted};
        font-size: 11px;
        background: transparent;
    }}

    QFrame#stepCard[state="active"] QLabel#stepTitle {{
        color: {p.text_on_accent};
        font-weight: 800;
    }}

    QFrame#stepCard[state="active"] QLabel#stepSubtitle {{
        color: {p.text_on_accent};
    }}

    QFrame#stepCard[state="success"] QLabel#stepTitle {{
        color: {p.text};
        font-weight: 800;
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
        background-color: {p.surface};
        border: 1px solid {p.border};
    }}

    /* Debug Workstation Mode-First Entry */
    QFrame#debugModeTile {{
        background-color: {p.surface};
        border: 1px solid {p.border};
        border-radius: 8px;
    }}
    QFrame#debugModeTile:hover {{
        background-color: {p.surface_raised};
        border-color: {p.primary};
    }}
    QPushButton#debugModeTileButton {{
        background-color: {p.surface_raised};
        color: {p.text};
        border: 1px solid {p.border_strong};
        border-radius: 4px;
        padding: 6px 12px;
        font-weight: 600;
        font-size: 12px;
    }}
    QPushButton#debugModeTileButton:hover {{
        background-color: {p.primary};
        color: #FFFFFF;
        border-color: {p.primary};
    }}

    /* Engineering Debug Workstation Splitters & Panes */
    QSplitter::handle {{
        background-color: {p.border_muted};
    }}
    QSplitter::handle:horizontal {{
        width: 3px;
    }}
    QSplitter::handle:vertical {{
        height: 3px;
    }}
    QSplitter::handle:hover {{
        background-color: {p.primary};
    }}

    QFrame#debugWorkstationToolbar QPushButton {{
        min-height: 24px;
        padding: 3px 8px;
        font-size: 11px;
        font-weight: 600;
        border-radius: 4px;
    }}

    QTableWidget#debugCallStackTable,
    QTableWidget#debugRegistersTable,
    QTableWidget#debugBreakpointsTable,
    QTreeWidget#debugSymbolsTree,
    QTreeView#debugVariablesTree {{
        background-color: {p.surface_sunken};
        border: 1px solid {p.border};
        gridline-color: {p.border_muted};
        font-size: 11px;
    }}

    QPlainTextEdit#debugSourceEditor {{
        background-color: {p.surface_sunken};
        border: 1px solid {p.border};
        color: {p.text};
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
