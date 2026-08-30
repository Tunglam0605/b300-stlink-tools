"""Idempotent host setup for a B300 SSH Debug Gateway.

This module manages only the operating-system SSH prerequisite. OpenOCD debug
ports remain loopback-only and are never added to a host firewall rule here.
"""

from __future__ import annotations

import getpass
import os
import platform
import shutil
import socket
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence, Tuple

DEBUG_PORTS = (3333, 4444, 6666)
DEFAULT_SSH_PORT = 22

@dataclass(frozen=True)
class GatewayHostCheck:
    name: str
    status: str
    code: str
    message: str
    next_action: str = "No action is required."

@dataclass(frozen=True)
class GatewayHostReport:
    platform: str
    checks: Tuple[GatewayHostCheck, ...]
    ssh_installed: bool
    ssh_service_running: bool
    ssh_startup_enabled: bool
    ssh_firewall_ready: bool
    ssh_port_listening: bool
    debug_ports_private: bool
    ready: bool
    conclusion: str
    ssh_port: int
    username: str
    hostname: str
    ipv4_addresses: Tuple[str, ...]

@dataclass(frozen=True)
class GatewayPreparePlan:
    platform: str
    actions: Tuple[str, ...]
    changes_required: bool
    requires_elevation: bool
    ssh_port: int

@dataclass(frozen=True)
class GatewayPrepareResult:
    plan: GatewayPreparePlan
    before: GatewayHostReport
    after: GatewayHostReport
    changed: bool
    succeeded: bool

CommandRunner = Callable[[Sequence[str], float], subprocess.CompletedProcess]

def _run(argv: Sequence[str], timeout: float = 20.0) -> subprocess.CompletedProcess:
    return subprocess.run(tuple(str(item) for item in argv), capture_output=True, text=True, timeout=timeout, check=False, creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW") else 0))

def _nonloopback_ipv4() -> Tuple[str, ...]:
    found = set()
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(item[4][0])
    except OSError:
        pass
    return tuple(sorted(value for value in found if value and not value.startswith("127.") and value != "0.0.0.0"))

def _tcp_connectable(port: int, host: str = "127.0.0.1") -> bool:
    try:
        connection = socket.create_connection((host, int(port)), timeout=0.35)
    except OSError:
        return False
    connection.close()
    return True

def _powershell() -> str:
    return shutil.which("powershell.exe") or shutil.which("powershell") or "powershell.exe"

def _windows_value(script: str, runner: CommandRunner) -> str:
    completed = runner((_powershell(), "-NoProfile", "-NonInteractive", "-Command", script), 30.0)
    return (completed.stdout or "").strip()

def _windows_debug_ports_private(runner: CommandRunner) -> bool:
    ports = ",".join(str(port) for port in DEBUG_PORTS)
    script = "$items=Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { @(%s) -contains $_.LocalPort }; $bad=$items | Where-Object { $_.LocalAddress -notin @('127.0.0.1','::1') }; if($bad){'EXPOSED'}else{'PRIVATE'}" % ports
    return _windows_value(script, runner) == "PRIVATE"

def _inspect_windows(ssh_port: int, runner: CommandRunner) -> GatewayHostReport:
    capability = _windows_value("$c=Get-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0' -ErrorAction SilentlyContinue; if($c){$c.State}else{'NotPresent'}", runner)
    service = _windows_value("$s=Get-Service -Name sshd -ErrorAction SilentlyContinue; if($s){$s.Status}else{'Missing'}", runner)
    installed = (capability.lower() == "installed" or service.lower() != "missing" or
                 Path(os.environ.get("WINDIR", r"C:\Windows"), "System32", "OpenSSH", "sshd.exe").is_file())
    running = service.lower() == "running"
    start_mode = _windows_value("$s=Get-CimInstance Win32_Service -Filter \"Name='sshd'\" -ErrorAction SilentlyContinue; if($s){$s.StartMode}else{'Missing'}", runner)
    startup = start_mode.lower() in {"auto", "automatic"}
    firewall = _windows_value("$r=Get-NetFirewallRule -ErrorAction SilentlyContinue | Where-Object { $_.Enabled -eq 'True' -and $_.Direction -eq 'Inbound' -and $_.Name -in @('OpenSSH-Server-In-TCP','B300-OpenSSH-Server-In-TCP') }; if($r){'READY'}else{'MISSING'}", runner) == "READY"
    return _build_report("windows", installed, running, startup, firewall, _tcp_connectable(ssh_port), _windows_debug_ports_private(runner), ssh_port, custom_port=(int(ssh_port) != DEFAULT_SSH_PORT))

def _linux_debug_ports_private(runner: CommandRunner) -> bool:
    completed = runner((shutil.which("ss") or "ss", "-ltnH"), 10.0)
    if completed.returncode != 0:
        return False
    for line in (completed.stdout or "").splitlines():
        fields = line.split()
        if len(fields) < 4:
            continue
        endpoint = fields[3]
        for port in DEBUG_PORTS:
            suffix = ":%d" % port
            if endpoint.endswith(suffix):
                host = endpoint[:-len(suffix)].strip("[]")
                if host not in {"127.0.0.1", "::1"}:
                    return False
    return True

def _linux_success(argv: Sequence[str], runner: CommandRunner) -> bool:
    return runner(argv, 20.0).returncode == 0

def _inspect_linux(ssh_port: int, runner: CommandRunner) -> GatewayHostReport:
    package = runner(("dpkg-query", "-W", "-f=${Status}", "openssh-server"), 20.0)
    installed = package.returncode == 0 and "install ok installed" in (package.stdout or "").lower()
    running = _linux_success(("systemctl", "is-active", "--quiet", "ssh"), runner)
    startup = _linux_success(("systemctl", "is-enabled", "--quiet", "ssh"), runner)
    ufw = shutil.which("ufw")
    firewall = True
    if ufw:
        status = runner((ufw, "status"), 20.0)
        text = status.stdout or ""
        if "Status: active" in text:
            firewall = any(
                (line.strip().startswith(str(ssh_port)) or ("%d/tcp" % ssh_port) in line) and
                "ALLOW" in line.upper()
                for line in text.splitlines()
            )
    return _build_report("linux", installed, running, startup, firewall, _tcp_connectable(ssh_port), _linux_debug_ports_private(runner), ssh_port, custom_port=(int(ssh_port) != DEFAULT_SSH_PORT))

def _build_report(system: str, installed: bool, running: bool, startup: bool, firewall: bool, listening: bool, private: bool, ssh_port: int, *, custom_port: bool = False) -> GatewayHostReport:
    checks = [
        GatewayHostCheck("ssh_install", "PASS" if installed else "FAIL", "SSH_SERVER_INSTALLED" if installed else "SSH_SERVER_MISSING", "OpenSSH Server is installed." if installed else "OpenSSH Server is not installed.", "No action is required." if installed else "Run Gateway Prepare to install OpenSSH Server."),
        GatewayHostCheck("ssh_service", "PASS" if running else "FAIL", "SSH_SERVICE_RUNNING" if running else "SSH_SERVICE_STOPPED", "SSH service is running." if running else "SSH service is not running.", "No action is required." if running else "Run Gateway Prepare to start the SSH service."),
        GatewayHostCheck("ssh_startup", "PASS" if startup else "FAIL", "SSH_STARTUP_ENABLED" if startup else "SSH_STARTUP_DISABLED", "SSH service starts automatically." if startup else "SSH service is not enabled for automatic startup.", "No action is required." if startup else "Run Gateway Prepare to enable SSH at boot."),
        GatewayHostCheck("ssh_firewall", "PASS" if firewall else "FAIL", "SSH_FIREWALL_READY" if firewall else "SSH_FIREWALL_BLOCKED", "Host firewall permits SSH TCP/%d." % ssh_port if firewall else "Host firewall does not have an enabled SSH TCP/%d allow rule." % ssh_port, "No action is required." if firewall else "Run Gateway Prepare to allow only the SSH port."),
        GatewayHostCheck("ssh_listener", "PASS" if listening else "FAIL", "SSH_PORT_LISTENING" if listening else "SSH_PORT_NOT_LISTENING", "Local SSH server accepts TCP/%d connections." % ssh_port if listening else "Local SSH server is not reachable on TCP/%d." % ssh_port, "No action is required." if listening else "Verify sshd is running and listening on the configured port."),
        GatewayHostCheck("debug_ports", "PASS" if private else "FAIL", "DEBUG_PORTS_PRIVATE" if private else "DEBUG_PORTS_EXPOSED", "GDB/Telnet/TCL debug ports are not exposed on non-loopback listeners." if private else "One or more debug ports (3333/4444/6666) are listening on a non-loopback address.", "No action is required." if private else "Stop/reconfigure the conflicting service. B300 Gateway requires debug ports to remain loopback-only."),
    ]
    if custom_port:
        checks.append(GatewayHostCheck("custom_ssh_port", "LIMITED", "CUSTOM_SSH_PORT_NOT_MANAGED", "Gateway Setup will not modify sshd_config for custom SSH ports.", "Keep the existing SSH configuration or use TCP/22 for managed setup."))
    failed = any(check.status == "FAIL" for check in checks)
    limited = any(check.status == "LIMITED" for check in checks)
    return GatewayHostReport(system, tuple(checks), installed, running, startup, firewall, listening, private, not failed, "BLOCKED" if failed else ("READY_WITH_WARNINGS" if limited else "READY"), int(ssh_port), getpass.getuser(), socket.gethostname(), _nonloopback_ipv4())

def inspect_gateway_host(ssh_port: int = DEFAULT_SSH_PORT, *, runner: CommandRunner = _run, system_name: Optional[str] = None) -> GatewayHostReport:
    if not 1 <= int(ssh_port) <= 65535:
        raise ValueError("SSH port must be in range 1..65535.")
    system = (system_name or platform.system()).strip().lower()
    if system == "windows":
        return _inspect_windows(int(ssh_port), runner)
    if system in {"linux", "ubuntu"}:
        return _inspect_linux(int(ssh_port), runner)
    raise RuntimeError("Gateway Setup supports Windows and Ubuntu/Linux only.")

def build_gateway_prepare_plan(report: GatewayHostReport) -> GatewayPreparePlan:
    actions = []
    if report.ssh_port != DEFAULT_SSH_PORT and not report.ready:
        raise ValueError("Managed Gateway Prepare supports TCP/22 only and never rewrites sshd_config. Use gateway doctor for an existing custom-port SSH server.")
    if not report.ssh_installed:
        actions.append("install_openssh_server")
    if not report.ssh_startup_enabled:
        actions.append("enable_ssh_startup")
    if not report.ssh_service_running:
        actions.append("start_ssh_service")
    if not report.ssh_firewall_ready:
        actions.append("allow_ssh_firewall")
    if not report.debug_ports_private:
        actions.append("manual_fix_debug_exposure")
    change_actions = tuple(action for action in actions if not action.startswith("manual_"))
    return GatewayPreparePlan(report.platform, tuple(actions), bool(change_actions), bool(change_actions), report.ssh_port)

def _windows_prepare_script(plan: GatewayPreparePlan) -> str:
    lines = ["$ErrorActionPreference='Stop'"]
    actions = set(plan.actions)
    if "install_openssh_server" in actions:
        lines += ["$cap=Get-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0'", "if($cap.State -ne 'Installed'){Add-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0' | Out-Null}"]
    if "enable_ssh_startup" in actions:
        lines.append("Set-Service -Name sshd -StartupType Automatic")
    if "start_ssh_service" in actions:
        lines.append("Start-Service -Name sshd")
    if "allow_ssh_firewall" in actions:
        lines += ["$fw=Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP','B300-OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue | Where-Object {$_.Enabled -eq 'True'}", "if(-not $fw){New-NetFirewallRule -Name 'B300-OpenSSH-Server-In-TCP' -DisplayName 'B300 OpenSSH Server (sshd)' -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 | Out-Null}"]
    lines.append("exit 0")
    return "\n".join(lines) + "\n"

def _run_windows_elevated(plan: GatewayPreparePlan, runner: CommandRunner) -> None:
    script = _windows_prepare_script(plan)
    if any(("LocalPort %d" % port) in script or ("LocalPort=%d" % port) in script for port in DEBUG_PORTS):
        raise RuntimeError("Safety invariant violated: debug ports must not be opened by Gateway Prepare.")
    handle = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8-sig", suffix=".ps1", delete=False, newline="\n")
    try:
        with handle:
            handle.write(script)
        path = str(Path(handle.name).resolve())
        ps = _powershell()
        command = (ps, "-NoProfile", "-Command", "$p=Start-Process -FilePath '%s' -Verb RunAs -Wait -PassThru -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File','%s'); exit $p.ExitCode" % (ps.replace("'", "''"), path.replace("'", "''")))
        result = runner(command, 900.0)
        if result.returncode != 0:
            raise RuntimeError("Elevated Windows OpenSSH setup failed with exit code %d." % result.returncode)
    finally:
        try:
            Path(handle.name).unlink()
        except OSError:
            pass

def _linux_privileged_prefix() -> Tuple[str, ...]:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return ()
    pkexec = shutil.which("pkexec")
    if pkexec:
        return (pkexec,)
    raise RuntimeError(
        "Administrator privileges are required. GUI setup needs pkexec/policykit; "
        "otherwise run B300 Tools from an already elevated/root session."
    )

def _run_linux_prepare(plan: GatewayPreparePlan, runner: CommandRunner) -> None:
    prefix = _linux_privileged_prefix()
    actions = set(plan.actions)
    commands = []
    if "install_openssh_server" in actions:
        commands += [prefix + ("apt-get", "update"), prefix + ("env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y", "openssh-server")]
    if "enable_ssh_startup" in actions or "start_ssh_service" in actions:
        commands.append(prefix + ("systemctl", "enable", "--now", "ssh"))
    if "allow_ssh_firewall" in actions:
        commands.append(prefix + ("ufw", "allow", "%d/tcp" % plan.ssh_port))
    for command in commands:
        result = runner(command, 900.0)
        if result.returncode != 0:
            raise RuntimeError("Gateway Prepare command failed (%d): %s\n%s" % (result.returncode, " ".join(command), (result.stderr or "").strip()))

def prepare_gateway_host(ssh_port: int = DEFAULT_SSH_PORT, *, runner: CommandRunner = _run, system_name: Optional[str] = None, inspector: Callable[..., GatewayHostReport] = inspect_gateway_host) -> GatewayPrepareResult:
    before = inspector(ssh_port=ssh_port, runner=runner, system_name=system_name)
    plan = build_gateway_prepare_plan(before)
    if "manual_fix_debug_exposure" in plan.actions:
        return GatewayPrepareResult(plan, before, before, False, False)
    if plan.changes_required:
        if before.platform == "windows":
            _run_windows_elevated(plan, runner)
        elif before.platform == "linux":
            _run_linux_prepare(plan, runner)
        else:
            raise RuntimeError("Unsupported Gateway Setup platform: %s" % before.platform)
    after = inspector(ssh_port=ssh_port, runner=runner, system_name=system_name)
    return GatewayPrepareResult(plan, before, after, plan.changes_required, after.ready)

def client_connection_text(report: GatewayHostReport) -> str:
    host = report.ipv4_addresses[0] if report.ipv4_addresses else report.hostname
    return ("Gateway host: %s\nSSH user: %s\nSSH port: %d\nGUI Client: host=%s user=%s port=%d\nSecurity: OpenOCD 3333/4444/6666 stay loopback-only; only SSH is exposed to LAN." % (host, report.username, report.ssh_port, host, report.username, report.ssh_port))
