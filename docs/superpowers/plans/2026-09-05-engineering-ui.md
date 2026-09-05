# Engineering UI implementation plan

**Goal:** Deliver the supplied five-page engineering reference as a coherent, shared-context desktop UI, ready for screenshot review.
**Architecture:** Existing profile stores and GatewaySessionManager remain authoritative for persistence and authentication. A small GUI AppContext owns current selection and evidence, a single SharedContextBar renders it above the four working pages, and page controllers consume that snapshot. Flash and live polling services remain unchanged.
**Tech Stack:** Python, PySide6, existing QSS, unittest isolated runner.
**Spec:** ../specs/2026-09-05-engineering-ui-request.md (user request, authoritative).
**Execution:** subagent-driven-development for bounded independent components; coordinator owns integration and final regression.

## Audit / baseline

- Branch: refactor/v0.20-engineering-ui, based on fcf70c22 (stable v0.19.1), carrying the user's reviewed frontend edits. Work stays in the requested canonical checkout.
- Existing ProjectProfileStore has workspace/symbols but no HEX/target-family. Existing records require exact schema 1; extend with explicit migration and preserve IDs/defaults.
- GatewayProfileStore already migrates the legacy single endpoint. Reuse without a second credential store; Local is a built-in connection alongside named SSH profiles.
- GatewaySessionManager already holds passwords in RAM and disconnect_all clears them. Reuse session identities across pages; no new SSH implementation.
- MainWindowV18 wires safety and shared services but page selectors independently retain selection. Header mode sync does not constitute global Project/Connection context.
- PROGRAM already performs fresh inspection, target/WRP/RDP validation, HEX plan, canonical confirmation and guarded transaction. Preserve these handlers and their tests.
- MONITOR owns LiveMonitorController/DebugLivePanel. Keep its RAM/DWT polling budgets; reorganize presentation and add charts from received samples only.
- Obsolete hidden mode/project/endpoint widgets and per-widget inline styles remain. Remove only after production consumers migrate; keep compatibility window tests separate.
- Existing shared cards/log components can be unified. Device sidebar currently repeats all hardware facts on every page; replace with page-specific quick summaries.

## Global constraints

- Exactly PROGRAM / MONITOR / DEBUG VS CODE / DEVICE / SETTINGS. No workbench or IDE recreation.
- No release, version bump, push, hardware access, flash, or SSH in this implementation task. Commit source for user review.
- Core flash safety and HardwareSession semantics unchanged. HW-P1-001 remains OPEN / DEFERRED.
- Reference is visual only. Unknown/pending/disconnected are neutral; red only for an actual failed check. Unsupported actions disabled with explanation.
- Toàn bộ chuỗi hiển thị trong giao diện production và các hộp thoại được mở từ đó phải dùng tiếng Việt nhất quán; giữ nguyên tên riêng và thuật ngữ kỹ thuật như ST-Link, OpenOCD, GDB, VS Code, SSH, ELF/AXF, WRP/RDP.
- Preserve saved profiles, defaults and settings; never persist credentials.
- Render and inspect five pages at 1366x768, 1600x900, 1920x1080 and 2560x1440.

## Tasks and acceptance

1. **Profiles + AppContext + SharedContextBar** — `b300_core/project_profiles.py`, `b300_gui/app_context.py`, `widgets/shared_context_bar.py`, `project_manager_dialog.py`, `tests/test_app_context.py`, `tests/test_project_profiles.py`.
   - [x] RED: schema-1 load, HEX extension roundtrip, one selection propagation, probe/connection invalidation, busy selection refusal, no secret serialization.
   - [x] Add optional application_hex/target_family; retain existing four positional fields. AppContext.selected_project/selected_connection/selected_probe/target_info/hardware_busy, changed signal; `select_project(id)`, `select_connection(id)`, `select_probe(serial)`.
   - [x] Bar observes context and emits manager requests; no network calls or duplicate stores. GREEN targeted tests and review.
2. **Shared components and monitor presentation** — `widgets/engineering.py`, `views/monitor_view.py`, new production live panel/trend component; preserve existing live controller interface.
   - [x] RED sample-driven values/filter/trend, bounded recent samples, supported refresh intervals, unchanged start/stop/controller ownership.
   - [x] One shared log/card/status system; large variable table + numeric trend + recent samples; no VS Code launch pane or repeated symbol/role selector.
   - [x] GREEN widget tests, review safe zero-halt requests unchanged.
3. **Production integration / page ownership** — `main_window_v18.py`, new context orchestration adapter, DEBUG view, SETTINGS view, DEVICE view, PROGRAM view.
   - [x] RED global selection controls all three consumers; local/SSH profile routing, reusable authenticated session, busy interlocks, close clears session, no duplicate mode/endpoint/file selection in primary UI.
   - [x] Resolve project HEX for PROGRAM; symbols for MONITOR; workspace/symbols for VS Code. Default action consumes context, override secondary only.
   - [x] Preserve preflight exact semantics; remote programming/unsupported diagnostics remain explicitly unavailable rather than operating local hardware accidentally.
   - [x] Host Gateway setup belongs SETTINGS, separate from connection manager. Reuse existing readiness/setup handlers and controlled start/stop.
4. **Visual unification and responsive shell** — shared QSS/tokens, compact header/nav, semantic icons, page-specific sidebar, actual model flash bar, compact logs.
   - [x] Remove migrated hidden duplicates. Add layout smoke assertions and screenshot script. Avoid new raw inline color duplication.
   - [x] Render all sizes, inspect clipping, contrast, main/side proportions and actual unknown/error/live states. Correct UI only.
5. **Final verification and review delivery**.
   - [x] Targeted tests + compileall + diff check. Full isolated regression using CI split-case list and verdict files (timeouts never accepted as PASS).
   - [x] Broad review; fix concrete findings; regenerate actual five-page screenshots from final source (offline, no fabricated hardware readiness).
   - [ ] Commit relevant source/tests/docs, leave unrelated Gateway recovery/Keil plans out. Report branch/commit/files/architecture/tests/screenshots/known differences/remaining issues.
   - [ ] Stop at IMPLEMENTATION READY FOR UI REVIEW.
