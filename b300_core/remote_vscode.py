"""Safe VSCode/Cortex-Debug remote profile generation for B300 debug gateways."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Dict, Optional, Tuple

_SAFE_HOST = re.compile(r"^[A-Za-z0-9._:-]+$")
_SAFE_USER = re.compile(r"^[A-Za-z0-9._-]+$")
_SAFE_PROBE = re.compile(r"^[A-Za-z0-9_.:-]+$")


def workspace_executable(relative_path: str) -> str:
    """Return a safe workspace-relative AXF/ELF reference for launch.json."""
    text = str(relative_path).replace("\\", "/").strip()
    path = PurePosixPath(text)
    if not text or path.is_absolute() or ".." in path.parts:
        raise ValueError("VSCode program must be a workspace-relative AXF/ELF path.")
    if path.suffix.lower() not in (".axf", ".elf"):
        raise ValueError("VSCode program must end in .axf or .elf.")
    return "${workspaceFolder}/%s" % path.as_posix()


@dataclass(frozen=True)
class RemoteVsCodeProfile:
    ssh_host: str
    ssh_user: str
    ssh_port: int = 22
    local_gdb_port: int = 3333
    remote_gdb_port: int = 3333
    executable: str = "${workspaceFolder}/build/application.elf"
    gdb_path: str = "arm-none-eabi-gdb"
    rtos: Optional[str] = "FreeRTOS"
    probe_serial: Optional[str] = None
    identity_file: Optional[Path] = None
    known_hosts_file: Optional[Path] = None

    def validate(self) -> None:
        if not self.ssh_host or not _SAFE_HOST.fullmatch(self.ssh_host):
            raise ValueError("SSH host contains unsupported characters.")
        if not self.ssh_user or not _SAFE_USER.fullmatch(self.ssh_user):
            raise ValueError("SSH user contains unsupported characters.")
        for label, port in (
            ("SSH", self.ssh_port),
            ("local GDB", self.local_gdb_port),
            ("remote GDB", self.remote_gdb_port),
        ):
            if not 1 <= int(port) <= 65535:
                raise ValueError("%s port must be in range 1..65535." % label)
        if not self.executable.strip() or "\x00" in self.executable:
            raise ValueError("VSCode executable/symbol path must not be empty.")
        if not self.gdb_path.strip() or "\x00" in self.gdb_path:
            raise ValueError("VSCode GDB path must not be empty.")
        if self.probe_serial is not None and not _SAFE_PROBE.fullmatch(self.probe_serial):
            raise ValueError("ST-Link probe serial contains unsupported characters.")
        if self.identity_file is not None and not Path(self.identity_file).is_file():
            raise ValueError("SSH identity file does not exist: %s" % self.identity_file)
        if self.known_hosts_file is not None and not Path(self.known_hosts_file).is_file():
            raise ValueError("SSH known_hosts file does not exist: %s" % self.known_hosts_file)

    @property
    def ssh_target(self) -> str:
        self.validate()
        return "%s@%s" % (self.ssh_user, self.ssh_host)

    def tunnel_argv(self) -> Tuple[str, ...]:
        self.validate()
        result = [
            "ssh", "-N",
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes",
            "-o", "ExitOnForwardFailure=yes",
            "-o", "ConnectTimeout=8",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3",
            "-L", "127.0.0.1:%d:127.0.0.1:%d" %
                  (self.local_gdb_port, self.remote_gdb_port),
        ]
        if self.identity_file is not None:
            result.extend(("-o", "IdentitiesOnly=yes", "-i", str(Path(self.identity_file))))
        if self.known_hosts_file is not None:
            result.extend(("-o", "UserKnownHostsFile=%s" % Path(self.known_hosts_file)))
        if self.ssh_port != 22:
            result.extend(("-p", str(self.ssh_port)))
        result.append(self.ssh_target)
        return tuple(result)

    def tunnel_command(self) -> str:
        return " ".join(self.tunnel_argv())

    def gateway_argv(self) -> Tuple[str, ...]:
        self.validate()
        result = [
            "b300-stlink", "debug", "gateway",
            "--bind-address", "127.0.0.1",
            "--gdb-port", str(self.remote_gdb_port),
            "--tcl-port", "6666",
        ]
        if self.probe_serial:
            result.extend(("--probe-serial", self.probe_serial))
        return tuple(result)

    def gateway_command(self) -> str:
        return " ".join(self.gateway_argv())

    def cortex_debug_configuration(self) -> Dict[str, object]:
        self.validate()
        config: Dict[str, object] = {
            "name": "B300 STM32F407 · Remote via SSH",
            "type": "cortex-debug",
            "request": "attach",
            "cwd": "${workspaceFolder}",
            "executable": self.executable,
            "servertype": "external",
            "gdbTarget": "127.0.0.1:%d" % self.local_gdb_port,
            "gdbPath": self.gdb_path,
            "toolchainPrefix": "arm-none-eabi",
            "device": "STM32F407ZE",
            "gdbInterruptMode": "exec-interrupt",
            "hardwareBreakpoints": {"require": True, "limit": 6},
            "hardwareWatchpoints": {"require": True, "limit": 4},
            "showDevDebugOutput": "vscode",
        }
        if self.rtos:
            config["rtos"] = self.rtos
        return config

    def launch_json(self) -> Dict[str, object]:
        return {"version": "0.2.0", "configurations": [self.cortex_debug_configuration()]}

    @staticmethod
    def extensions_json() -> Dict[str, object]:
        return {"recommendations": ["marus25.cortex-debug"]}

    def record(self) -> Dict[str, object]:
        self.validate()
        return {
            "schema_version": 1,
            "command": "debug vscode",
            "status": "ok",
            "security": {
                "openocd_bind": "127.0.0.1",
                "gdb_exposed_publicly": False,
                "tcl_bind": "127.0.0.1:6666",
                "tcl_forwarded": False,
                "transport": "SSH local port forwarding",
            },
            "gateway_command": list(self.gateway_argv()),
            "ssh_tunnel_command": list(self.tunnel_argv()),
            "vscode_gdb_target": "127.0.0.1:%d" % self.local_gdb_port,
            "program": self.executable,
            "requires_gateway_gdb": False,
            "requires_vscode_gdb": True,
            "cortex_debug_extension": "marus25.cortex-debug",
            "vscode_launch": self.launch_json(),
        }

    def instructions_text(self) -> str:
        self.validate()
        return (
            "B300 Remote Debug via VSCode + SSH\n\n"
            "1. Gateway machine physically connected to ST-Link:\n"
            "   %s\n\n"
            "2. VSCode/operator machine - keep this SSH tunnel running:\n"
            "   %s\n\n"
            "3. Install Cortex-Debug and an Arm GNU toolchain that provides "
            "arm-none-eabi-gdb, arm-none-eabi-objdump and arm-none-eabi-nm.\n"
            "4. Open the matching source workspace/AXF and select "
            "'B300 STM32F407 · Remote via SSH' in Run and Debug.\n\n"
            "Safety: OpenOCD GDB/TCL stay on gateway loopback. Only GDB is forwarded through SSH.\n"
            "SSH uses BatchMode + strict host-key checking; enroll the trusted Gateway host key and SSH key first.\n"
            "The generated VSCode profile uses request=attach and forces hardware breakpoints/watchpoints.\n"
            "Do not expose ports 3333 or 6666 directly to LAN/Internet.\n"
        ) % (self.gateway_command(), self.tunnel_command())

    @staticmethod
    def _check_output(path: Path, force: bool) -> None:
        if path.exists() and not force:
            raise FileExistsError("Refusing to overwrite existing remote-debug file: %s" % path)

    def write_kit(self, directory: Path, *, force: bool = False) -> Tuple[Path, ...]:
        self.validate()
        root = Path(directory).expanduser().resolve()
        launch = root / ".vscode" / "launch.json"
        extensions = root / ".vscode" / "extensions.json"
        tunnel = root / "b300-ssh-tunnel.txt"
        gateway = root / "b300-gateway-command.txt"
        guide = root / "B300-REMOTE-DEBUG.md"
        outputs = (launch, extensions, tunnel, gateway, guide)
        for output in outputs:
            self._check_output(output, force)
        launch.parent.mkdir(parents=True, exist_ok=True)
        root.mkdir(parents=True, exist_ok=True)
        launch.write_text(json.dumps(self.launch_json(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        extensions.write_text(json.dumps(self.extensions_json(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tunnel.write_text(self.tunnel_command() + "\n", encoding="utf-8")
        gateway.write_text(self.gateway_command() + "\n", encoding="utf-8")
        guide.write_text(self.instructions_text(), encoding="utf-8")
        return outputs
