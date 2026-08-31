# Frontend Redesign Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate the user-owned PySide6 frontend redesign onto the released v0.13.3 code without changing B300 flash, bootloader, or SSH security policies.

**Architecture:** Preserve v0.13.3 as the integration base. Commit the frontend working-tree changes to their source branch, merge that commit into a dedicated integration branch, then reconcile only presentation-layer conflicts while retaining existing service, safety, and SSH call paths.

**Tech Stack:** Python 3, PySide6, unittest, QSS, Git worktrees.

**Spec:** `docs/superpowers/specs/2026-08-31-gui-frontend-redesign.md`

## Global Constraints

- Do not alter `artifacts_oneclick/`; do not stage it or any `__pycache__/` file.
- Preserve the v0.13.3 SSH/UAC authorization fixes and all existing flash/bootloader/debug security policy.
- Keep all actions keyboard accessible, with explicit labels/tooltips and non-colour-only pass/fail feedback.
- Preserve compact layouts without horizontal scrollbars.

---

### Task 1: Preserve and import the frontend branch state

**Files:**
- Modify: `b300_gui/main_window.py`, `b300_gui/styles.py`, `b300_gui/debug_connection_panel.py`, `b300_gui/debug_tab.py`, `b300_gui/gateway_setup_tab.py`, `b300_gui/memory_tab.py`
- Create: `b300_gui/theme.py`, `b300_gui/views/*.py`, `b300_gui/widgets/*.py`
- Test: `tests/test_gui_redesign.py`

- [x] Stage only the listed source, test and specification files from `feature/gui-frontend-redesign`.
- [x] Commit them to make the user-owned frontend change an auditable Git commit.
- [x] Merge the commit into `feature/integrate-frontend-redesign`, based on `develop/v0.13.3`.
- [x] Confirm no artifact/cache file is staged.

### Task 2: Reconcile safety-sensitive integration points

**Files:**
- Modify: `b300_gui/main_window.py`
- Test: `tests/test_gui_redesign.py`, existing GUI safety tests

- [x] Add regression coverage that Operator uses the Core `TargetInfo` contract and remains disabled until MainWindow's safe plan gate is open.
- [x] Run the new coverage and observe the imported UI fail on invalid `TargetInfo` fields and a missing flash callback.
- [x] Keep target information for display only while retaining the pre-existing F407/WRP/flash-plan gate for operational readiness.
- [x] Run the regression test and GUI redesign tests.

### Task 3: Validate functional and visual integration

**Files:**
- Test: `tests/test_gui_redesign.py`, existing `tests/test_gui_*.py`

- [x] Run unit tests for the theme, header, operator view, stepper, memory map and MainWindow mode switching.
- [x] Run the repository CI's isolated-module unittest runner, `python -m compileall b300_core b300_gui`, and GUI smoke test.
- [x] Run `git diff --check`, inspect staged paths, and verify compact UI tests do not enable a horizontal scrollbar.
- [x] Commit the integration only after fresh verification evidence is recorded.
