# Installer atomic upgrade and release completion

**Goal:** Preserve the old complete runtime on failed Windows upgrades and release only after all exact-candidate gates pass.

**Architecture:** Keep the approved v0.18 production architecture. Extract the new payload before changing the installation. Back up only package-owned paths, replace the runtime, and restore after Inno rollback on failure. User files outside owned paths remain untouched.

**Spec:** ../specs/2026-09-05-release-hardening-consolidation-design.md

## Constraints

- Continue feature/release-hardening-consolidation in the existing worktree; preserve commits and WIP.
- No hardware flash, no changes to bootloader or debug safety.
- HW-P1-001 remains DEFERRED without new hardware evidence.
- No merge/tag/publish until required local and same-SHA CI/package gates pass.

## Execution checklist

- [x] Inspect both dirty files and reproduce targeted hash-rollback failure (2026-09-05: FAIL, missing runtime trees).
- [x] Extend real Inno integration tests with changed candidate contents, user data, late failure, and successful stale cleanup.
- [x] Modify packaging/windows/b300-stlink-gui.iss to stage before mutation and restore owned paths after rollback.
- [x] Run `python -m unittest tests.test_gui_packaging -v` and required GUI/CLI modules with the canonical split-case runner.
- [x] Replace workflow timeout acceptance with a bounded failing gate; verify hashes plus real candidate executable smoke after injected failure.
- [x] Build exact GUI/CLI/installer candidate and validate fresh, upgrade, rollback, OpenOCD and managed GDB.
- [x] Audit `git diff v0.18.0...HEAD`, docs, skill, version source, and remaining blockers.
- [ ] Choose patch/minor version from public behavior, update canonical version and release notes; commit and push.
- [ ] Require CI and Development packages PASS on the same final SHA across all required platforms.
- [ ] Merge, verify main, tag, run official release, and verify public hashes/signatures/manifests only after gates pass.

## Review rulings and additional closed blockers

- Complete runtime manifests were required by the approved design; ZIP/tar now stage, hash and validate all payload files. Inno consumes the same format before and after installation.
- Named VS Code configuration merging, bridge/Monitor interlocks, authenticated Monitor sessions, startup cancellation and owned-forward cleanup were required for the existing workflow. All gained regression tests.
- The supplied GDB incident was traced to an absent Gateway listener. Client now preflights it before READY. The user's test-board connection was restored without flash, with RUNNING verified after the diagnostic attach.
- Further CLI handler extraction is deferred as code organization; role semantics and safety behavior are preserved. Retained legacy consumers are classified in the companion inventory.
- Version 0.18.1 is a patch: existing public workflows and safety boundaries remain in place.
- Local evidence is recorded in docs/releases/v0.18.1-local-validation.md. Commit/CI/release completion is reported against actual remote SHAs and artifacts, without changing the tested candidate merely to update this checklist.
