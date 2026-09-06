# Large Typed Watch Batching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow compound DWARF variables such as the 167-field `xAgvInfor` to be added atomically and sampled in bounded round-robin SWD batches.

**Architecture:** Validate up to 512 typed watches as one logical set, then partition them deterministically into batches whose actual PCSR, RAM, and 64-bit coherence reads never exceed 32 words. The existing monitor loop reads one batch per interval and emits partial samples carrying batch progress; the GUI updates only returned rows and retains older values for other batches.

**Tech Stack:** Python 3.9, dataclasses, PySide6, `unittest`, OpenOCD TCL, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-07-large-typed-watch-batching-design.md`

## Global Constraints

- Each hardware transaction uses at most `MAX_LIVE_READ_WORDS == 32` reads including DWT PCSR and 64-bit coherence reads.
- Typed DWARF watches are capped at 512; manual CLI `NAME:TYPE` watches remain capped at 64.
- Compound add is atomic: validation failure adds no rows.
- Monitor remains zero-halt and requires a RUNNING target.
- Flash safety contract and `HW-P1-001` status remain unchanged.
- Python 3.9 compatibility is required on Windows x64, Ubuntu x64, and Ubuntu ARM64.

---

### Task 1: Deterministic typed-watch batch planner

**Files:**
- Modify: `b300_core/live_monitor.py`
- Modify: `b300_core/variable_watch.py`
- Test: `tests/test_live_monitor.py`
- Test: `tests/test_variable_watch.py`

**Interfaces:**
- Produces: `MAX_MANUAL_LIVE_WATCHES = 64`, `MAX_LIVE_WATCHES = 512`.
- Produces: `plan_live_watch_batches(watches: Iterable[LiveWatch]) -> Tuple[Tuple[LiveWatch, ...], ...]`.
- Consumes: existing `validate_compiled_watches`, `_word_addresses`, and `_coherence_addresses` safety rules.

- [ ] **Step 1: Write failing planner and compound tests**

Add tests proving 167 packed `u8` watches compile past the old 64 ceiling, a mixed set is split when the next watch would exceed 32 reads, every batch validates independently, order is preserved, and manual specs still reject item 65.

```python
watches = tuple(LiveWatch("v%d" % i, "u8", 0x20000000 + i, 1) for i in range(167))
batches = plan_live_watch_batches(watches)
self.assertEqual(tuple(w.name for batch in batches for w in batch), tuple(w.name for w in watches))
self.assertTrue(all(live_watch_read_count(batch) <= 32 for batch in batches))
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m unittest tests.test_live_monitor tests.test_variable_watch`

Expected: import/limit assertions fail because the planner and separate limits do not exist.

- [ ] **Step 3: Implement the minimal planner**

Introduce the two limits, separate structural validation from aggregate batch validation, add a single read-cost helper, and greedily preserve watch order while starting a new batch before cost would exceed 32.

```python
def plan_live_watch_batches(watches):
    selected = validate_compiled_watch_set(watches)
    batches = []
    current = []
    for watch in selected:
        candidate = tuple(current + [watch])
        if current and live_watch_read_count(candidate) > MAX_LIVE_READ_WORDS:
            batches.append(tuple(current))
            current = [watch]
        else:
            current.append(watch)
    if current:
        batches.append(tuple(current))
    return tuple(batches)
```

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python -m unittest tests.test_live_monitor tests.test_variable_watch`

- [ ] **Step 5: Commit**

```text
git add b300_core/live_monitor.py b300_core/variable_watch.py tests/test_live_monitor.py tests/test_variable_watch.py
git commit -m "feat: plan bounded batches for large typed watches"
```

### Task 2: Round-robin partial sampling

**Files:**
- Modify: `b300_core/live_monitor.py`
- Modify: `b300_core/live_session.py`
- Test: `tests/test_live_monitor.py`
- Test: `tests/test_live_session.py`

**Interfaces:**
- Consumes: `plan_live_watch_batches` from Task 1.
- Extends: `LiveSample` with `batch_index: int = 0` and `batch_count: int = 1`.
- Preserves: `run_live_monitor(...) -> LiveSummary` public call shape.

- [ ] **Step 1: Write failing round-robin tests**

Build a fake TCL reader and assert successive samples contain batch 1, batch 2, then batch 1; each request contains at most 32 addresses and every request contains `DWT_PCSR_ADDRESS`. Assert cancellation and final RUNNING checks still occur.

```python
run_live_monitor(tcl, symbols, compiled_watches=watches, sample_limit=3, on_sample=samples.append)
self.assertEqual([(s.batch_index, s.batch_count) for s in samples], [(0, 2), (1, 2), (0, 2)])
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m unittest tests.test_live_monitor tests.test_live_session`

Expected: the unbatched validator rejects the large logical watch set.

- [ ] **Step 3: Implement batch selection inside the sampler**

Resolve manual symbols, combine manual and typed watches, plan batches once before reading hardware, and select `batches[cycle % len(batches)]` for each iteration. Build address/coherence maps from only that batch and emit its index/count.

- [ ] **Step 4: Update session validation**

Validate manual watches against 64 and typed watches against 512 without applying the 32-read budget to the complete logical set. Call the planner so any impossible scalar fails before OpenOCD or SSH setup.

- [ ] **Step 5: Run tests and verify GREEN**

Run: `python -m unittest tests.test_live_monitor tests.test_live_session tests.test_cli_live_monitor`

- [ ] **Step 6: Commit**

```text
git add b300_core/live_monitor.py b300_core/live_session.py tests/test_live_monitor.py tests/test_live_session.py
git commit -m "feat: sample typed watches in round-robin batches"
```

### Task 3: Atomic large-struct UI and batch progress

**Files:**
- Modify: `b300_gui/views/monitor_view.py`
- Modify: `b300_gui/variable_tree_panel.py`
- Modify: `b300_gui/debug_live_panel.py`
- Modify: `b300_gui/production_live_panel.py`
- Test: `tests/test_monitor_variable_tree.py`
- Test: `tests/test_variable_tree_model.py`
- Test: `tests/test_engineering_monitor.py`

**Interfaces:**
- Consumes: 512-field collection/compilation and `LiveSample.batch_index/batch_count`.
- Produces: Vietnamese batch status and a disabled Add action after failed preflight.

- [ ] **Step 1: Write failing UI tests**

Create a catalog with 167 packed descendants, click its compound row, and assert all 167 rows appear. Feed partial samples and assert untouched rows retain their values/timestamps. Feed a two-batch sample and assert the status contains `nhóm 2/2`.

```python
tree.add_button.click()
self.assertEqual(len(panel.compiled_watches()), 167)
self.assertIn("167", tree.status.text())
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m unittest tests.test_monitor_variable_tree tests.test_variable_tree_model tests.test_engineering_monitor`

- [ ] **Step 3: Implement atomic add and failure state**

Use the batch planner as preflight before mutating the panel, retain catalog order, add every new compiled watch only after all validation succeeds, and disable the Add button when preflight rejects the current selection. Selection changes clear that rejection.

- [ ] **Step 4: Render partial/batch status**

Keep existing row values for omitted watches and append `· nhóm N/M` only when `batch_count > 1`.

- [ ] **Step 5: Run tests and verify GREEN**

Run: `python -m unittest tests.test_monitor_variable_tree tests.test_variable_tree_model tests.test_engineering_monitor tests.test_live_monitor_controller`

- [ ] **Step 6: Commit**

```text
git add b300_gui tests/test_monitor_variable_tree.py tests/test_variable_tree_model.py tests/test_engineering_monitor.py
git commit -m "fix: add and display large compound watches"
```

### Task 4: Release metadata and complete software verification

**Files:**
- Modify: `b300_version.py`
- Modify: `CHANGELOG.md`
- Test: release/version/package test modules under `tests/`

**Interfaces:**
- Produces: source version `0.21.2` and matching release notes.

- [ ] **Step 1: Add the `0.21.2` changelog entry and bump version**

Document large compound batching, 32-read preservation, UI progress, and unchanged flash safety/HW status.

- [ ] **Step 2: Run focused regression**

Run all Monitor, controller, gateway, remote, VS Code, packaging, updater, release metadata, and safety modules.

- [ ] **Step 3: Run the complete suite once on the final source SHA**

Run: `python -m unittest discover -s tests -q`

Expected: all tests pass with zero failures/errors.

- [ ] **Step 4: Run static/release checks**

```text
python -m compileall -q b300_core b300_gui
python -m scripts.release.validate_version --check-tag v0.21.2
python -m scripts.release.changelog 0.21.2 --output build/v0212-release-notes.md
git diff --check
```

- [ ] **Step 5: Commit**

```text
git add b300_version.py CHANGELOG.md
git commit -m "release: prepare v0.21.2"
```

### Task 5: Connected-hardware acceptance

**Files:**
- Read: `AGENTS.md`
- Read: `docs/03_FLASH_FIRMWARE.md`
- Read: `docs/04_DEBUG.md`
- Produce: command outputs retained in the task transcript; no source artifact is committed.

**Interfaces:**
- Consumes: one connected ST-Link, STM32F407 target, and the exact project AXF/HEX.
- Produces: read-only and bounded hardware evidence before release.

- [ ] **Step 1: Re-run hardware doctor**

Run `b300-stlink doctor --json`; require one probe, STM32F407, valid app vector/metadata, and S0-S2 WRP active.

- [ ] **Step 2: Exercise real batched Monitor**

Use the source CLI/session against `Main_V2_F407.axf`, select all typed descendants of `xAgvInfor`, capture at least one complete three-batch round, and verify target state remains RUNNING before and after cleanup.

- [ ] **Step 3: Exercise bounded local debug lifecycle**

Run debug dry-run first. Start loopback GDB/TCL, verify target identity/state and read-only commands, then perform the documented detach/cleanup and confirm ports close. This may briefly halt/resume the MCU as part of GDB attach; do not use `load`, `restore`, flash commands, register writes, or breakpoints.

- [ ] **Step 4: Exercise gateway health without exposing debug ports**

Start the managed gateway on loopback, verify advertised GDB/TCL endpoints and health JSON, then stop it and confirm endpoints close. Do not expose 3333/6666 to LAN.

- [ ] **Step 5: Validate application flash plan without writing**

Locate the exact application HEX paired with the tested AXF and run `b300-stlink flash <hex> --dry-run --json`. Require erase Sector 3-7, write/verify application, 44-byte `STLM + VERIFIED` metadata at `0x0800C000`, then `reset run`; reject any mass erase or Sector 0-2 operation.

- [ ] **Step 6: Actual flash gate**

Run an actual flash only when the exact HEX, board, and probe have been confirmed in this session. Require exact verify, metadata read-back, reset, valid PC range, and cleared BKP1R. On failure stop without automatic retry.

### Task 6: Review, merge, CI, and public release

**Files:**
- Review: all branch changes against `v0.21.1`.
- Mutate: Git branch/tag and GitHub Release only after all gates pass.

**Interfaces:**
- Produces: public GitHub release `v0.21.2` at the exact approved main SHA.

- [ ] **Step 1: Perform code review and resolve findings**

Review batching safety, cancellation, partial sample state, Python 3.9 support, and unchanged flash contract. Add failing regression tests before any corrective source change.

- [ ] **Step 2: Fast-forward `main` and push**

Require a clean worktree and final verified branch SHA.

- [ ] **Step 3: Wait for canonical main CI**

Require Windows x64, Ubuntu x64, and Ubuntu ARM64 success on the exact final SHA. Native CI is reused because this release contains no native C++ changes.

- [ ] **Step 4: Tag and publish**

Create and push `v0.21.2` only after main CI passes. Ensure the tag triggers only `Publish B300 ST-Link Tools release`.

- [ ] **Step 5: Verify public release**

Require draft=false, prerelease=false, Latest=true, 16 assets, correct version/commit in signed metadata, successful signature verification, and HTTP 200 for stable Windows/Linux/update links.
