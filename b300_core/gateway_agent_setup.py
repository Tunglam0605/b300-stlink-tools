"""Idempotent per-user Gateway Agent autostart setup."""

from __future__ import annotations

import os
import platform
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence, Tuple


TASK_NAME = "B300-STLink-GatewayAgent"
UNIT_NAME = "b300-stlink-gateway-agent.service"


@dataclass(frozen=True)
class GatewayAgentSetupReport:
    platform: str
    supported: bool
    installed: bool
    running: bool
    version: Optional[str]
    autostart_enabled: bool
    reason_code: str


@dataclass(frozen=True)
class GatewayAgentSetupPlan:
    platform: str
    commands: Tuple[Tuple[str, ...], ...]
    changes_required: bool
    requires_elevation: bool


@dataclass(frozen=True)
class GatewayAgentSetupResult:
    before: GatewayAgentSetupReport
    plan: GatewayAgentSetupPlan
    after: GatewayAgentSetupReport
    changed: bool


def _system(system_name: Optional[str]) -> str:
    value = (system_name or platform.system()).strip().lower()
    return "windows" if value.startswith("win") else "linux" if value in {"linux", "ubuntu"} else value


def _run(command: Sequence[str]):
    return subprocess.run(tuple(str(item) for item in command), capture_output=True,
                          text=True, check=False, shell=False)


def _result_field(result, key: str, default=""):
    if isinstance(result, dict):
        return result.get(key, default)
    return getattr(result, key, default)


def inspect_gateway_agent_setup(*, cli_path: Path, system_name: Optional[str] = None,
                                runner: Callable = _run) -> GatewayAgentSetupReport:
    system = _system(system_name)
    cli = Path(cli_path).expanduser()
    if system == "windows":
        query = runner(("schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST", "/V"))
        output = str(_result_field(query, "stdout", ""))
        installed = cli.is_file() or "B300-STLink-GatewayAgent" in output
        enabled = installed and "B300-STLink-GatewayAgent" in output
        running = "Running" in output
        version = _result_field(query, "version", None) or _extract_version(output)
        return GatewayAgentSetupReport(system, True, installed, running, version, enabled,
                                        "AGENT_READY" if enabled else "AGENT_MISSING")
    if system == "linux":
        enabled_result = runner(("systemctl", "--user", "is-enabled", UNIT_NAME))
        active_result = runner(("systemctl", "--user", "is-active", UNIT_NAME))
        installed = cli.is_file() or _result_field(enabled_result, "returncode", 1) == 0
        enabled = _result_field(enabled_result, "returncode", 1) == 0
        running = _result_field(active_result, "returncode", 1) == 0
        return GatewayAgentSetupReport(system, True, installed, running, None, enabled,
                                       "AGENT_READY" if enabled else "AGENT_MISSING")
    return GatewayAgentSetupReport(system, False, False, False, None, False, "UNSUPPORTED_PLATFORM")


def _extract_version(output: str) -> Optional[str]:
    for line in output.splitlines():
        if "version" in line.lower() and ":" in line:
            return line.split(":", 1)[1].strip() or None
    return None


def build_gateway_agent_setup_plan(report: GatewayAgentSetupReport, *, cli_path: Path,
                                   system_name: Optional[str] = None) -> GatewayAgentSetupPlan:
    system = _system(system_name or report.platform)
    if not report.supported:
        return GatewayAgentSetupPlan(system, (), False, False)
    cli = str(Path(cli_path).expanduser().resolve())
    if system == "windows":
        command = (
            "schtasks", "/Create", "/TN", TASK_NAME, "/SC", "ONLOGON",
            "/TR", '"%s" debug gateway-agent --managed-child --json' % cli,
            "/F",
        )
        return GatewayAgentSetupPlan(system, (command,), not report.autostart_enabled, False)
    # The packaged CLI installs this unit template under the user's systemd
    # unit directory. Setup only reloads and enables that exact unit; it never
    # enables linger or escalates privileges.
    commands = (
        ("systemctl", "--user", "daemon-reload"),
        ("systemctl", "--user", "enable", "--now", UNIT_NAME),
    )
    return GatewayAgentSetupPlan(system, commands, not report.autostart_enabled, False)


def prepare_gateway_agent_setup(*, cli_path: Path, system_name: Optional[str] = None,
                                runner: Callable = _run) -> GatewayAgentSetupResult:
    before = inspect_gateway_agent_setup(cli_path=cli_path, system_name=system_name, runner=runner)
    plan = build_gateway_agent_setup_plan(before, cli_path=cli_path, system_name=system_name)
    changed = False
    for command in plan.commands:
        result = runner(command)
        if _result_field(result, "returncode", 0) != 0:
            raise RuntimeError("Gateway Agent setup command failed: %s" % " ".join(command))
        changed = True
    after = inspect_gateway_agent_setup(cli_path=cli_path, system_name=system_name, runner=runner)
    return GatewayAgentSetupResult(before, plan, after, changed)


__all__ = [
    "GatewayAgentSetupPlan", "GatewayAgentSetupReport", "GatewayAgentSetupResult",
    "build_gateway_agent_setup_plan", "inspect_gateway_agent_setup",
    "prepare_gateway_agent_setup",
]
