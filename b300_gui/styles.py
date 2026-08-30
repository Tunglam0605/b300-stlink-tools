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
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.7px;
    text-transform: uppercase;
    padding-left: 2px;
}

QLabel#navSectionTitle {
    color: #94A3B8;
    font-size: 10px;
    font-weight: 700;
    padding: 4px 6px 2px 6px;
    letter-spacing: 0.5px;
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

/* Sidebar Navigation Panel - Vertical Workstation */
QFrame#sidebarPanel {
    background-color: #F8FAFC;
    border-right: 1px solid #E2E8F0;
    min-width: 196px;
    max-width: 208px;
}

QPushButton#navButton {
    background-color: transparent;
    color: #475569;
    font-size: 13px;
    font-weight: 600;
    text-align: left;
    padding: 6px 12px;
    border: none;
    border-radius: 6px;
    margin: 1px 4px;
    min-height: 36px;
    max-height: 36px;
}

QPushButton#navButton:hover {
    background-color: #F1F5F9;
    color: #0F172A;
}

QPushButton#navButton:checked, QPushButton#navButton[active="true"] {
    background-color: #E0F2FE;
    color: #0284C7;
    font-weight: 700;
    border-left: 3px solid #0284C7;
    border-top-left-radius: 2px;
    border-bottom-left-radius: 2px;
}

/* Tab Widget & Bar - Modern Engineering Workstation */
QTabWidget::pane {
    border: 1px solid #E2E8F0;
    background-color: #FFFFFF;
    border-radius: 8px;
    top: -1px;
}

QTabBar::tab {
    min-width: 140px;
    padding: 8px 20px;
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
    color: #0284C7;
    background-color: #FFFFFF;
    border-top: 3px solid #0284C7;
    border-left: 1px solid #CBD5E1;
    border-right: 1px solid #CBD5E1;
    font-weight: 700;
}

/* GroupBox Card Surface - Sleek Workstation Panels */
QGroupBox {
    background-color: #FFFFFF;
    border: 1px solid #E2E8F0;
    border-radius: 8px;
    margin-top: 12px;
    padding: 10px 10px 8px 10px;
    font-weight: 700;
    font-size: 12px;
    color: #1E293B;
}

QGroupBox:hover {
    border-color: #CBD5E1;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    top: 2px;
    padding: 2px 8px;
    color: #0369A1;
    background-color: #F0F9FF;
    border: 1px solid #BAE6FD;
    border-radius: 4px;
    font-weight: 700;
    font-size: 11px;
    letter-spacing: 0.3px;
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
    min-height: 30px;
    padding: 4px 12px;
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
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0284C7, stop:1 #0369A1);
    color: #FFFFFF;
    border: 1px solid #0369A1;
    font-size: 13px;
    font-weight: 700;
    min-height: 32px;
    padding: 6px 20px;
    border-radius: 6px;
}

QPushButton#flashButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0369A1, stop:1 #075985);
    border-color: #075985;
    color: #FFFFFF;
}

QPushButton#flashButton:pressed {
    background-color: #075985;
    border-color: #0C4A6E;
    color: #FFFFFF;
}

QPushButton#flashButton:disabled {
    color: #94A3B8;
    background: #E2E8F0;
    border: 1px solid #CBD5E1;
}

/* Secondary Factory Provision Button */
QPushButton#factoryProvisionButton {
    background-color: #FFF7ED;
    color: #C2410C;
    border: 1px solid #FED7AA;
    font-size: 13px;
    font-weight: 700;
    min-height: 32px;
    padding: 6px 18px;
    border-radius: 6px;
}

QPushButton#factoryProvisionButton:hover {
    background-color: #FFEDD5;
    border-color: #FDBA74;
    color: #9A3412;
}

QPushButton#factoryProvisionButton:pressed {
    background-color: #FDBA74;
    border-color: #FB923C;
    color: #7C2D12;
}

QPushButton#factoryProvisionButton:disabled {
    color: #94A3B8;
    background: #F1F5F9;
    border: 1px solid #E2E8F0;
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

/* Collapsible Engineering Card & Panels */
QFrame#collapsibleCard {
    background-color: #FFFFFF;
    border: 1px solid #E2E8F0;
    border-radius: 8px;
}

QFrame#collapsibleCard:hover {
    border-color: #CBD5E1;
}

QFrame#collapsibleHeader {
    background-color: #F8FAFC;
    border-top-left-radius: 7px;
    border-top-right-radius: 7px;
    border-bottom: 1px solid #E2E8F0;
    padding: 6px 12px;
}

QFrame#collapsibleHeader[expanded="false"] {
    border-bottom: none;
    border-radius: 7px;
}

QLabel#collapsibleTitle {
    font-weight: 700;
    font-size: 13px;
    color: #0F172A;
}

QLabel#collapsibleSubtitle {
    color: #64748B;
    font-size: 11px;
    font-weight: 500;
}

/* Live Monitor & Workstation Tables */
QTableWidget#timelineTable, QTableWidget#liveVariablesTable {
    background-color: #FFFFFF;
    color: #0F172A;
    alternate-background-color: #F8FAFC;
    gridline-color: #F1F5F9;
    border: 1px solid #E2E8F0;
    border-radius: 6px;
    font-family: "Cascadia Code", "Consolas", "Courier New", monospace;
    font-size: 12px;
}

QTableWidget#timelineTable::item, QTableWidget#liveVariablesTable::item {
    padding: 4px 6px;
}

QTableWidget#timelineTable::item:selected, QTableWidget#liveVariablesTable::item:selected {
    background-color: #E0F2FE;
    color: #0369A1;
}

/* Count & Filter Badges */
QLabel#badgeInfo {
    background-color: #F0F9FF;
    color: #0369A1;
    border: 1px solid #BAE6FD;
    border-radius: 10px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 700;
    font-family: "Cascadia Code", "Consolas", monospace;
}

QLabel#badgeWarn {
    background-color: #FFFBEB;
    color: #B45309;
    border: 1px solid #FDE68A;
    border-radius: 10px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 700;
    font-family: "Cascadia Code", "Consolas", monospace;
}

QLabel#badgeError {
    background-color: #FEF2F2;
    color: #B91C1C;
    border: 1px solid #FECACA;
    border-radius: 10px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 700;
    font-family: "Cascadia Code", "Consolas", monospace;
}

/* Interactive Debug Amber Warning Banner */
QFrame#interactiveDebugWarning {
    background-color: #FFFBEB;
    border: 1px solid #FDE68A;
    border-left: 4px solid #D97706;
    border-radius: 6px;
    padding: 8px 12px;
}

QLabel#interactiveDebugWarningText {
    color: #92400E;
    font-size: 12px;
    font-weight: 600;
}

/* Primary Live Monitor Action Button */
QPushButton#liveStartButton {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #059669, stop:1 #10B981);
    color: #FFFFFF;
    border: 1px solid #047857;
    font-weight: 700;
    border-radius: 6px;
    padding: 4px 16px;
}

QPushButton#liveStartButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #047857, stop:1 #059669);
    border-color: #065F46;
}

QPushButton#liveStartButton:disabled {
    color: #94A3B8;
    background: #E2E8F0;
    border-color: #CBD5E1;
}

/* Secondary Action Button */
QPushButton#secondaryActionBtn {
    background-color: #F8FAFC;
    color: #334155;
    border: 1px solid #CBD5E1;
    font-weight: 600;
    border-radius: 6px;
    padding: 4px 12px;
}

QPushButton#secondaryActionBtn:hover {
    background-color: #F1F5F9;
    color: #0F172A;
    border-color: #94A3B8;
}

/* Health Badge styling */
QLabel#healthLifecycleBadge {
    border-radius: 6px;
    padding: 6px 14px;
    font-weight: 700;
    font-size: 12px;
    font-family: "Cascadia Code", "Consolas", monospace;
}

QLabel#healthLifecycleBadge[lifecycle="BOOTABLE"] {
    background-color: #ECFDF5;
    color: #047857;
    border: 1px solid #6EE7B7;
}

QLabel#healthLifecycleBadge[lifecycle="NON_BOOTABLE"] {
    background-color: #FEF2F2;
    color: #991B1B;
    border: 1px solid #FECACA;
}

QLabel#healthLifecycleBadge[lifecycle="STALE"] {
    background-color: #F1F5F9;
    color: #64748B;
    border: 1px solid #CBD5E1;
}

/* Flash Plan Cards & Factory Profile */
QLabel#flashPlanSummaryCard {
    background-color: #F0F9FF;
    color: #0369A1;
    border: 1px solid #BAE6FD;
    border-left: 3px solid #0284C7;
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 12px;
}

QLabel#flashPlanBadge {
    background-color: #F8FAFC;
    color: #475569;
    border: 1px solid #E2E8F0;
    border-radius: 6px;
    padding: 4px 8px;
    font-weight: 600;
    font-size: 11px;
    font-family: "Cascadia Code", "Consolas", monospace;
}

QLabel#factoryBootloaderProfileInfo {
    background-color: #F8FAFC;
    color: #334155;
    border: 1px solid #E2E8F0;
    border-radius: 6px;
    padding: 8px 10px;
    font-family: "Cascadia Code", "Consolas", monospace;
    font-size: 11px;
    line-height: 1.4;
}


/* RC3 UX hierarchy: persistent page context + role-based remote workflow */
QFrame#pageContextHeader {
    background-color: #FFFFFF;
    border: 1px solid #E2E8F0;
    border-radius: 8px;
}

QLabel#pageContextTitle, QLabel#pageTitle {
    color: #0F172A;
    font-size: 17px;
    font-weight: 800;
}

QLabel#pageContextSubtitle, QLabel#pageSubtitle {
    color: #64748B;
    font-size: 12px;
    line-height: 1.3;
}

QLabel#roleSectionTitle {
    color: #0F172A;
    font-size: 13px;
    font-weight: 800;
}

QLabel#formLabel {
    color: #64748B;
    font-size: 11px;
    font-weight: 700;
}

QFrame#gatewayHero {
    background-color: #FFFFFF;
    border: 1px solid #E2E8F0;
    border-radius: 10px;
}

QLabel#rolePrompt {
    color: #475569;
    font-weight: 700;
    padding-right: 4px;
}

QPushButton#roleToggle {
    background-color: #F8FAFC;
    color: #475569;
    border: 1px solid #CBD5E1;
    border-radius: 7px;
    padding: 6px 12px;
    min-height: 34px;
    font-weight: 650;
}

QPushButton#roleToggle:hover {
    background-color: #F0F9FF;
    color: #0369A1;
    border-color: #7DD3FC;
}

QPushButton#roleToggle:checked {
    background-color: #E0F2FE;
    color: #0369A1;
    border: 2px solid #38BDF8;
    font-weight: 800;
}

QLabel#nextActionBanner {
    background-color: #EFF6FF;
    color: #1E40AF;
    border: 1px solid #BFDBFE;
    border-left: 4px solid #3B82F6;
    border-radius: 7px;
    padding: 7px 10px;
    font-weight: 650;
}

QLabel#nextActionBanner[state="warning"] {
    background-color: #FFFBEB;
    color: #92400E;
    border-color: #FDE68A;
    border-left-color: #D97706;
}

QLabel#nextActionBanner[state="success"] {
    background-color: #ECFDF5;
    color: #065F46;
    border-color: #A7F3D0;
    border-left-color: #059669;
}

QFrame#workflowStepHeader {
    background-color: transparent;
    border: none;
    padding-top: 2px;
}

QLabel#workflowStepBadge {
    background-color: #0284C7;
    color: white;
    border-radius: 12px;
    font-size: 11px;
    font-weight: 800;
}

QScrollArea#workflowScroll {
    background-color: transparent;
    border: none;
}

QScrollArea#workflowScroll > QWidget > QWidget {
    background-color: #F8FAFC;
}

QLabel#gatewaySetupStatus, QLabel#clientProfileStatus, QLabel#clientConnectionStatus {
    background-color: #F8FAFC;
    color: #475569;
    border: 1px solid #E2E8F0;
    border-radius: 6px;
    padding: 6px 9px;
    font-weight: 650;
}

QLabel#gatewaySetupStatus[state="ready"],
QLabel#clientProfileStatus[state="ready"],
QLabel#clientConnectionStatus[state="ready"] {
    background-color: #ECFDF5;
    color: #065F46;
    border-color: #A7F3D0;
}

QLabel#gatewaySetupStatus[state="warning"] {
    background-color: #FFFBEB;
    color: #92400E;
    border-color: #FDE68A;
}

QLabel#gatewaySetupStatus[state="error"],
QLabel#clientProfileStatus[state="error"],
QLabel#clientConnectionStatus[state="error"] {
    background-color: #FEF2F2;
    color: #991B1B;
    border-color: #FECACA;
}

QLabel#safetyNote, QLabel#infoNote {
    background-color: #F8FAFC;
    color: #64748B;
    border: 1px solid #E2E8F0;
    border-radius: 6px;
    padding: 7px 9px;
    font-size: 11px;
}


QFrame#debugSafetyGuide {
    background-color: #FFFFFF;
    border: 1px solid #E2E8F0;
    border-radius: 8px;
}

QLabel#debugSafeModeBadge {
    background-color: #ECFDF5;
    color: #065F46;
    border: 1px solid #A7F3D0;
    border-radius: 5px;
    padding: 4px 8px;
    font-size: 11px;
    font-weight: 800;
}

QLabel#debugIntrusiveModeBadge {
    background-color: #FFFBEB;
    color: #92400E;
    border: 1px solid #FDE68A;
    border-radius: 5px;
    padding: 4px 8px;
    font-size: 11px;
    font-weight: 750;
}


QLabel#recommendedFlashFlow {
    background-color: #EFF6FF;
    color: #1E40AF;
    border: 1px solid #BFDBFE;
    border-radius: 6px;
    padding: 7px 10px;
    font-size: 11px;
    font-weight: 700;
}

QPushButton#dryRunButton {
    background-color: #F0F9FF;
    color: #0369A1;
    border: 1px solid #7DD3FC;
    border-radius: 6px;
    min-height: 32px;
    padding: 6px 16px;
    font-weight: 700;
}

QPushButton#dryRunButton:hover {
    background-color: #E0F2FE;
    border-color: #38BDF8;
}

QLabel#factoryWarningNote {
    background-color: #FFF7ED;
    color: #9A3412;
    border: 1px solid #FED7AA;
    border-left: 4px solid #F97316;
    border-radius: 6px;
    padding: 7px 9px;
    font-size: 11px;
    font-weight: 650;
}

QPushButton#cancelOperationButton {
    background-color: #FEF2F2;
    color: #991B1B;
    border: 1px solid #FECACA;
    border-radius: 6px;
    min-height: 28px;
    font-weight: 650;
}


QLabel#memoryReadOnlyNotice {
    background-color: #FFF7ED;
    color: #9A3412;
    border: 1px solid #FED7AA;
    border-left: 4px solid #F97316;
    border-radius: 6px;
    padding: 7px 10px;
    font-size: 11px;
    font-weight: 650;
}

QLabel#memoryOperationStatus {
    background-color: #F8FAFC;
    color: #475569;
    border: 1px solid #E2E8F0;
    border-radius: 6px;
    padding: 6px 9px;
    font-size: 11px;
    font-weight: 650;
}

QPushButton#memoryCancelButton {
    background-color: #FEF2F2;
    color: #991B1B;
    border: 1px solid #FECACA;
    border-radius: 6px;
    font-weight: 650;
}

/* RC3 compact action hierarchy */
QPushButton#gatewayPrepareButton,
QPushButton#gatewayTrustHostButton,
QPushButton#gatewayClientConnectButton,
QPushButton#gatewayIdentityPrepareButton {
    min-height: 34px;
    background-color: #0284C7;
    color: #FFFFFF;
    border: 1px solid #0284C7;
    font-weight: 750;
}

QPushButton#gatewayPrepareButton:hover,
QPushButton#gatewayTrustHostButton:hover,
QPushButton#gatewayClientConnectButton:hover,
QPushButton#gatewayIdentityPrepareButton:hover {
    background-color: #0369A1;
    color: #FFFFFF;
    border-color: #0369A1;
}

QPushButton#gatewayRefreshButton,
QPushButton#gatewaySelfTestButton,
QPushButton#gatewayCopyClientButton,
QPushButton#gatewayShowHostKeyButton,
QPushButton#gatewayCopyHostFingerprintButton,
QPushButton#gatewayIdentityCopyButton,
QPushButton#gatewayAuthorizeKeyButton {
    min-height: 34px;
    background-color: #FFFFFF;
    color: #334155;
    border: 1px solid #CBD5E1;
    font-weight: 650;
}

QTableWidget#gatewaySetupCheckTable {
    font-size: 12px;
    gridline-color: #E2E8F0;
}

QTableWidget#gatewaySetupCheckTable QHeaderView::section {
    min-height: 28px;
    padding: 4px 8px;
    font-size: 11px;
    font-weight: 750;
}

QProgressBar#gatewayProgress {
    min-height: 8px;
    max-height: 12px;
}
"""
