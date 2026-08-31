"""Operator-first setup helpers for a fresh B300 workstation.

System changes are explicit and idempotent. On Windows, ST-Link driver
installation accepts only an official STSW-LINK009-style package selected from
trusted local storage or bundled with the application after redistribution is
approved.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence, Tuple

from .linux_usb import perform_linux_usb_setup
from .process_startup import child_process_kwargs
from .ssh_identity import inspect_ssh_client_prerequisites, prepare_ssh_client_prerequisites

STLINK_OFFICIAL_URL = "https://www.st.com/en/development-tools/stsw-link009.html"
_STLINK_VID_RE = re.compile(r"VID[_ ]?0483", re.IGNORECASE)
_STLINK_PID_RE = re.compile(r"PID[_ ]?374[0-9A-F]?", re.IGNORECASE)
_ALLOWED_INF_NAMES = {
    "stlink_dbg_winusb.inf",
    "stlink_vcp.inf",
    "stlink_bridge_winusb.inf",
}


@dataclass(frozen=True)
class SetupComponent:
    component_id: str
    title: str
    state: str
    required: bool
    installable: bool
    detail: str
    action: str = ""


@dataclass(frozen=True)
class MachineSetupReport:
    platform: str
    components: Tuple[SetupComponent, ...]

    @property
    def required_ready(self) -> bool:
        return all(item.state == "ready" for item in self.components if item.required)

    @property
    def missing_required(self) -> Tuple[SetupComponent, ...]:
        return tuple(item for item in self.components if item.required and item.state != "ready")


@dataclass(frozen=True)
class SetupInstallResult:
    component_id: str
    changed: bool
    succeeded: bool
    message: str


class DriverPackageRequired(RuntimeError):
    """Official STSW-LINK009 payload must be supplied before installation."""


def _run(argv: Sequence[str], timeout: float = 60.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        tuple(str(x) for x in argv), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout, check=False,
        **child_process_kwargs("windows" if os.name == "nt" else None),
    )


def _powershell() -> str:
    return shutil.which("powershell.exe") or shutil.which("powershell") or "powershell.exe"


def _parse_json_records(text: str) -> tuple[dict, ...]:
    if not str(text).strip():
        return ()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return ()
    if isinstance(payload, dict):
        return (payload,)
    if isinstance(payload, list):
        return tuple(item for item in payload if isinstance(item, dict))
    return ()


def inspect_windows_stlink_driver(*, runner: Callable = _run) -> SetupComponent:
    ps = _powershell()
    script = (
        "$d=Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | "
        "Where-Object { $_.InstanceId -like 'USB\\VID_0483&PID_374*' }; "
        "if($d){$d | Select-Object Status,FriendlyName,InstanceId | ConvertTo-Json -Compress}"
    )
    try:
        current = runner((ps, "-NoProfile", "-NonInteractive", "-Command", script), 30.0)
        records = _parse_json_records(current.stdout or "")
    except Exception:
        records = ()
    if records:
        statuses = {str(item.get("Status") or "").strip().lower() for item in records}
        names = [str(item.get("FriendlyName") or "ST-Link") for item in records]
        if statuses and statuses <= {"ok"}:
            return SetupComponent(
                "stlink_driver", "ST-Link USB Driver", "ready", True, False,
                "Driver hoạt động · %s" % ", ".join(names[:2]),
            )
        return SetupComponent(
            "stlink_driver", "ST-Link USB Driver", "missing", True, True,
            "Windows nhìn thấy ST-Link nhưng driver chưa hoạt động đúng.", "install_stlink_driver",
        )

    try:
        store = runner(("pnputil.exe", "/enum-drivers"), 30.0)
        text = ((store.stdout or "") + "\n" + (store.stderr or "")).lower()
    except Exception:
        text = ""
    if "stlink" in text or "stmicroelectronics" in text or "stlink_dbg_winusb.inf" in text:
        return SetupComponent(
            "stlink_driver", "ST-Link USB Driver", "ready", True, False,
            "Driver ST-Link đã có trong Windows Driver Store; cắm ST-Link để xác nhận thiết bị.",
        )
    return SetupComponent(
        "stlink_driver", "ST-Link USB Driver", "missing", True, True,
        "Chưa tìm thấy driver ST-Link chính thức trên máy.", "install_stlink_driver",
    )


def inspect_machine_setup(*, openocd_ready: bool, system_name: Optional[str] = None,
                          runner: Callable = _run) -> MachineSetupReport:
    system = (system_name or platform.system()).strip().lower()
    components = []
    components.append(SetupComponent(
        "openocd", "OpenOCD", "ready" if openocd_ready else "missing", True,
        not openocd_ready,
        "Đã đóng kèm trong B300 Tools." if openocd_ready else "OpenOCD chưa sẵn sàng.",
        "install_openocd" if not openocd_ready else "",
    ))
    if system == "windows":
        components.insert(0, inspect_windows_stlink_driver(runner=runner))
    elif system in {"linux", "ubuntu"}:
        report = perform_linux_usb_setup(system="Linux", install_requested=False, confirmed=False)
        components.insert(0, SetupComponent(
            "linux_udev", "Quyền USB ST-Link (udev)",
            "ready" if report.rule_installed else "missing", True, not report.rule_installed,
            "udev rule đã sẵn sàng." if report.rule_installed else "Thiếu udev rule để user thường truy cập ST-Link.",
            "install_linux_udev" if not report.rule_installed else "",
        ))
    try:
        ssh = inspect_ssh_client_prerequisites(system_name=system, runner=runner)
        components.append(SetupComponent(
            "openssh_client", "OpenSSH Client", "ready" if ssh.ready else "optional", False,
            not ssh.ready, "Sẵn sàng cho kết nối từ xa." if ssh.ready else "Chỉ cần khi máy này dùng kết nối từ xa.",
            "install_openssh_client" if not ssh.ready else "",
        ))
    except Exception as error:
        components.append(SetupComponent(
            "openssh_client", "OpenSSH Client", "optional", False, False,
            "Không kiểm tra được OpenSSH Client: %s" % error,
        ))
    components.append(SetupComponent(
        "runtime", "B300 Runtime", "ready", True, False,
        "Ứng dụng chạy độc lập; không cần cài Python hay thư viện ngoài.",
    ))
    return MachineSetupReport("linux" if system == "ubuntu" else system, tuple(components))


def _safe_driver_inf_files(root: Path) -> tuple[Path, ...]:
    base = Path(root).resolve()
    if not base.exists():
        return ()
    candidates = []
    for path in base.rglob("*.inf"):
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(base)
        except (OSError, ValueError):
            continue
        name = resolved.name.lower()
        if name not in _ALLOWED_INF_NAMES and "stlink" not in name:
            continue
        try:
            text = resolved.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if _STLINK_VID_RE.search(text) and _STLINK_PID_RE.search(text):
            candidates.append(resolved)
    return tuple(sorted(set(candidates)))


def find_local_stlink_driver_package() -> Optional[Path]:
    """Find an already-approved local STSW-LINK009 source without network access."""
    candidates = []
    override = os.environ.get("B300_STLINK_DRIVER_PACKAGE", "").strip()
    if override:
        candidates.append(Path(override).expanduser())
    executable_root = Path(sys.executable).resolve().parent
    candidates.extend([
        executable_root / "vendor" / "stlink-driver",
        executable_root.parent / "vendor" / "stlink-driver",
        Path(__file__).resolve().parents[1] / "vendor" / "stlink-driver",
    ])
    for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env_name, "").strip()
        if base:
            candidates.append(
                Path(base) / "STMicroelectronics" / "STM32Cube" / "STM32CubeProgrammer" / "Drivers"
            )
    for candidate in candidates:
        try:
            selected = candidate.resolve()
        except OSError:
            continue
        if selected.is_file() and selected.suffix.lower() == ".zip":
            return selected
        if not selected.is_dir():
            continue
        zips = sorted(selected.glob("*.zip"))
        if zips:
            return zips[0]
        try:
            if _safe_driver_inf_files(selected):
                return selected
        except OSError:
            continue
    return None


def validate_stlink_driver_package(root: Path) -> tuple[Path, ...]:
    files = _safe_driver_inf_files(Path(root))
    if not files:
        raise ValueError("Gói đã chọn không giống STSW-LINK009 chính thức cho ST-Link VID 0483 / PID 374x.")
    return files


def _extract_driver_zip(archive: Path, destination: Path) -> Path:
    archive = Path(archive).resolve(strict=True)
    with zipfile.ZipFile(str(archive), "r") as package:
        for info in package.infolist():
            target = (destination / info.filename).resolve()
            try:
                target.relative_to(destination.resolve())
            except ValueError as error:
                raise ValueError("Driver ZIP chứa đường dẫn không an toàn.") from error
        package.extractall(str(destination))
    return destination


def _elevated_scan_devices(*, runner: Callable = _run) -> bool:
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8-sig", suffix=".ps1", delete=False) as script:
        script.write("$ErrorActionPreference='Stop'\n")
        script.write("pnputil.exe /scan-devices | Out-Null\n")
        script.write("exit $LASTEXITCODE\n")
        script_path = Path(script.name)
    try:
        ps = _powershell()
        escaped_ps = ps.replace("'", "''")
        escaped_script = str(script_path.resolve()).replace("'", "''")
        argv = (
            ps, "-NoProfile", "-NonInteractive", "-Command",
            "$p=Start-Process -FilePath '%s' -Verb RunAs -WindowStyle Hidden -Wait -PassThru "
            "-ArgumentList @('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File','%s'); exit $p.ExitCode" %
            (escaped_ps, escaped_script),
        )
        return runner(argv, 180.0).returncode == 0
    finally:
        try:
            script_path.unlink()
        except OSError:
            pass


def _elevated_pnputil_install(inf_files: Sequence[Path], *, runner: Callable = _run) -> None:
    if not inf_files:
        raise DriverPackageRequired("Chưa có gói STSW-LINK009 chính thức để cài.")
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8-sig", suffix=".ps1", delete=False) as script:
        script.write("$ErrorActionPreference='Stop'\n")
        for inf in inf_files:
            escaped = str(Path(inf).resolve()).replace("'", "''")
            script.write("& pnputil.exe /add-driver '%s' /install\n" % escaped)
            script.write("if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}\n")
        script.write("pnputil.exe /scan-devices | Out-Null\nexit 0\n")
        script_path = Path(script.name)
    try:
        ps = _powershell()
        escaped_ps = ps.replace("'", "''")
        escaped_script = str(script_path.resolve()).replace("'", "''")
        argv = (
            ps, "-NoProfile", "-NonInteractive", "-Command",
            "$p=Start-Process -FilePath '%s' -Verb RunAs -WindowStyle Hidden -Wait -PassThru "
            "-ArgumentList @('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File','%s'); exit $p.ExitCode" %
            (escaped_ps, escaped_script),
        )
        result = runner(argv, 900.0)
        if result.returncode != 0:
            raise RuntimeError("Cài ST-Link driver thất bại (exit code %d)." % result.returncode)
    finally:
        try:
            script_path.unlink()
        except OSError:
            pass


def install_windows_stlink_driver(package: Optional[Path] = None, *, runner: Callable = _run) -> SetupInstallResult:
    before = inspect_windows_stlink_driver(runner=runner)
    if before.state == "ready":
        return SetupInstallResult("stlink_driver", False, True, "ST-Link driver đã sẵn sàng; không thay đổi hệ thống.")
    if package is None:
        # Give Windows Plug-and-Play one safe chance before requiring the vendor package.
        try:
            if _elevated_scan_devices(runner=runner) and inspect_windows_stlink_driver(runner=runner).state == "ready":
                return SetupInstallResult("stlink_driver", True, True, "Windows đã tự hoàn tất driver ST-Link.")
        except Exception:
            pass
        local_package = find_local_stlink_driver_package()
        if local_package is None:
            raise DriverPackageRequired(
                "Windows chưa tự resolve được driver. Hãy chọn ZIP/thư mục STSW-LINK009 chính thức từ STMicroelectronics."
            )
        package = local_package
    selected = Path(package)
    with tempfile.TemporaryDirectory(prefix="b300-stlink-driver-") as temporary:
        root = Path(temporary)
        if selected.is_file() and selected.suffix.lower() == ".zip":
            source_root = _extract_driver_zip(selected, root)
        elif selected.is_dir():
            source_root = selected
        else:
            raise ValueError("Hãy chọn ZIP hoặc thư mục STSW-LINK009 đã giải nén.")
        inf_files = validate_stlink_driver_package(source_root)
        _elevated_pnputil_install(inf_files, runner=runner)
    after = inspect_windows_stlink_driver(runner=runner)
    return SetupInstallResult(
        "stlink_driver", True, after.state == "ready",
        "ST-Link driver đã được cài; hãy rút/cắm lại ST-Link nếu thiết bị chưa xuất hiện." if after.state == "ready"
        else "Driver đã được thêm vào Windows; hãy rút/cắm lại ST-Link rồi Kiểm tra lại.",
    )


def install_linux_udev() -> SetupInstallResult:
    before = perform_linux_usb_setup(system="Linux", install_requested=False, confirmed=False)
    if before.rule_installed:
        return SetupInstallResult("linux_udev", False, True, "udev rule đã sẵn sàng.")
    after = perform_linux_usb_setup(system="Linux", install_requested=True, confirmed=True)
    return SetupInstallResult("linux_udev", after.changed, after.rule_installed, after.message)


def install_openssh_client(*, system_name: Optional[str] = None) -> SetupInstallResult:
    result = prepare_ssh_client_prerequisites(system_name=system_name)
    return SetupInstallResult(
        "openssh_client", result.changed, result.succeeded,
        "OpenSSH Client đã sẵn sàng." if result.succeeded else "Không thể hoàn tất OpenSSH Client.",
    )
