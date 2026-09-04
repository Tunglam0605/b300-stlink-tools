"""High-level VS Code debug orchestration for B300.

B300 owns the ST-Link/OpenOCD/SSH infrastructure and safety boundaries. VS Code
plus Cortex-Debug owns the interactive debugger UX and GDB data plane.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, Optional

from .debug_service import DebugConfig, DebugService, DebugState
from .gdb_runtime import resolve_gdb
from .models import ProbeRef
from .process_startup import child_process_kwargs
from .remote_debug_guard import RemoteDebugGuard
from .remote_session import RemoteForward, RemoteSession
from .remote_vscode import workspace_executable
from .tcl_client import SafeTclClient, TclEndpoint


class DebugRole(str, Enum):
    LOCAL = "LOCAL"
    GATEWAY = "GATEWAY"
    CLIENT = "CLIENT"


class BridgeState(str, Enum):
    STOPPED = "STOPPED"
    READY = "READY"
    FAILED = "FAILED"


@dataclass(frozen=True)
class VsCodeBridgeState:
    role: Optional[DebugRole]
    state: BridgeState
    gdb_target: Optional[str]
    openocd_state: Optional[str] = None
    tunnel_name: Optional[str] = None
    initial_target_state: Optional[str] = None
    detail: str = ""


@dataclass(frozen=True)
class VsCodeExternalProfile:
    """Cortex-Debug profile for a B300-managed external GDB server."""

    name: str
    executable: str
    gdb_target: str
    gdb_path: str = "arm-none-eabi-gdb"
    device: str = "STM32F407ZE"
    rtos: Optional[str] = "FreeRTOS"

    def validate(self) -> None:
        if not self.name.strip():
            raise ValueError("VS Code debug profile name must not be empty.")
        if not self.executable.strip() or "\x00" in self.executable:
            raise ValueError("VS Code executable/symbol path must not be empty.")
        if not re.fullmatch(r"127\.0\.0\.1:[0-9]{1,5}", self.gdb_target):
            raise ValueError("B300 VS Code GDB target must use local loopback.")
        port = int(self.gdb_target.rsplit(":", 1)[1])
        if not 1 <= port <= 65535:
            raise ValueError("VS Code GDB target port must be in range 1..65535.")
        if not self.gdb_path.strip() or "\x00" in self.gdb_path:
            raise ValueError("VS Code GDB path must not be empty.")

    def configuration(self) -> Dict[str, object]:
        self.validate()
        config: Dict[str, object] = {
            "name": self.name,
            "type": "cortex-debug",
            "request": "attach",
            "cwd": "${workspaceFolder}",
            "executable": self.executable,
            "servertype": "external",
            "gdbTarget": self.gdb_target,
            "gdbPath": self.gdb_path,
            "toolchainPrefix": "arm-none-eabi",
            "device": self.device,
            "gdbInterruptMode": "exec-interrupt",
            # Interactive debugging may halt the MCU; force hardware breakpoints
            # so Cortex-Debug never tries to patch flash with software breakpoints.
            "hardwareBreakpoints": {"require": True, "limit": 6},
            "hardwareWatchpoints": {"require": True, "limit": 4},
        }
        if self.rtos:
            config["rtos"] = self.rtos
        return config

    def launch_json(self) -> Dict[str, object]:
        return {"version": "0.2.0", "configurations": [self.configuration()]}

    def write_launch_json(self, workspace: Path, *, force: bool = False) -> Path:
        root = Path(workspace).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("VS Code workspace directory does not exist.")
        output = root / ".vscode" / "launch.json"
        if output.exists() and not force:
            raise FileExistsError(
                "Refusing to overwrite existing VS Code launch.json: %s" % output
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(self.launch_json(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return output


ProcessFactory = Callable[..., object]
TclFactory = Callable[[TclEndpoint], SafeTclClient]
GuardFactory = Callable[..., RemoteDebugGuard]


def _windows_code_exe_from_launcher(path: Path) -> Optional[Path]:
    """Map VS Code's PATH-facing code.cmd to the shell-free Code.exe binary."""
    selected = Path(path)
    if selected.suffix.lower() not in {".cmd", ".bat"}:
        return selected if selected.is_file() else None
    candidate = selected.parent.parent / "Code.exe"
    return candidate if candidate.is_file() else None


def _usable_vscode_launcher(value: str) -> Optional[str]:
    selected = Path(value).expanduser()
    if selected.is_file():
        if os.name == "nt":
            executable = _windows_code_exe_from_launcher(selected)
            return str(executable.resolve()) if executable is not None else None
        return str(selected.resolve())
    found = shutil.which(value)
    if not found:
        return None
    found_path = Path(found)
    if os.name == "nt":
        executable = _windows_code_exe_from_launcher(found_path)
        return str(executable.resolve()) if executable is not None else None
    return str(found_path.resolve())


def resolve_vscode(explicit: Optional[str] = None) -> str:
    """Resolve the VS Code launcher without invoking a command shell."""
    if explicit:
        launcher = _usable_vscode_launcher(explicit)
        if launcher:
            return launcher
        raise FileNotFoundError("VS Code launcher was not found: %s" % explicit)

    configured = os.environ.get("B300_VSCODE")
    if configured:
        launcher = _usable_vscode_launcher(configured)
        if launcher:
            return launcher

    if os.name == "nt":
        candidates = []
        local_app_data = os.environ.get("LOCALAPPDATA")
        program_files = os.environ.get("ProgramFiles")
        if local_app_data:
            candidates.append(Path(local_app_data) / "Programs" / "Microsoft VS Code" / "Code.exe")
        if program_files:
            candidates.append(Path(program_files) / "Microsoft VS Code" / "Code.exe")
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate.resolve())
        # PATH commonly exposes code.cmd. Convert it back to Code.exe so
        # subprocess can remain shell=False and no console shell is spawned.
        for command in ("code", "code.cmd", "Code.exe"):
            launcher = _usable_vscode_launcher(command)
            if launcher:
                return launcher
    else:
        launcher = _usable_vscode_launcher("code")
        if launcher:
            return launcher

    raise FileNotFoundError(
        "VS Code launcher was not found. Install VS Code or set B300_VSCODE."
    )


def launch_vscode(workspace: Path, *, executable: Optional[str] = None,
                  process_factory: ProcessFactory = subprocess.Popen,
                  platform_name: Optional[str] = None):
    """Open a workspace only after an explicit caller action."""
    root = Path(workspace).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("VS Code workspace directory does not exist.")
    launcher = resolve_vscode(executable)
    return process_factory(
        (launcher, "--reuse-window", str(root)),
        shell=False,
        **child_process_kwargs(platform_name),
    )


class VsCodeDebugBridge:
    """One high-level control plane for LOCAL/GATEWAY/CLIENT VS Code debug.

    LOCAL/GATEWAY start OpenOCD through DebugService, which owns the long-lived
    DEBUGGING HardwareSession lease. A loopback-only TCL endpoint is retained
    internally for RemoteDebugGuard; TCL is never forwarded to a Client. CLIENT
    only opens a loopback SSH GDB forward to an already-running Gateway OpenOCD
    instance. GDB itself always belongs to Cortex-Debug on the VS Code machine.
    """

    CLIENT_FORWARD_NAME = "vscode_gdb"

    def __init__(self, debug_service: Optional[DebugService] = None,
                 *, tcl_factory: TclFactory = SafeTclClient,
                 guard_factory: GuardFactory = RemoteDebugGuard) -> None:
        self.debug_service = debug_service or DebugService()
        self._tcl_factory = tcl_factory
        self._guard_factory = guard_factory
        self._role: Optional[DebugRole] = None
        self._remote_session: Optional[RemoteSession] = None
        self._remote_forward: Optional[RemoteForward] = None
        self._server_config: Optional[DebugConfig] = None
        self._guard: Optional[RemoteDebugGuard] = None
        self._last_detail = ""

    @property
    def state(self) -> VsCodeBridgeState:
        if self._role in (DebugRole.LOCAL, DebugRole.GATEWAY):
            openocd = self.debug_service.poll()
            if openocd in (DebugState.READY, DebugState.CONNECTED):
                bridge_state = BridgeState.READY
            elif openocd == DebugState.FAILED:
                bridge_state = BridgeState.FAILED
            else:
                bridge_state = BridgeState.STOPPED
            target = None
            if self._server_config is not None and bridge_state == BridgeState.READY:
                target = "127.0.0.1:%d" % self._server_config.gdb_port
            initial = (
                getattr(self._guard, "initial_target_state", None)
                if self._guard is not None else None
            )
            return VsCodeBridgeState(
                role=self._role,
                state=bridge_state,
                gdb_target=target,
                openocd_state=openocd.value,
                initial_target_state=initial,
                detail=self._last_detail,
            )

        if self._role == DebugRole.CLIENT:
            session = self._remote_session
            forward = self._remote_forward
            if session is None or not session.connected or forward is None:
                return VsCodeBridgeState(
                    role=self._role, state=BridgeState.FAILED, gdb_target=None,
                    tunnel_name=self.CLIENT_FORWARD_NAME,
                    detail="Remote SSH/GDB forward is not active.",
                )
            return VsCodeBridgeState(
                role=self._role,
                state=BridgeState.READY,
                gdb_target="127.0.0.1:%d" % forward.local_port,
                tunnel_name=self.CLIENT_FORWARD_NAME,
                detail=self._last_detail,
            )

        return VsCodeBridgeState(None, BridgeState.STOPPED, None, detail=self._last_detail)

    def _require_stopped(self) -> None:
        current = self.state
        if current.role is not None and current.state != BridgeState.STOPPED:
            raise RuntimeError(
                "B300 VS Code bridge is already active in %s mode." % current.role.value
            )
        if self._role is not None:
            self.stop()

    def start_local(self, probe: ProbeRef, *, gdb_port: int = 3333,
                    tcl_port: int = 6666, event_sink=None) -> VsCodeBridgeState:
        return self._start_server(
            DebugRole.LOCAL, probe, gdb_port=gdb_port,
            tcl_port=tcl_port, event_sink=event_sink,
        )

    def start_gateway(self, probe: ProbeRef, *, gdb_port: int = 3333,
                      tcl_port: int = 6666, event_sink=None) -> VsCodeBridgeState:
        return self._start_server(
            DebugRole.GATEWAY, probe, gdb_port=gdb_port,
            tcl_port=tcl_port, event_sink=event_sink,
        )

    def _guard_event(self, event: str, message: str) -> None:
        self._last_detail = "Run-state guard %s: %s" % (event, message)

    def _openocd_event(self, line: str, event_sink=None) -> None:
        if event_sink is not None:
            try:
                event_sink(line)
            except Exception:
                # Presentation/log consumers must never take down the safety guard.
                pass
        guard = self._guard
        if guard is None:
            return
        try:
            guard.handle_openocd_line(line)
        except Exception as error:
            self._last_detail = "Run-state guard warning: %s" % error

    def _start_server(self, role: DebugRole, probe: ProbeRef, *, gdb_port: int,
                      tcl_port: int, event_sink=None) -> VsCodeBridgeState:
        if role not in (DebugRole.LOCAL, DebugRole.GATEWAY):
            raise ValueError("Only LOCAL/GATEWAY roles may start local OpenOCD.")
        self._require_stopped()
        config = DebugConfig(
            probe=probe,
            bind_address="127.0.0.1",
            gdb_port=int(gdb_port),
            telnet_port=None,
            tcl_port=int(tcl_port),
        )
        config.validate()
        self._server_config = config
        try:
            self.debug_service.start(
                config,
                event_sink=lambda line: self._openocd_event(line, event_sink),
            )
            tcl = self._tcl_factory(TclEndpoint("127.0.0.1", config.tcl_port))
            guard = self._guard_factory(tcl, event_sink=self._guard_event)
            guard.capture_initial_state()
            self._guard = guard
        except Exception:
            try:
                self.debug_service.stop()
            finally:
                self._guard = None
                self._server_config = None
                self._last_detail = "OpenOCD/run-state guard failed to start."
            raise
        self._role = role
        self._last_detail = (
            "OpenOCD GDB/TCL are private on gateway loopback; only GDB may be SSH-forwarded."
            if role == DebugRole.GATEWAY else
            "OpenOCD GDB/TCL are private on local loopback."
        )
        return self.state

    def start_client(self, session: RemoteSession, *, remote_gdb_port: int = 3333,
                     local_gdb_port: int = 0) -> VsCodeBridgeState:
        self._require_stopped()
        if not session.connected:
            raise RuntimeError("Remote B300 SSH session must be connected before Debug Client starts.")
        forward = session.open_forward(
            self.CLIENT_FORWARD_NAME,
            remote_port=int(remote_gdb_port),
            local_port=int(local_gdb_port),
            remote_host="127.0.0.1",
            local_host="127.0.0.1",
        )
        self._role = DebugRole.CLIENT
        self._remote_session = session
        self._remote_forward = forward
        self._last_detail = "VS Code GDB is forwarded through the authenticated SSH session."
        return self.state

    def stop(self) -> VsCodeBridgeState:
        role = self._role
        cleanup_detail = ""
        try:
            if role in (DebugRole.LOCAL, DebugRole.GATEWAY):
                guard = self._guard
                if guard is not None:
                    try:
                        snapshot = guard.restore_initial_state(reason="bridge_stop")
                        cleanup_detail = (
                            "Target state restored to %s." % snapshot.final_target_state
                            if snapshot.restored else
                            "Target state checked: %s." % snapshot.final_target_state
                        )
                    except Exception as error:
                        cleanup_detail = "WARNING: target run-state restoration failed: %s" % error
                self.debug_service.stop()
            elif role == DebugRole.CLIENT and self._remote_session is not None:
                self._remote_session.close_forward(self.CLIENT_FORWARD_NAME)
        finally:
            self._role = None
            self._remote_session = None
            self._remote_forward = None
            self._server_config = None
            self._guard = None
            self._last_detail = cleanup_detail
        return self.state

    def profile(self, *, program_relative: str, gdb_path: Optional[str] = None,
                name: Optional[str] = None) -> VsCodeExternalProfile:
        current = self.state
        if current.state != BridgeState.READY or not current.gdb_target:
            raise RuntimeError("B300 VS Code bridge must be READY before generating a debug profile.")
        if gdb_path is None:
            try:
                gdb_path = resolve_gdb()
            except Exception:
                gdb_path = "arm-none-eabi-gdb"
        selected_name = name or (
            "B300 STM32F407 · Remote via Gateway"
            if current.role == DebugRole.CLIENT else
            "B300 STM32F407 · Local ST-Link"
        )
        if current.role == DebugRole.GATEWAY:
            selected_name = name or "B300 STM32F407 · Gateway Local Debug"
        return VsCodeExternalProfile(
            name=selected_name,
            executable=workspace_executable(program_relative),
            gdb_target=current.gdb_target,
            gdb_path=gdb_path,
        )


__all__ = [
    "BridgeState",
    "DebugRole",
    "VsCodeBridgeState",
    "VsCodeDebugBridge",
    "VsCodeExternalProfile",
    "launch_vscode",
    "resolve_vscode",
]
