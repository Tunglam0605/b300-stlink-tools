# Technical Specification: B300 GUI Frontend Modern Redesign

- **Date**: 2026-08-31
- **Branch**: `feature/gui-frontend-redesign`
- **Scope**: Complete UX/UI overhaul of `b300_gui` desktop application (PySide6).

## 1. Overview & Objectives

The goal is to modernize the B300 ST-Link desktop GUI into a high-performance, aesthetically pleasing, and dual-purpose industrial tool supporting two distinct workflows:
1. **Operator Production Mode (Vận hành sản xuất)**: Fast, safe, 1-click firmware provisioning with automated probe detection, clear pipeline stepper, large pass/fail indicators, and strict prevention of accidental destructive actions.
2. **Engineering R&D Mode (Kỹ sư R&D)**: Full telemetry workbench featuring detailed flash diagnostics, color-coded visual memory map, live variable monitor & realtime plotting, hardware register inspection, and SSH gateway management.

Key improvements:
- **Dynamic Theming Engine**: Switch between Modern High-Contrast Dark Mode and Clean Studio Light Mode with `Ctrl+T` or toolbar toggle.
- **Modern Compact Sidebar & Slim Header**: Space-efficient 64px compact icon sidebar (expandable to 200px), eliminating redundant nested headers.
- **Modular Component Architecture**: Decouple monolithic `main_window.py` (~2000 lines) into dedicated view controllers and reusable widgets under `b300_gui/views/` and `b300_gui/widgets/`.

---

## 2. Design System & Theme Engine (`b300_gui/theme.py`)

### 2.1 Theme Tokens
- **Dark Palette**:
  - Canvas: `#0B0F17` (Deep Obsidian)
  - Card/Surface: `#151D2A` (Slate Dark)
  - Surface Raised / Header: `#1E293B` (Slate 800)
  - Input / Terminal BG: `#090D14`
  - Border Subdued: `#1E293B`
  - Border Active: `#38BDF8`
  - Text Primary: `#F8FAFC`
  - Text Muted: `#94A3B8`
  - Primary Accent: `#0EA5E9` (Sky Blue)
  - Success: `#10B981` (Emerald Green)
  - Danger: `#EF4444` (Coral Red)
  - Warning: `#F59E0B` (Amber)

- **Light Palette**:
  - Canvas: `#F8FAFC` (Slate 50)
  - Card/Surface: `#FFFFFF` (Pure White)
  - Surface Raised / Header: `#F1F5F9` (Slate 100)
  - Input / Terminal BG: `#0F172A` (Dark Terminal for contrast)
  - Border Subdued: `#E2E8F0`
  - Border Active: `#0284C7`
  - Text Primary: `#0F172A`
  - Text Muted: `#64748B`
  - Primary Accent: `#0284C7`
  - Success: `#059669`
  - Danger: `#DC2626`
  - Warning: `#D97706`

### 2.2 ThemeManager
- Implemented in `b300_gui/theme.py`.
- Manages `current_theme` ("dark" or "light") and persists in `QSettings("B300", "STLinkTools")`.
- Generates complete, optimized QSS stylesheets for widgets, tables, combo boxes, scrollbars, dialogs, and buttons.
- Emits `theme_changed(str)` signal to notify custom-painted widgets (e.g. Memory Map bar, Live Plot graphs) to update palettes.

---

## 3. Navigation & App Shell

### 3.1 Slim Header Bar (`b300_gui/widgets/header_bar.py`)
- Brand chip: "⚡ B300 Tools" with version indicator.
- Segmented Mode Pill Switcher: `[⚡ Vận hành Sản xuất]` | `[🔬 Kỹ sư R&D]`.
- Active Probe Status Pill: Displays probe name + serial + 1-click refresh icon.
- Quick Actions: Theme toggle (☀️ / 🌙), Machine Setup Wizard button, Help / About button.

### 3.2 Compact Sidebar (`b300_gui/widgets/compact_sidebar.py`)
- Width: 64px in compact mode (clean vector/emoji icons with tooltips) or 200px in expanded mode.
- Navigation items:
  - In **Production Mode**: Shows single focus tab "Nạp sản xuất" (Operator Flash).
  - In **R&D Mode**: Shows 4 specialized workspaces:
    1. "Nạp Firmware (R&D)"
    2. "Memory Map & Metadata"
    3. "Live Debug & Plot"
    4. "Gateway Setup & SSH"
- Bottom footer: Expand/Collapse toggle button.

### 3.3 Status Footer Bar
- Left: OpenOCD daemon & ST-Link connection status (e.g., `ST-Link: 127.0.0.1:3333`).
- Center: Target CPU state (`CPU: RUNNING` / `CPU: HALTED`).
- Right: Active operation progress indicator / task status.

---

## 4. Specialized Workspaces & Views

### 4.1 Operator Production View (`b300_gui/views/operator_view.py`)
- **Large Probe Card**: Clear display of detected ST-Link probe with big status badge ("SẴN SÀNG" / "CHƯA KẾT NỐI").
- **HEX File Selector Card**: File picker with recent files, auto-detecting size, entry point, CRC32, and sector boundary validation.
- **Large Action Buttons**:
  - `[🔍 KIỂM TRA (DRY-RUN)]`
  - `[⚡ NẠP FIRMWARE (PROVISION)]` (High visibility primary action button)
- **Pipeline Stepper**: Visual 6-step progress indicator:
  1. Probe & Target Detection
  2. HEX Analysis & Bounds Check
  3. Safe Sector Erase (S3-S7, S0-S2 untouched)
  4. Write & Verify Application
  5. Inject & Verify 44-byte STLM Verified Metadata at `0x0800C000`
  6. Safe Reset & Run Verification
- **PASS / FAIL Banner**: Full-width high-visibility alert banner displayed immediately upon completion with duration and status details.
- **Collapsible Log Drawer**: Compact drawer that can be expanded if an error occurs.

### 4.2 Engineering R&D Workspaces
- **R&D Flash View**: Includes advanced dry-run inspection, bootloader factory provisioning with type-to-confirm dialog (`PROVISION BOOTLOADER`), target health check, and live openocd output viewer.
- **Visual Memory Map View (`b300_gui/widgets/memory_map_widget.py`)**:
  - Interactive graphical memory bar dividing Flash:
    - Sector 0--2: `0x08000000 - 0x0800BFFF` (Bootloader - Protected WRP)
    - Sector 3: `0x0800C000 - 0x0800FFFF` (AppMeta STLM 44 bytes)
    - Sector 4--7: `0x08010000 - 0x0807FFFF` (Application Area)
  - Quick inspection tables for AppMeta record, Vector Table, Option Bytes/WRP, and BKP1R register.
  - Interactive Hex Dump viewer.
- **Live Debug Studio**:
  - Live Watch table with variable value polling and color-coded change highlights.
  - Realtime plotting panel with Dark/Light palette integration.
  - Hardware breakpoint & stack frame inspector.
- **Gateway Setup View**:
  - SSH readiness checklist and 1-click client configuration.

---

## 5. File & Directory Structure

```
b300_gui/
├── __init__.py
├── __main__.py
├── theme.py                     # [NEW] ThemeManager & Dynamic Dark/Light QSS
├── styles.py                    # Legacy style fallback / token exporter
├── main_window.py               # Refactored Main Window Shell
├── widgets/                     # [NEW] Reusable UI Components
│   ├── __init__.py
│   ├── header_bar.py            # Slim Top Header with Mode & Theme Switcher
│   ├── compact_sidebar.py       # Compact/Expanded Nav Sidebar
│   ├── pipeline_stepper.py      # Visual 6-Step Flash Progress Stepper
│   ├── memory_map_widget.py     # Color-coded Graphical Memory Map Bar
│   └── pass_fail_banner.py      # Large Operator PASS/FAIL result banner
├── views/                       # [NEW] Workspace Views
│   ├── __init__.py
│   ├── operator_view.py         # Dedicated 1-Click Production View
│   ├── rnd_flash_view.py        # Detailed R&D Flash Workbench
│   ├── memory_view.py           # Memory Map & Metadata Inspector
│   ├── debug_studio_view.py     # Live Debug, Watch, & Realtime Plot Studio
│   └── gateway_view.py          # SSH Gateway Setup Assistant
└── ... (existing dialogs and helpers)
```

---

## 6. Verification & Quality Gates
1. **Theme Switching**: Test `ThemeManager` toggle and QSS generation for both dark and light modes.
2. **Smoke Test**: `python -m b300_gui --smoke-test` exits 0 cleanly.
3. **Unit Tests**: Add tests covering new widgets, view controllers, theme switching, and mode toggling (`tests/test_gui_redesign.py`).
4. **All Core Tests**: `python -m unittest discover -s tests -q` passes without regressions.
