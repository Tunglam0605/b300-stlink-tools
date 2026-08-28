# Safe Application and Factory Provisioning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the unsupported Application marker contract and add a separately guarded, bundled-artifact Factory Bootloader provisioning workflow.

**Architecture:** Keep Application policy/service command generation free of WRP operations. Add separately typed factory policy, resource, OpenOCD, and service modules; expose them only through a new CLI command and dedicated GUI tab. Validate the pinned resource at runtime and during packaging.

**Tech Stack:** Python 3 standard library, `unittest`, PySide6, PyInstaller, OpenOCD command generation.

**Spec:** `docs/superpowers/specs/2026-08-28-safe-application-factory-provisioning-design.md`

## Global Constraints

- Do not run real ST-Link/OpenOCD flash, protect, debug, halt, or reset commands.
- Never emit mass/chip erase or RDP lock/unlock.
- Application mode erases exactly sectors 3--7 and never emits `flash protect`.
- Factory mode accepts only the pinned bundled Bootloader and writes only sectors 0--2.
- Leave all changes uncommitted in the current working tree for user review.

---

### Task 1: Application safety contract

**Files:**
- Modify: `tests/test_core_openocd.py`, `tests/test_flash_service.py`, `tests/test_b300_stlink.py`
- Modify: `b300_core/models.py`, `b300_core/policy.py`, `b300_core/openocd.py`, `b300_core/service.py`, `b300_stlink.py`

**Interfaces:**
- Produces: `FlashResult` without marker state; two dry-run transactions (`program_verify`, `reset`); `BootVerification(pc, bkp1r, passed, reason)`.

- [ ] Write tests requiring no marker/BKP4 references, exact sectors 3--7, separate conditional reset, no protection commands, and PC+BKP1R post-verification.
- [ ] Run the focused tests and confirm failures come from the old marker contract.
- [ ] Remove marker constants/builders/result fields/phases and update post-verification parsing.
- [ ] Run the focused tests until green.

### Task 2: Trusted Bootloader policy and resource

**Files:**
- Create: `resources/firmware/b300_bootloader_f407ze_com3_v00050000.hex`
- Create: `resources/firmware/b300_bootloader_manifest.json`
- Create: `b300_core/factory_resource.py`, `b300_core/factory_policy.py`
- Modify: `b300_core/hex_image.py`, `b300_core/models.py`
- Test: `tests/test_factory_policy.py`, `tests/test_factory_resource.py`

**Interfaces:**
- Produces: `inspect_bootloader_image(path) -> ImageInfo`, `load_trusted_bootloader() -> TrustedBootloader`, `FactoryPlan`/`FactoryPreview` fixed to sectors `(0, 1, 2)`.

- [ ] Copy the audited tracked artifact without modifying bytes and write literal manifest provenance/hash/range fields.
- [ ] Write tests for exact range acceptance/rejection, manifest/hash failure, vector plausibility, and target identity.
- [ ] Run tests and confirm the new interfaces are missing.
- [ ] Implement generic bounded HEX inspection plus fail-closed resource and factory policy validation.
- [ ] Run the focused tests until green.

### Task 3: Factory OpenOCD orchestration and CLI

**Files:**
- Create: `b300_core/factory_openocd.py`, `b300_core/factory_service.py`
- Modify: `b300_core/models.py`, `b300_core/openocd.py`, `b300_stlink.py`
- Test: `tests/test_factory_openocd.py`, `tests/test_factory_service.py`, `tests/test_b300_stlink.py`

**Interfaces:**
- Produces: exact protect-off, erase/program/verify, protect-on, reset and inspect command builders; `FactoryService.provision(plan, ...)`; CLI `provision-bootloader`.

- [ ] Write tests for dry-run order/conditions, confirmation requirement, exact `0 0 2` ranges, exact verify event, re-protection, and absence of forbidden commands.
- [ ] Run focused tests and confirm failures describe the missing factory path.
- [ ] Add structured sector protection parsing and the separate factory service/CLI flow.
- [ ] Run focused tests until green.

### Task 4: Separate guarded GUI surface

**Files:**
- Create: `b300_gui/factory_tab.py`
- Modify: `b300_gui/main_window.py`, `b300_gui/viewmodels.py`, `b300_gui/operation_state.py`
- Test: `tests/test_gui_viewmodels.py`, `tests/test_gui_smoke.py`

**Interfaces:**
- Produces: separate `FactoryTab` with immutable artifact display, dry-run-first state, typed acknowledgement, and distinct provision signal.

- [ ] Write offscreen GUI tests proving Application and Factory actions are separate and Factory real action stays disabled until every guard passes.
- [ ] Run tests and confirm failure from the absent surface.
- [ ] Implement the factory tab using the existing design system and shared exclusive service lock.
- [ ] Run focused GUI tests until green.

### Task 5: Packaging, playbooks, skill, and operator docs

**Files:**
- Modify: `build_native_bundle.py`, `package_internal.py`, `b300_gui.spec`
- Modify: `AGENTS.md`, `.agents/skills/b300-ota-stlink/**`, `README.md`, `CONTRIBUTING.md`, `docs/00_START_HERE.md`, `docs/03_FLASH_FIRMWARE.md`, `docs/05_TROUBLESHOOTING.md`, `docs/06_AI_AGENT_MANUAL.md`, `docs/07_GUI_WINDOWS_UBUNTU.md`, `docs/08_RELEASE_ACCEPTANCE.md`
- Modify: packaging/release tests as required.

**Interfaces:**
- Produces: both native executables and both archive flavors containing `resources/firmware`; updated low-freedom safety instructions.

- [ ] Write packaging tests that include trusted resources only after validation and preserve their archive path.
- [ ] Run packaging tests and confirm failure.
- [ ] Add PyInstaller/archive resource wiring and build-time trust validation.
- [ ] Replace marker documentation with metadata-erased behavior and document the factory-only WRP exception/usage.
- [ ] Run focused packaging/documentation tests until green.

### Task 6: Offline verification and review

**Files:** all changed files.

- [ ] Run `python -m unittest discover -s tests -q`.
- [ ] Run syntax compilation and dry-run CLI smoke commands using temporary/synthetic inputs only.
- [ ] Search the active source/docs for marker/BKP4/forbidden OpenOCD command residue.
- [ ] Review `git diff --check`, `git diff --stat`, and the complete diff for safety regressions.
- [ ] Report exact CLI usage, tests, provenance, unrun hardware validation, and remaining risks without committing.
