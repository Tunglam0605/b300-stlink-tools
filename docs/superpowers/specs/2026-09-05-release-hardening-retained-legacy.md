# Retained legacy classification after consolidation

This supplements the baseline audit; its original dependency inventory describes
v0.18.0 before consolidation and is historical evidence.

| Module group | Classification | Reason retained |
|---|---|---|
| `main_window.py` | COMPAT / shared implementation | V18 reuses provisioning, Factory, updater and diagnostics handlers. `legacy_workbenches=False` prevents old workbench construction. |
| `debug_tab.py`, `debug_tab_compat.py` | COMPAT | Historical package imports and tests retain the old API. Imports may occur; production does not instantiate a hidden DebugTab. |
| `main_window_v15.py`, `debug_tab_v15.py`, `debug_tab_v152.py`, `debug_tab_v160.py`, `debug_tab_v170.py` | DEPRECATED / TEST-ONLY consumers | Historical regressions and compatibility references. No production window chooses these. |
| `debug_ide_workbench.py`, source/variables/breakpoints/callstack/register panes, intelligence tabs, legacy workspace | DEPRECATED / TEST-ONLY consumers | Retained to avoid deleting tested historical behavior; interactive production debugging belongs to VS Code. |
| `debug_live_panel.py`, `live_monitor_controller.py`, `views/monitor_view.py` | CANONICAL | Reusable display plus independent non-halting Monitor lifecycle. |
| `views/debug_vscode_view.py`, `vscode_debug_controller.py`, `core/vscode_bridge.py` | CANONICAL | Production LOCAL / GATEWAY / CLIENT bridge. |
| CLI `debug server` | COMPAT / DEPRECATED | Alias warns and preserves Gateway semantics. |

`b300_stlink.py` still contains shared command handlers. Further extraction is
deferred because release correctness does not require another dispatch refactor;
public role semantics and shared safety services remain authoritative.

No removal in this cycle changes the S0–S2 guard, S3 metadata, S4–S7 application
contract or Factory confirmation. HW-P1-001 remains DEFERRED.
