#!/usr/bin/env python3
"""Fail CI if the v0.23.2 functional-parity safety net is accidentally removed."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

BASELINE_COMMIT = "2eed7cec3aeba2e2eb67b76efd8d309dbb4066e5"

REQUIRED_RUNTIME_PATHS = (
    "b300_stlink.py",
    "b300_gui/__main__.py",
    "b300_gui/production_window.py",
    "b300_gui/main_window_v18.py",
    "b300_gui/debug_tab_compat.py",
    "b300_core/service.py",
    "b300_core/openocd.py",
    "b300_core/policy.py",
    "b300_core/factory_policy.py",
    "b300_core/factory_resource.py",
    "b300_core/hardware_session.py",
    "b300_core/debug_service.py",
    "b300_core/live_monitor.py",
    "b300_core/gateway_lease.py",
    "b300_core/gateway_lease_coordinator.py",
    "b300_core/gateway_supervisor.py",
    "b300_core/vscode_bridge.py",
    "b300_core/updater.py",
    "b300_core/release_manifest.py",
    "b300_core/machine_setup.py",
)

REQUIRED_TEST_MODULES = (
    "tests/test_b300_stlink.py",
    "tests/test_flash_service.py",
    "tests/test_core_hex_policy.py",
    "tests/test_core_openocd.py",
    "tests/test_cli_flash_debug_ux.py",
    "tests/test_factory_policy.py",
    "tests/test_factory_resource.py",
    "tests/test_factory_service.py",
    "tests/test_factory_openocd.py",
    "tests/test_cli_factory_probe_policy.py",
    "tests/test_core_probe_memory_metadata.py",
    "tests/test_device_state.py",
    "tests/test_hardware_session.py",
    "tests/test_gui_interlocks.py",
    "tests/test_debug_service.py",
    "tests/test_debug_session.py",
    "tests/test_gdb_mi.py",
    "tests/test_gateway_supervisor.py",
    "tests/test_gateway_client.py",
    "tests/test_gateway_lease.py",
    "tests/test_gateway_lease_coordinator.py",
    "tests/test_v018_vscode_bridge.py",
    "tests/test_v018_vscode_controller.py",
    "tests/test_live_monitor.py",
    "tests/test_live_session.py",
    "tests/test_live_monitor_controller.py",
    "tests/test_engineering_monitor.py",
    "tests/test_freertos_inspector.py",
    "tests/test_target_awareness.py",
    "tests/test_v018_simplified_ui.py",
    "tests/test_gui_smoke.py",
    "tests/test_production_window_compat.py",
    "tests/test_debug_tab.py",
    "tests/test_debug_connection_ux.py",
    "tests/test_updater.py",
    "tests/test_cli_update.py",
    "tests/test_cli_update_install.py",
    "tests/test_gui_updater.py",
    "tests/test_machine_setup.py",
    "tests/test_linux_usb_setup.py",
    "tests/test_offline_setup.py",
    "tests/test_gui_packaging.py",
    "tests/test_build_native_bundle.py",
    "tests/test_release_documentation.py",
)

REQUIRED_DOCUMENT = "docs/15_FUNCTIONAL_PARITY_V0.23.2_TO_V0.24.md"


def missing(paths):
    return [path for path in paths if not (ROOT / path).is_file()]


def main() -> int:
    problems = []

    runtime_missing = missing(REQUIRED_RUNTIME_PATHS)
    if runtime_missing:
        problems.append("required runtime paths missing: " + ", ".join(runtime_missing))

    tests_missing = missing(REQUIRED_TEST_MODULES)
    if tests_missing:
        problems.append("required regression modules missing: " + ", ".join(tests_missing))

    parity_doc = ROOT / REQUIRED_DOCUMENT
    if not parity_doc.is_file():
        problems.append(f"functional parity document missing: {REQUIRED_DOCUMENT}")
    elif BASELINE_COMMIT not in parity_doc.read_text(encoding="utf-8"):
        problems.append("functional parity document no longer identifies the v0.23.2 baseline commit")

    gui_entry = (ROOT / "b300_gui/__main__.py").read_text(encoding="utf-8")
    if "production_window" not in gui_entry or "ProductionMainWindow as MainWindow" not in gui_entry:
        problems.append(
            "production GUI entry changed from ProductionMainWindow; update the parity contract "
            "and replacement regression coverage deliberately before changing this guard"
        )

    compat_entry = (ROOT / "b300_gui/main_window_v18.py").read_text(encoding="utf-8")
    if "MainWindowV18 = ProductionMainWindow" not in compat_entry:
        problems.append(
            "historical MainWindowV18 compatibility alias was removed or changed without "
            "an explicit compatibility decision"
        )

    ci_text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    if "find tests -maxdepth 1 -name 'test_*.py'" not in ci_text:
        problems.append("Linux CI no longer discovers the complete tests/test_*.py suite")
    if "Get-ChildItem tests -Filter 'test_*.py'" not in ci_text:
        problems.append("Windows CI no longer discovers the complete tests/test_*.py suite")

    if problems:
        print("Functional parity contract FAILED:")
        for problem in problems:
            print(f" - {problem}")
        return 1

    print(
        "Functional parity contract PASS "
        f"(baseline {BASELINE_COMMIT[:7]}, "
        f"{len(REQUIRED_RUNTIME_PATHS)} runtime paths, "
        f"{len(REQUIRED_TEST_MODULES)} regression modules)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
