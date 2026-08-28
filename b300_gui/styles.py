"""Clean Modern Light styling and QSS tokens for B300 ST-Link GUI."""

from __future__ import annotations

# Clean Modern Light Design Tokens
COLOR_CANVAS = "#F8FAFC"            # Slate 50 canvas background
COLOR_SURFACE = "#FFFFFF"           # Pure white card surface
COLOR_SURFACE_MUTED = "#F1F5F9"     # Slate 100 secondary surface
COLOR_INPUT_BG = "#FFFFFF"          # Form input background
COLOR_TERMINAL_BG = "#0F172A"       # Deep dark IDE terminal background

COLOR_BORDER = "#E2E8F0"            # Slate 200 light border
COLOR_BORDER_STRONG = "#CBD5E1"     # Slate 300 control border
COLOR_BORDER_MUTED = "#F1F5F9"      # Slate 100 subtle divider

COLOR_TEXT = "#0F172A"              # Slate 900 high-contrast typography
COLOR_TEXT_SECONDARY = "#334155"    # Slate 700 standard text
COLOR_TEXT_MUTED = "#64748B"        # Slate 500 secondary typography
COLOR_TEXT_EYEBROW = "#0284C7"      # Sky 600 tech eyebrow text

COLOR_PRIMARY = "#0284C7"           # Sky Blue focus & highlight
COLOR_PRIMARY_HOVER = "#0369A1"     # Sky Blue hover
COLOR_ACCENT_GREEN = "#059669"      # Emerald 600 primary action
COLOR_ACCENT_GREEN_HOVER = "#047857"# Emerald 700 hover
COLOR_WARNING = "#D97706"           # Amber 600
COLOR_DANGER = "#DC2626"            # Red 600


APP_STYLE = """
/* Global Window & Typography */
QMainWindow, QDialog, QWidget {
    background-color: #F8FAFC;
    color: #0F172A;
    font-family: "Segoe UI", -apple-system, BlinkMacSystemFont, "Roboto", system-ui, sans-serif;
    font-size: 13px;
}

QDialog {
    background-color: #FFFFFF;
}

/* Tooltips */
QToolTip {
    background-color: #0F172A;
    color: #F8FAFC;
    border: 1px solid #334155;
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 12px;
    font-weight: 500;
}

/* Header & Brand Logo Badge */
QLabel#brandLogo {
    background-color: #FFFFFF;
    border: 1px solid #E2E8F0;
    border-radius: 10px;
    padding: 4px 10px;
}

QLabel#brandLogo:hover {
    background-color: #F8FAFC;
    border: 1px solid #0284C7;
}

QLabel#eyebrowLabel {
    color: #0284C7;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.8px;
    text-transform: uppercase;
}

QLabel#updateChannelLabel {
    color: #0369A1;
    background-color: #F0F9FF;
    border: 1px solid #BAE6FD;
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 700;
    font-family: "Cascadia Code", "Consolas", monospace;
}

QLabel#subtitleLabel {
    color: #64748B;
    font-size: 12px;
}

/* Tab Widget & Bar */
QTabWidget::pane {
    border: 1px solid #E2E8F0;
    background-color: #FFFFFF;
    border-radius: 8px;
    top: -1px;
}

QTabBar::tab {
    min-width: 140px;
    padding: 9px 18px;
    font-weight: 600;
    font-size: 13px;
    color: #64748B;
    background-color: #F1F5F9;
    border: 1px solid #E2E8F0;
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    margin-right: 4px;
}

QTabBar::tab:hover {
    color: #0284C7;
    background-color: #E2E8F0;
}

QTabBar::tab:selected {
    color: #0F172A;
    background-color: #FFFFFF;
    border-top: 3px solid #0284C7;
    border-left: 1px solid #E2E8F0;
    border-right: 1px solid #E2E8F0;
    font-weight: 700;
}

/* GroupBox Card Surface with Interactive Glow */
QGroupBox {
    background-color: #FFFFFF;
    border: 1px solid #E2E8F0;
    border-radius: 8px;
    margin-top: 10px;
    padding: 12px 10px 10px 10px;
    font-weight: 700;
    color: #0F172A;
}

QGroupBox:hover {
    border: 1px solid #BAE6FD;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 14px;
    padding: 0 8px;
    color: #0284C7;
    background-color: #FFFFFF;
    border-radius: 4px;
    font-weight: 700;
}

/* Form Controls & Inputs */
QLineEdit, QComboBox, QPlainTextEdit {
    background-color: #FFFFFF;
    color: #0F172A;
    border: 1px solid #CBD5E1;
    border-radius: 6px;
    padding: 4px 8px;
    min-height: 28px;
    selection-background-color: #E0F2FE;
    selection-color: #0369A1;
}

QLineEdit:hover, QComboBox:hover {
    border: 1px solid #94A3B8;
}

QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {
    border: 2px solid #0284C7;
}

QLineEdit:read-only {
    background-color: #F8FAFC;
    color: #475569;
    border: 1px solid #E2E8F0;
}

QComboBox {
    combobox-popup: 0;
    padding-right: 20px;
}

QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 22px;
    border-left: 1px solid #E2E8F0;
    border-top-right-radius: 6px;
    border-bottom-right-radius: 6px;
}

QComboBox QAbstractItemView {
    background-color: #FFFFFF;
    color: #0F172A;
    border: 1px solid #CBD5E1;
    border-radius: 6px;
    selection-background-color: #E0F2FE;
    selection-color: #0369A1;
    padding: 4px;
}

/* Scroll Areas */
QScrollArea {
    border: none;
    background-color: transparent;
}

QScrollArea > QWidget > QWidget {
    background-color: transparent;
}

/* Modern Slim Scrollbars */
QScrollBar:vertical {
    background-color: #F8FAFC;
    width: 10px;
    margin: 0px;
    border-radius: 5px;
}

QScrollBar::handle:vertical {
    background-color: #CBD5E1;
    min-height: 24px;
    border-radius: 5px;
}

QScrollBar::handle:vertical:hover {
    background-color: #94A3B8;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}

QScrollBar:horizontal {
    background-color: #F8FAFC;
    height: 10px;
    margin: 0px;
    border-radius: 5px;
}

QScrollBar::handle:horizontal {
    background-color: #CBD5E1;
    min-width: 24px;
    border-radius: 5px;
}

QScrollBar::handle:horizontal:hover {
    background-color: #94A3B8;
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
}

/* Rich Text / Markdown Changelog Browser */
QTextBrowser {
    background-color: #F8FAFC;
    color: #0F172A;
    border: 1px solid #E2E8F0;
    border-radius: 8px;
    padding: 12px 16px;
    font-size: 13px;
    line-height: 1.6;
    selection-background-color: #E0F2FE;
    selection-color: #0369A1;
}

/* Standard PushButtons with Elevation & Glow */
QPushButton {
    min-height: 32px;
    padding: 4px 16px;
    border-radius: 6px;
    border: 1px solid #CBD5E1;
    background-color: #FFFFFF;
    color: #1E293B;
    font-weight: 600;
}

QPushButton:hover {
    background-color: #F0F9FF;
    border-color: #0284C7;
    color: #0284C7;
}

QPushButton:pressed {
    background-color: #E0F2FE;
    border-color: #0369A1;
    color: #0369A1;
}

QPushButton:focus {
    border: 2px solid #0284C7;
}

QPushButton:disabled {
    color: #94A3B8;
    background-color: #F1F5F9;
    border-color: #E2E8F0;
}

/* Primary CTA Flash Action Button */
QPushButton#flashButton {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #059669, stop:1 #10B981);
    color: #FFFFFF;
    border: 1px solid #047857;
    font-size: 13px;
    font-weight: 700;
    min-height: 36px;
    padding: 6px 22px;
    border-radius: 6px;
}

QPushButton#flashButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #047857, stop:1 #059669);
    border-color: #065F46;
    color: #FFFFFF;
}

QPushButton#flashButton:pressed {
    background-color: #065F46;
    border-color: #047857;
    color: #FFFFFF;
}

QPushButton#flashButton:disabled {
    color: #94A3B8;
    background: #E2E8F0;
    border: 1px solid #CBD5E1;
}

/* Destructive Action Factory Provision Button */
QPushButton#factoryProvisionButton {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #DC2626, stop:1 #EA580C);
    color: #FFFFFF;
    border: 1px solid #B91C1C;
    font-size: 13px;
    font-weight: 700;
    min-height: 36px;
    padding: 6px 22px;
    border-radius: 6px;
}

QPushButton#factoryProvisionButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #B91C1C, stop:1 #C2410C);
    border-color: #991B1B;
    color: #FFFFFF;
}

QPushButton#factoryProvisionButton:pressed {
    background-color: #991B1B;
    border-color: #7F1D1D;
    color: #FFFFFF;
}

QPushButton#factoryProvisionButton:disabled {
    color: #94A3B8;
    background: #E2E8F0;
    border: 1px solid #CBD5E1;
}

/* Primary Action Update Button (Cockpit style) */
QPushButton#updateActionButton {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0284C7, stop:1 #06B6D4);
    color: #FFFFFF;
    border: 1px solid #0284C7;
    font-size: 13px;
    font-weight: 700;
    min-height: 36px;
    padding: 6px 22px;
    border-radius: 6px;
}

QPushButton#updateActionButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0369A1, stop:1 #0891B2);
    border-color: #0369A1;
    color: #FFFFFF;
}

QPushButton#updateActionButton:pressed {
    background-color: #0369A1;
    border-color: #075985;
    color: #FFFFFF;
}

QPushButton#updateActionButton:disabled {
    color: #94A3B8;
    background: #E2E8F0;
    border: 1px solid #CBD5E1;
}

/* Offline Setup Environment Button */
QPushButton#setupButton {
    background-color: #F0F9FF;
    color: #0284C7;
    border: 1px solid #BAE6FD;
    font-weight: 600;
}

QPushButton#setupButton:hover {
    background-color: #E0F2FE;
    border-color: #0284C7;
    color: #0369A1;
}

/* Status Banner with Industrial Telemetry Stripe */
QLabel#statusBanner {
    border-radius: 6px;
    padding: 6px 12px;
    background-color: #F0F9FF;
    color: #0369A1;
    font-weight: 600;
    font-size: 12px;
    border: 1px solid #BAE6FD;
    border-left: 4px solid #0284C7;
}

QLabel#statusBanner[state="normal"] {
    background-color: #F0F9FF;
    color: #0369A1;
    border: 1px solid #BAE6FD;
    border-left: 4px solid #0284C7;
}

QLabel#statusBanner[state="success"] {
    background-color: #ECFDF5;
    color: #065F46;
    border: 1px solid #A7F3D0;
    border-left: 4px solid #059669;
}

QLabel#statusBanner[state="error"] {
    background-color: #FEF2F2;
    color: #991B1B;
    border: 1px solid #FECACA;
    border-left: 4px solid #DC2626;
}

QLabel#statusBanner[state="busy"] {
    background-color: #FFFBEB;
    color: #92400E;
    border: 1px solid #FDE68A;
    border-left: 4px solid #D97706;
}

/* Progress Bar with Soft Tech Gradient */
QProgressBar {
    background-color: #F1F5F9;
    border: 1px solid #CBD5E1;
    border-radius: 6px;
    text-align: center;
    color: #0F172A;
    font-weight: 600;
    min-height: 22px;
}

QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0284C7, stop:0.5 #38BDF8, stop:1 #10B981);
    border-radius: 5px;
}

/* Plan Table & Memory Grid */
QTableWidget {
    background-color: #FFFFFF;
    color: #0F172A;
    alternate-background-color: #F8FAFC;
    gridline-color: #E2E8F0;
    border: 1px solid #CBD5E1;
    border-radius: 6px;
}

QTableWidget#memoryTable {
    background-color: #FFFFFF;
    color: #0F172A;
    alternate-background-color: #F8FAFC;
    gridline-color: #E2E8F0;
    border: 1px solid #CBD5E1;
    border-radius: 6px;
    font-family: "Cascadia Code", "Consolas", "Courier New", monospace;
    font-size: 12px;
}

QTableWidget::item {
    padding: 0px 6px;
    color: #0F172A;
}

QTableWidget#memoryTable::item {
    padding: 2px 4px;
}

QTableWidget#memoryTable::item:selected {
    background-color: #E0F2FE;
    color: #0369A1;
}

QHeaderView::section {
    background-color: #F1F5F9;
    color: #334155;
    padding: 2px 6px;
    border: none;
    border-bottom: 1px solid #CBD5E1;
    border-right: 1px solid #E2E8F0;
    font-weight: 700;
}

/* Real-time Log Consoles */
QPlainTextEdit#logView, QPlainTextEdit#debugLogView, QPlainTextEdit#factoryLogView {
    background-color: #F8FAFC;
    color: #1E293B;
    font-family: "Cascadia Code", "Consolas", "Courier New", monospace;
    font-size: 12px;
    border: 1px solid #CBD5E1;
    border-radius: 6px;
    padding: 8px;
    line-height: 1.4;
    selection-background-color: #BAE6FD;
    selection-color: #0369A1;
}

/* Read-Only Hex Preview */
QPlainTextEdit#hexView {
    background-color: #F8FAFC;
    color: #0369A1;
    font-family: "Cascadia Code", "Consolas", "Courier New", monospace;
    font-size: 12px;
    border: 1px solid #CBD5E1;
    border-radius: 6px;
    padding: 8px;
    line-height: 1.4;
    selection-background-color: #BAE6FD;
    selection-color: #0369A1;
}

/* Read-Only Warning Banner */
QLabel#readOnlyBanner {
    background-color: #EFF6FF;
    color: #1E40AF;
    border: 1px solid #BFDBFE;
    border-radius: 8px;
    padding: 10px 14px;
    font-weight: 600;
}

/* Hardware Info Cards & Chips */
QLabel#targetSummaryBox, QLabel#imageSummaryBox {
    background-color: #F8FAFC;
    border: 1px solid #E2E8F0;
    border-radius: 6px;
    padding: 6px 10px;
    color: #0F172A;
    font-family: "Cascadia Code", "Consolas", "Segoe UI", monospace;
    font-size: 12px;
}

/* Flash Plan Summary Card */
QLabel#flashPlanSummaryCard {
    background-color: #F8FAFC;
    border: 1px solid #BAE6FD;
    border-left: 4px solid #0284C7;
    border-radius: 6px;
    padding: 8px 12px;
    font-size: 12px;
    line-height: 1.4;
}

/* Metadata State Notice */
QLabel#metadataNotice {
    background-color: #F0FDF4;
    color: #166534;
    border: 1px solid #BBF7D0;
    border-radius: 6px;
    padding: 8px 12px;
    font-size: 12px;
    font-weight: 500;
}

/* Debug State Badges */
QLabel#debugStateBadge {
    border-radius: 6px;
    padding: 6px 12px;
    font-weight: 700;
    font-size: 12px;
}

QLabel#debugStateBadge[state="stopped"] {
    background-color: #F1F5F9;
    color: #64748B;
    border: 1px solid #CBD5E1;
}

QLabel#debugStateBadge[state="ready"] {
    background-color: #F0FDF4;
    color: #166534;
    border: 1px solid #BBF7D0;
}

QLabel#debugStateBadge[state="connected"] {
    background-color: #F0F9FF;
    color: #0369A1;
    border: 1px solid #BAE6FD;
}

QLabel#debugStateBadge[state="halted"] {
    background-color: #FEF3C7;
    color: #92400E;
    border: 1px solid #FDE68A;
}

QLabel#debugStateBadge[state="running"] {
    background-color: #ECFDF5;
    color: #047857;
    border: 1px solid #6EE7B7;
}

QLabel#debugStateBadge[state="failed"] {
    background-color: #FEF2F2;
    color: #991B1B;
    border: 1px solid #FECACA;
}

/* Menu Bar */
QMenuBar {
    background-color: #F8FAFC;
    color: #0F172A;
    border-bottom: 1px solid #E2E8F0;
}

QMenuBar::item:selected {
    background-color: #E0F2FE;
    color: #0284C7;
    border-radius: 4px;
}

QMenu {
    background-color: #FFFFFF;
    color: #0F172A;
    border: 1px solid #E2E8F0;
    border-radius: 8px;
    padding: 6px;
}

QMenu::item {
    padding: 6px 24px 6px 12px;
    border-radius: 4px;
}

QMenu::item:selected {
    background-color: #E0F2FE;
    color: #0284C7;
}

/* Dialogs & Message Boxes */
QDialog {
    background-color: #FFFFFF;
    color: #0F172A;
}

QMessageBox {
    background-color: #FFFFFF;
    color: #0F172A;
}

QMessageBox QLabel {
    color: #0F172A;
    font-size: 13px;
}

QMessageBox QPushButton {
    min-width: 80px;
    padding: 6px 16px;
    border-radius: 6px;
    font-weight: 600;
    background-color: #F1F5F9;
    border: 1px solid #CBD5E1;
    color: #334155;
}

QMessageBox QPushButton:hover {
    background-color: #E2E8F0;
    color: #0F172A;
}
"""
