# Canonical GUI and Monitor Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the v0.18 five-page GUI the only production widget tree and give MONITOR an independent, tested zero-halt lifecycle so production no longer constructs the v0.15-v0.17 Debug IDE.

**Architecture:** Extract the proven Live Monitor orchestration from `DebugTab` into a focused controller that still uses the existing core sessions, panel, workers, and HardwareSession manager. Add an explicit v0.18 construction profile to the current window shell, migrate v0.18 to that profile, then remove historical GUI modules only after import and regression evidence proves they have no production consumer.

**Tech Stack:** Python 3.11, PySide6, `unittest`, existing `B300Service`, `DebugService`, `LiveMonitorSession`, `FunctionWorker`.

**Spec:** `docs/superpowers/specs/2026-09-05-release-hardening-consolidation-design.md`

## Global Constraints

- Production GUI remains exactly `PROGRAM / MONITOR / DEBUG / DEVICE / SETTINGS`.
- Production interactive debug remains VS Code + Cortex-Debug; do not restore an internal IDE.
- Reuse existing Live Monitor backend and preserve non-halting behavior, cadence, allow-lists, analytics, cancellation, cleanup, and HardwareSession semantics.
- Never weaken B300 flash, metadata, WRP, probe-selection, network-binding, or target run-state safety.
- No production page may construct a hidden legacy Debug/Gateway/Memory/Operator widget solely for compatibility.
- Historical removal happens only after direct consumer and regression evidence.
- Keep current v0.18 public GUI startup arguments and test injection points compatible.

---

### Task 1: Protect the canonical production object graph

**Files:**
- Modify: `tests/test_v018_simplified_ui.py`
- Test: `tests/test_v018_simplified_ui.py`

**Interfaces:**
- Consumes: `MainWindowV18`, its five `*_view` properties, and injected fake services.
- Produces: regression requirements that `MainWindowV18` has no legacy `debug_tab`, `gateway_tab`, `memory_tab`, or `operator_view` child and that MONITOR owns a live controller directly.

- [ ] **Step 1: Add a failing production object-graph test**

```python
def test_production_window_does_not_construct_hidden_legacy_workbenches(self) -> None:
    window = self._make_window()
    try:
        self.assertFalse(hasattr(window, "debug_tab"))
        self.assertFalse(hasattr(window, "gateway_tab"))
        self.assertFalse(hasattr(window, "memory_tab"))
        self.assertFalse(hasattr(window, "operator_view"))
        self.assertIs(window.monitor_view.live_panel.parent(), window.monitor_view)
        self.assertIs(window.monitor_view.controller.panel, window.monitor_view.live_panel)
    finally:
        self._close(window)
```

- [ ] **Step 2: Run the test and verify the current hidden DebugTab assertion fails**

Run: `python scripts/run_unittest_module.py --split-cases --case-timeout 60 tests.test_v018_simplified_ui`

Expected: FAIL because v0.18 currently constructs `debug_tab`, `gateway_tab`, `memory_tab`, and `operator_view` through the base window.

- [ ] **Step 3: Replace the old coupling assertion**

Replace the assertion that `window.monitor_view.live_panel is window.debug_tab.live_panel` with assertions that MONITOR owns its panel/controller and page changes do not replace either object.

- [ ] **Step 4: Commit the red production contract**

```text
git add tests/test_v018_simplified_ui.py
git commit -m "test: define canonical v018 production widget graph"
```

### Task 2: Extract Live Monitor orchestration from legacy DebugTab

**Files:**
- Create: `b300_gui/live_monitor_controller.py`
- Create: `tests/test_live_monitor_controller.py`
- Modify: `b300_gui/views/monitor_view.py`
- Modify: `b300_gui/debug_tab.py`

**Interfaces:**
- Consumes: `DebugLivePanel`, `LiveMonitorSession`, `LocalLiveMonitorConfig`, `ClientLiveMonitorConfig`, `FunctionWorker`, selected probe callback, current symbol/role/remote profile state, and an OpenOCD executable.
- Produces: `LiveMonitorRequest`, `LiveMonitorController.start(request)`, `stop()`, `clear()`, `export(parent)`, `prepare_shutdown() -> bool`, `request_shutdown()`, `active`, `operation_state_changed`, and `log`.

- [ ] **Step 1: Write controller lifecycle tests with a fake session**

```python
class LiveMonitorControllerTests(unittest.TestCase):
    def test_local_start_streams_samples_closes_session_and_reenables_controls(self):
        panel = FakeLivePanel(watch_specs=("speed:f32",), interval=0.5, limit=2)
        session = FakeLiveSession(samples=(sample0, sample1))
        controller = LiveMonitorController(
            panel=panel,
            selected_probe=lambda: ProbeRef("ABC"),
            openocd_executable="openocd",
            session_factory=lambda **_kwargs: session,
            worker_factory=SynchronousWorker,
        )
        controller.start(LiveMonitorRequest.local(Path("firmware.axf")))
        self.assertEqual(panel.samples, [sample0, sample1])
        self.assertTrue(session.closed)
        self.assertFalse(controller.active)
        self.assertEqual(panel.control_state, (True, False, True))

    def test_cancel_closes_session_and_preserves_non_halting_transport(self):
        controller, session = self.make_running_controller()
        controller.stop()
        self.assertTrue(session.cancelled)
        self.assertNotIn("gdb", session.started_transport)
```

Also cover invalid/missing ELF, Gateway rejection, Client profile construction,
start failure, analytics-render failure isolation, worker creation failure,
shutdown timeout, and exactly one busy true/false signal pair.

- [ ] **Step 2: Run the new tests and verify import failure**

Run: `python -m unittest tests.test_live_monitor_controller -v`

Expected: FAIL because `b300_gui.live_monitor_controller` does not exist.

- [ ] **Step 3: Implement immutable request types**

```python
@dataclass(frozen=True)
class LiveMonitorRequest:
    role: str
    symbols: Optional[Path]
    host: str = ""
    user: str = ""
    ssh_port: int = 22
    symbol_roots: tuple[Path, ...] = ()

    @classmethod
    def local(cls, symbols: Path) -> "LiveMonitorRequest":
        return cls("LOCAL", Path(symbols))
```

Validation accepts only `LOCAL` or `CLIENT`; it requires an existing ELF/AXF
for LOCAL and requires host/user plus ELF or a bounded symbol root for CLIENT.

- [ ] **Step 4: Implement the controller by moving, not duplicating, the proven lifecycle**

```python
class LiveMonitorController(QObject):
    operation_state_changed = Signal(bool)
    log = Signal(str)

    def start(self, request: LiveMonitorRequest) -> None:
        watch_specs = self.panel.watch_specs()
        config = self._build_config(request, watch_specs)
        self.panel.reset_for_sampling()
        self._active = True
        live = self._session_factory(openocd_executable=self._openocd_executable)
        self._live_session = live

        def execute(log, phase, _cancel):
            try:
                info = live.start_local(config) if request.role == "LOCAL" else live.start_client(config)
                summary = live.run(phase)
                return summary, live.analytics_snapshot(), info
            finally:
                live.close()

        self._begin_worker(execute)
```

Use the existing `FunctionWorker`; always clear session/worker state and emit
`operation_state_changed(False)` on completed, failed, rejected, and shutdown
paths. Presentation-only analytics exceptions must not fail a valid session.

- [ ] **Step 5: Make MonitorView the permanent production owner**

```python
self.live_panel = live_panel or DebugLivePanel(self)
self.controller = controller or LiveMonitorController(
    panel=self.live_panel,
    selected_probe=selected_probe,
    openocd_executable=openocd_executable,
    parent=self,
)
self.live_panel.start_button.clicked.connect(self._start_requested)
self.live_panel.stop_button.clicked.connect(self.controller.stop)
```

Add a compact source/role row to `MonitorView`: `LOCAL / CLIENT`, selected
AXF/ELF, and one browse action. Keep host/user/SSH details collapsed and load
the saved remote profile for CLIENT. Do not expose TCL/GDB ports.

- [ ] **Step 6: Delegate the legacy DebugTab live methods to the same controller**

Keep `DebugTab` compatibility temporarily, but replace its independent live
session implementation with a controller constructed around its existing
panel/fields. This prevents two lifecycle implementations while historical
tests are migrated.

- [ ] **Step 7: Run focused monitor tests**

Run:

```text
python -m unittest tests.test_live_monitor_controller tests.test_live_monitor tests.test_live_session tests.test_live_service tests.test_live_analytics -v
python scripts/run_unittest_module.py --split-cases --case-timeout 60 tests.test_gui_smoke
python scripts/run_unittest_module.py --split-cases --case-timeout 60 tests.test_v018_simplified_ui
```

Expected: PASS with no target access during widget construction or navigation.

- [ ] **Step 8: Commit the extraction**

```text
git add b300_gui/live_monitor_controller.py b300_gui/views/monitor_view.py b300_gui/debug_tab.py tests/test_live_monitor_controller.py tests/test_v018_simplified_ui.py
git commit -m "refactor: give monitor an independent production lifecycle"
```

### Task 3: Add a canonical v0.18 shell construction profile

**Files:**
- Create: `b300_gui/production_shell.py`
- Create: `b300_gui/production_state.py`
- Create: `tests/test_production_shell.py`
- Modify: `b300_gui/main_window_v18.py`
- Modify: `b300_gui/main_window.py`
- Modify: `b300_gui/__main__.py`

**Interfaces:**
- Consumes: existing service/update/setup injection arguments and the five v0.18 views.
- Produces: `ProductionShell(QMainWindow)`, `ProductionDeviceState`, a five-page stack, shared header status, update/setup/help actions, and no legacy widget construction.

- [ ] **Step 1: Write failing shell tests**

```python
def test_shell_has_exactly_five_pages_and_no_hidden_qtabwidget(self):
    window = make_window()
    self.assertEqual(tuple(window.page_names), ("program", "monitor", "debug", "device", "settings"))
    self.assertEqual(window.findChildren(QTabWidget), [])

def test_probe_and_target_state_is_one_shared_snapshot(self):
    window = make_window(probe_loader=lambda: (probe,))
    window.apply_target_info(target)
    self.assertEqual(window.device_state.probe, probe)
    self.assertEqual(window.device_state.target, target)
    self.assertIs(window.program_view.device_state, window.device_state)
    self.assertIs(window.device_view.device_state, window.device_state)
```

- [ ] **Step 2: Verify the tests fail against the current inherited window**

Run: `python scripts/run_unittest_module.py tests.test_production_shell`

Expected: FAIL because the production shell/state does not exist and a hidden
legacy `QTabWidget` is constructed.

- [ ] **Step 3: Implement the shared device state**

```python
class ProductionDeviceState(QObject):
    changed = Signal()

    def set_probes(self, probes: Sequence[ProbeInfo]) -> None:
        self.probes = tuple(probes)
        self.changed.emit()

    def set_target(self, target: Optional[TargetInfo]) -> None:
        self.target = target
        self.changed.emit()
```

Views read the same snapshot and render different detail levels; they do not
run their own probe scans or target inspections.

- [ ] **Step 4: Implement only the reusable window chrome in ProductionShell**

Build the HeaderBar, five-button sidebar, one page title/status row, and one
`QStackedWidget`. Preserve QSettings, theme, first-run timer, update timer,
window geometry, About, What's New, support bundle, and close coordination.
Do not instantiate `MemoryTab`, `DebugTab`, `GatewaySetupTab`, `OperatorView`, or
the legacy flash tab.

- [ ] **Step 5: Change MainWindowV18 to inherit ProductionShell**

Move only v0.18-used provisioning/update/setup handlers out of
`main_window.py` into focused collaborators or `MainWindowV18`; keep their
calls to `B300Service` and existing workers unchanged. `main_window.py` remains
the compatibility owner until Task 5 removes historical consumers.

- [ ] **Step 6: Keep the executable entry point stable**

`b300_gui.__main__` continues importing `MainWindowV18 as MainWindow` and keeps
`--smoke-test` and `--first-run-setup` behavior unchanged.

- [ ] **Step 7: Run the production GUI contract**

Run:

```text
python scripts/run_unittest_module.py tests.test_production_shell
python scripts/run_unittest_module.py --split-cases --case-timeout 60 tests.test_v018_simplified_ui
python scripts/run_unittest_module.py --split-cases --case-timeout 60 tests.test_gui_interlocks
python scripts/run_unittest_module.py --split-cases --case-timeout 60 tests.test_gui_smoke
python scripts/run_unittest_module.py tests.test_gui_updater
python -m b300_gui --smoke-test
```

Expected: PASS; smoke mode performs no USB, OpenOCD, or target request.

- [ ] **Step 8: Commit the canonical shell**

```text
git add b300_gui/production_shell.py b300_gui/production_state.py b300_gui/main_window_v18.py b300_gui/main_window.py b300_gui/__main__.py tests/test_production_shell.py tests/test_v018_simplified_ui.py tests/test_gui_interlocks.py tests/test_gui_smoke.py
git commit -m "refactor: make v018 the canonical production window"
```

### Task 4: Consolidate duplicate five-page actions and copy

**Files:**
- Modify: `b300_gui/widgets/header_bar.py`
- Modify: `b300_gui/views/program_view.py`
- Modify: `b300_gui/views/monitor_view.py`
- Modify: `b300_gui/views/debug_vscode_view.py`
- Modify: `b300_gui/views/device_view.py`
- Modify: `b300_gui/views/settings_view.py`
- Modify: `b300_gui/main_window_v18.py`
- Modify: `tests/test_v018_simplified_ui.py`
- Modify: `tests/test_gui_interlocks.py`

**Interfaces:**
- Consumes: shared `ProductionDeviceState`, setup/update/support actions, and VS Code bridge state.
- Produces: one global probe refresh/connection action, one setup entry, one update entry, concise default copy, and Details/Diagnostics disclosure.

- [ ] **Step 1: Add failing uniqueness and copy-budget tests**

```python
def test_primary_actions_are_not_duplicated(self):
    window = self._make_window()
    self.assertEqual(len(window.findChildren(QPushButton, "refreshProbeAction")), 1)
    self.assertEqual(len(window.findChildren(QPushButton, "machineSetupAction")), 1)
    self.assertEqual(len(window.findChildren(QPushButton, "checkUpdateAction")), 1)

def test_default_pages_hide_transport_internals(self):
    window = self._make_window()
    visible = " ".join(label.text() for label in window.findChildren(QLabel) if label.isVisible())
    for internal in ("127.0.0.1", "3333", "6666", "TCL", "GDB path", "OpenOCD path"):
        self.assertNotIn(internal, visible)
```

- [ ] **Step 2: Verify current duplicate/default-internal assertions fail**

Run: `python scripts/run_unittest_module.py --split-cases --case-timeout 60 tests.test_v018_simplified_ui`

- [ ] **Step 3: Implement the concise action hierarchy**

Use one global connection strip for probe, target, run-state, and busy owner.
PROGRAM keeps one `Chọn HEX` and one context-sensitive primary action. DEVICE
shows detail but routes refresh/inspect to the same global actions. SETTINGS
owns machine setup, update, support, appearance, and About. DEBUG uses a single
role selector and one primary Start/Open button plus Stop.

- [ ] **Step 4: Put technical content behind existing collapsible primitives**

Move endpoint/path/raw fields into collapsed `CollapsibleCard` instances named
`Details` or `Diagnostics`. Visibility changes must not trigger hardware or
network actions.

- [ ] **Step 5: Preserve interlocks and run focused tests**

Run:

```text
python scripts/run_unittest_module.py --split-cases --case-timeout 60 tests.test_v018_simplified_ui
python scripts/run_unittest_module.py --split-cases --case-timeout 60 tests.test_gui_interlocks
python scripts/run_unittest_module.py tests.test_v018_vscode_controller
python scripts/run_unittest_module.py tests.test_gui_updater
```

- [ ] **Step 6: Commit the UI consolidation**

```text
git add b300_gui/widgets/header_bar.py b300_gui/views b300_gui/main_window_v18.py tests/test_v018_simplified_ui.py tests/test_gui_interlocks.py
git commit -m "refactor: consolidate v018 operator actions"
```

### Task 5: Remove historical internal-IDE modules and compatibility mutation

**Files:**
- Delete after proof: `b300_gui/main_window_v15.py`
- Delete after proof: `b300_gui/debug_tab.py`
- Delete after proof: `b300_gui/debug_tab_compat.py`
- Delete after proof: `b300_gui/debug_tab_v15.py`
- Delete after proof: `b300_gui/debug_tab_v152.py`
- Delete after proof: `b300_gui/debug_tab_v160.py`
- Delete after proof: `b300_gui/debug_tab_v170.py`
- Delete after proof: `b300_gui/debug_ide_workbench.py`
- Delete after proof: `b300_gui/debug_workspace.py`
- Delete after proof: `b300_gui/debug_source_view.py`
- Delete after proof: `b300_gui/debug_variables_pane.py`
- Delete after proof: `b300_gui/debug_breakpoints_pane.py`
- Delete after proof: `b300_gui/debug_callstack_pane.py`
- Delete after proof: `b300_gui/debug_registers_pane.py`
- Delete after proof: `b300_gui/debug_intelligence_tabs.py`
- Delete after proof: `b300_gui/views/debug_studio_view.py`
- Modify: `b300_gui/__init__.py`
- Remove/replace: historical-only test modules identified in the audit
- Modify: `tests/test_v018_simplified_ui.py`
- Modify: `tests/test_gui_packaging.py`

**Interfaces:**
- Consumes: completed Tasks 2-4 and import/search evidence.
- Produces: no package-import monkey patch and no shipped internal IDE implementation.

- [ ] **Step 1: Prove there are no production imports**

Run:

```text
rg -n "main_window_v15|debug_tab(_compat|_v15|_v152|_v160|_v170)?|debug_ide_workbench|debug_workspace|debug_(source_view|variables_pane|breakpoints_pane|callstack_pane|registers_pane|intelligence_tabs)|debug_studio_view" b300_gui b300_gui_entry.py packaging build_native_bundle.py
```

Expected: only files scheduled for removal; no import from `MainWindowV18`,
`ProductionShell`, `MonitorView`, executable entries, or packaging hidden-import lists.

- [ ] **Step 2: Add a packaged-module exclusion test**

```python
def test_native_gui_does_not_collect_removed_internal_ide_modules(self):
    sources = packaging_source_text()
    for module in REMOVED_INTERNAL_IDE_MODULES:
        self.assertNotIn(module, sources)
```

- [ ] **Step 3: Remove historical tests before modules only when their contract is superseded**

Delete tests that assert v0.15/v0.17 presentation. Preserve backend tests for
GDB/MI, snapshot, SVD, FreeRTOS, fault analysis, live monitoring, and debug
lifecycle because those test reusable core capability rather than removed UI.

- [ ] **Step 4: Remove the module chain and package import mutation**

Make `b300_gui/__init__.py` export only `__version__`; it must not replace class
objects during import. Delete the proven-unreferenced modules.

- [ ] **Step 5: Run import, production, core-debug, and packaging regressions**

Run:

```text
python -c "import b300_gui; from b300_gui.main_window_v18 import MainWindowV18; print(b300_gui.__version__)"
python scripts/run_unittest_module.py --split-cases --case-timeout 60 tests.test_v018_simplified_ui
python scripts/run_unittest_module.py --split-cases --case-timeout 60 tests.test_gui_smoke
python -m unittest tests.test_debug_service tests.test_debug_session tests.test_gdb_mi tests.test_live_monitor tests.test_live_session -v
python -m unittest tests.test_gui_packaging tests.test_v018_managed_gdb_release -v
```

- [ ] **Step 6: Commit historical UI removal**

```text
git add -A b300_gui tests
git commit -m "refactor: remove retired internal debug IDE"
```

### Task 6: Verify the complete GUI consolidation slice

**Files:**
- Modify: `docs/superpowers/specs/2026-09-05-release-hardening-consolidation-audit.md`
- Modify: `docs/superpowers/plans/2026-09-05-canonical-gui-monitor.md`

**Interfaces:**
- Consumes: all preceding tasks.
- Produces: actual command evidence and final inventory state for this slice.

- [ ] **Step 1: Run static production ownership checks**

Run:

```text
rg -n "MainWindowV18 as MainWindow" b300_gui/__main__.py
rg -n "Studio Debug|Internal IDE|Interactive Debug|Debug Server|Gateway Server" b300_gui
```

Expected: one canonical entry; no removed production terminology in user-visible copy.

- [ ] **Step 2: Run the complete GUI/core regression set**

Run:

```text
python -m unittest tests.test_live_monitor_controller tests.test_live_monitor tests.test_live_session tests.test_live_service tests.test_live_analytics tests.test_v018_vscode_bridge tests.test_v018_vscode_controller tests.test_vscode_environment -v
python scripts/run_unittest_module.py --split-cases --case-timeout 60 tests.test_v018_simplified_ui
python scripts/run_unittest_module.py --split-cases --case-timeout 60 tests.test_gui_interlocks
python scripts/run_unittest_module.py --split-cases --case-timeout 60 tests.test_gui_smoke
python scripts/run_unittest_module.py tests.test_gui_updater
python -m b300_gui --smoke-test
```

- [ ] **Step 3: Run the full repository regression**

Run: `python -m unittest discover -s tests -q`

Expected: PASS. If monolithic Qt teardown is unstable, preserve its exact log
and also run every GUI module through `scripts/run_unittest_module.py`; do not
claim the monolithic command passed unless it actually exits zero.

- [ ] **Step 4: Update the audit with measured post-change evidence**

Replace inventory actions with completed states, list retained compatibility
modules and exact consumers, record module/line-count changes, and paste test
command/result summaries without claiming hardware acceptance.

- [ ] **Step 5: Commit verification evidence**

```text
git add docs/superpowers/specs/2026-09-05-release-hardening-consolidation-audit.md docs/superpowers/plans/2026-09-05-canonical-gui-monitor.md
git commit -m "docs: record canonical gui consolidation evidence"
```
