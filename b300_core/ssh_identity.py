"""B300-managed SSH client identity and Gateway public-key authorization."""

from __future__ import annotations

import base64
import hashlib
import os
import platform
import shutil
import subprocess
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

_ALLOWED_KEY_TYPES = {"ssh-ed25519"}

@dataclass(frozen=True)
class SshIdentityReport:
    private_key: Path
    public_key: Path
    keygen_available: bool
    pair_exists: bool
    public_key_text: Optional[str]
    fingerprint: Optional[str]
    ready: bool

@dataclass(frozen=True)
class AuthorizedKeyResult:
    target: Path
    fingerprint: str
    changed: bool
    administrator_target: bool

@dataclass(frozen=True)
class SshClientPrerequisiteReport:
    platform: str
    ssh_executable: Optional[Path]
    keygen_executable: Optional[Path]
    installed: bool
    ready: bool
    actions: tuple[str, ...]
    changes_required: bool


@dataclass(frozen=True)
class SshClientPrepareResult:
    before: SshClientPrerequisiteReport
    after: SshClientPrerequisiteReport
    changed: bool
    succeeded: bool

CommandRunner = Callable[[Sequence[str], float], subprocess.CompletedProcess]

def _run(argv: Sequence[str], timeout: float = 30.0) -> subprocess.CompletedProcess:
    return subprocess.run(tuple(str(x) for x in argv), capture_output=True, text=True, timeout=timeout, check=False, creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW") else 0))

def _windows_openssh_binary(name: str) -> Optional[Path]:
    root = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "OpenSSH"
    candidate = root / (name if name.lower().endswith(".exe") else name + ".exe")
    return candidate if candidate.is_file() else None


def resolve_ssh_client_executable(name: str = "ssh") -> Optional[Path]:
    resolved = shutil.which(name)
    if resolved:
        return Path(resolved)
    if platform.system().lower() == "windows":
        return _windows_openssh_binary(name)
    return None


def inspect_ssh_client_prerequisites(
        *, runner: CommandRunner = _run, system_name: Optional[str] = None,
) -> SshClientPrerequisiteReport:
    system = (system_name or platform.system()).strip().lower()
    ssh = resolve_ssh_client_executable("ssh")
    keygen = resolve_ssh_client_executable("ssh-keygen")
    actions = []
    if system == "windows":
        ps = shutil.which("powershell.exe") or shutil.which("powershell") or "powershell.exe"
        script = (
            "$c=Get-WindowsCapability -Online -Name 'OpenSSH.Client~~~~0.0.1.0' "
            "-ErrorAction SilentlyContinue; if($c){$c.State}else{'NotPresent'}"
        )
        result = runner((ps, "-NoProfile", "-NonInteractive", "-Command", script), 30.0)
        installed = (result.stdout or "").strip().lower() == "installed" or (ssh is not None and keygen is not None)
        if not installed or ssh is None or keygen is None:
            actions.append("install_openssh_client")
    elif system in {"linux", "ubuntu"}:
        package = runner(("dpkg-query", "-W", "-f=${Status}", "openssh-client"), 20.0)
        installed = package.returncode == 0 and "install ok installed" in (package.stdout or "").lower()
        if not installed or ssh is None or keygen is None:
            actions.append("install_openssh_client")
    else:
        raise RuntimeError("B300 SSH Client setup supports Windows and Ubuntu/Linux only.")
    ready = installed and ssh is not None and keygen is not None
    return SshClientPrerequisiteReport(
        system if system != "ubuntu" else "linux", ssh, keygen, installed, ready,
        tuple(actions), bool(actions),
    )


def _prepare_windows_ssh_client(runner: CommandRunner) -> None:
    ps = shutil.which("powershell.exe") or shutil.which("powershell") or "powershell.exe"
    script_file = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8-sig", suffix=".ps1", delete=False, newline="\n"
    )
    try:
        with script_file:
            script_file.write(
                "$ErrorActionPreference='Stop'\n"
                "$c=Get-WindowsCapability -Online -Name 'OpenSSH.Client~~~~0.0.1.0'\n"
                "if($c.State -ne 'Installed'){Add-WindowsCapability -Online -Name 'OpenSSH.Client~~~~0.0.1.0' | Out-Null}\n"
                "exit 0\n"
            )
        path = str(Path(script_file.name).resolve()).replace("'", "''")
        escaped_ps = ps.replace("'", "''")
        command = (
            ps, "-NoProfile", "-Command",
            "$p=Start-Process -FilePath '%s' -Verb RunAs -Wait -PassThru "
            "-ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File','%s'); exit $p.ExitCode" %
            (escaped_ps, path),
        )
        result = runner(command, 900.0)
        if result.returncode != 0:
            raise RuntimeError("Elevated Windows OpenSSH Client setup failed with exit code %d." % result.returncode)
    finally:
        try:
            Path(script_file.name).unlink()
        except OSError:
            pass


def _linux_privileged_prefix() -> tuple[str, ...]:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return ()
    pkexec = shutil.which("pkexec")
    if pkexec:
        return (pkexec,)
    raise RuntimeError(
        "Administrator privileges are required. GUI setup needs pkexec/policykit; "
        "otherwise run B300 Tools from an already elevated/root session."
    )


def prepare_ssh_client_prerequisites(
        *, runner: CommandRunner = _run, system_name: Optional[str] = None,
        inspector: Callable[..., SshClientPrerequisiteReport] = inspect_ssh_client_prerequisites,
) -> SshClientPrepareResult:
    before = inspector(runner=runner, system_name=system_name)
    if before.ready:
        return SshClientPrepareResult(before, before, False, True)
    if "install_openssh_client" not in before.actions:
        return SshClientPrepareResult(before, before, False, False)
    if before.platform == "windows":
        _prepare_windows_ssh_client(runner)
    elif before.platform == "linux":
        prefix = _linux_privileged_prefix()
        for command in (
            prefix + ("apt-get", "update"),
            prefix + ("env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y", "openssh-client"),
        ):
            result = runner(command, 900.0)
            if result.returncode != 0:
                raise RuntimeError(
                    "OpenSSH Client setup command failed (%d): %s\n%s" %
                    (result.returncode, " ".join(command), (result.stderr or "").strip())
                )
    else:
        raise RuntimeError("Unsupported SSH Client setup platform: %s" % before.platform)
    after = inspector(runner=runner, system_name=system_name)
    return SshClientPrepareResult(before, after, True, after.ready)


def default_b300_identity_path(home: Optional[Path] = None) -> Path:
    return Path(home or Path.home()) / ".ssh" / "b300_gateway_ed25519"

def validate_public_key(value: str) -> str:
    line = str(value).strip()
    if "\n" in line or "\r" in line:
        raise ValueError("Exactly one OpenSSH public-key line is required.")
    parts = line.split()
    if len(parts) < 2 or parts[0] not in _ALLOWED_KEY_TYPES:
        raise ValueError("B300 accepts an ssh-ed25519 public key.")
    try:
        raw = base64.b64decode(parts[1].encode("ascii"), validate=True)
    except Exception as error:
        raise ValueError("SSH public-key payload is not valid base64.") from error
    try:
        offset = 0
        if len(raw) < 4:
            raise ValueError
        type_length = struct.unpack_from(">I", raw, offset)[0]
        offset += 4
        key_type = raw[offset:offset + type_length]
        offset += type_length
        if key_type != b"ssh-ed25519" or len(raw) < offset + 4:
            raise ValueError
        key_length = struct.unpack_from(">I", raw, offset)[0]
        offset += 4
        key_bytes = raw[offset:offset + key_length]
        offset += key_length
        if key_length != 32 or len(key_bytes) != 32 or offset != len(raw):
            raise ValueError
    except (ValueError, struct.error):
        raise ValueError("SSH public-key payload is not a canonical ssh-ed25519 key.")
    return " ".join(parts)

def public_key_identity(value: str) -> str:
    normalized = validate_public_key(value)
    parts = normalized.split()
    return "%s %s" % (parts[0], parts[1])

def public_key_fingerprint(value: str) -> str:
    normalized = validate_public_key(value)
    raw = base64.b64decode(normalized.split()[1].encode("ascii"), validate=True)
    digest = base64.b64encode(hashlib.sha256(raw).digest()).decode("ascii").rstrip("=")
    return "SHA256:%s" % digest

def inspect_ssh_identity(path: Optional[Path] = None) -> SshIdentityReport:
    private = Path(path or default_b300_identity_path()).expanduser()
    public = Path(str(private) + ".pub")
    keygen = resolve_ssh_client_executable("ssh-keygen") is not None
    private_exists = private.is_file()
    public_exists = public.is_file()
    if private_exists != public_exists:
        return SshIdentityReport(private, public, keygen, False, None, None, False)
    if not private_exists:
        return SshIdentityReport(private, public, keygen, False, None, None, False)
    try:
        text = validate_public_key(public.read_text(encoding="utf-8"))
        fingerprint = public_key_fingerprint(text)
    except (OSError, ValueError, UnicodeError):
        return SshIdentityReport(private, public, keygen, True, None, None, False)
    return SshIdentityReport(private, public, keygen, True, text, fingerprint, True)

def ensure_ssh_identity(path: Optional[Path] = None, *, runner: CommandRunner = _run) -> SshIdentityReport:
    private = Path(path or default_b300_identity_path()).expanduser()
    before = inspect_ssh_identity(private)
    if before.ready:
        return before
    if private.exists() or Path(str(private) + ".pub").exists():
        raise RuntimeError("B300 SSH identity is incomplete/corrupt; refusing to overwrite either key file.")
    executable_path = resolve_ssh_client_executable("ssh-keygen")
    if executable_path is None:
        raise RuntimeError("ssh-keygen was not found. Prepare OpenSSH Client first.")
    executable = str(executable_path)
    private.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(str(private.parent), 0o700)
    result = runner((executable, "-q", "-t", "ed25519", "-N", "", "-C", "b300-stlink-tools", "-f", str(private)), 60.0)
    if result.returncode != 0:
        raise RuntimeError("ssh-keygen failed: %s" % ((result.stderr or result.stdout or "unknown error").strip()))
    after = inspect_ssh_identity(private)
    if not after.ready:
        raise RuntimeError("ssh-keygen completed but the B300 identity did not verify.")
    if os.name != "nt":
        os.chmod(str(private), 0o600)
        os.chmod(str(after.public_key), 0o644)
    return after

def _contains_key(path: Path, public_key: str) -> bool:
    needle = public_key_identity(public_key)
    if not path.is_file():
        return False
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return False
    for line in lines:
        try:
            if public_key_identity(line) == needle:
                return True
        except ValueError:
            continue
    return False

def _windows_admin_member(runner: CommandRunner) -> bool:
    ps = shutil.which("powershell.exe") or "powershell.exe"
    script = "$ids=[Security.Principal.WindowsIdentity]::GetCurrent().Groups | ForEach-Object {$_.Value}; if($ids -contains 'S-1-5-32-544'){'YES'}else{'NO'}"
    result = runner((ps, "-NoProfile", "-NonInteractive", "-Command", script), 20.0)
    return result.returncode == 0 and (result.stdout or "").strip() == "YES"

def authorized_keys_target(*, system_name: Optional[str] = None, runner: CommandRunner = _run, home: Optional[Path] = None, program_data: Optional[Path] = None) -> tuple[Path, bool]:
    system = (system_name or platform.system()).lower()
    if system == "windows" and _windows_admin_member(runner):
        root = Path(program_data or os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
        return root / "ssh" / "administrators_authorized_keys", True
    return Path(home or Path.home()) / ".ssh" / "authorized_keys", False

def _install_user_authorized_key(target: Path, public_key: str) -> bool:
    if _contains_key(target, public_key):
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(str(target.parent), 0o700)
    with target.open("a", encoding="utf-8", newline="\n") as handle:
        if target.stat().st_size:
            handle.write("\n")
        handle.write(validate_public_key(public_key) + "\n")
    if os.name != "nt":
        os.chmod(str(target), 0o600)
    return True

def _install_windows_admin_key(target: Path, public_key: str, runner: CommandRunner) -> bool:
    if _contains_key(target, public_key):
        return False
    key_file = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".pub", delete=False, newline="\n")
    try:
        with key_file:
            key_file.write(validate_public_key(public_key) + "\n")
        ps = shutil.which("powershell.exe") or "powershell.exe"
        script_file = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8-sig", suffix=".ps1", delete=False, newline="\n")
        try:
            script = """$ErrorActionPreference='Stop'
$line=(Get-Content -LiteralPath '%s' -Raw).Trim()
$target='%s'
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
if(-not (Test-Path -LiteralPath $target)){New-Item -ItemType File -Path $target | Out-Null}
$parts=$line -split '\\s+'
$needle=$parts[0]+' '+$parts[1]
$found=$false
foreach($existing in (Get-Content -LiteralPath $target -ErrorAction SilentlyContinue)){
  $ep=$existing.Trim() -split '\\s+'
  if($ep.Length -ge 2 -and ($ep[0]+' '+$ep[1]) -eq $needle){$found=$true; break}
}
if(-not $found){Add-Content -LiteralPath $target -Value $line}
icacls $target /inheritance:r | Out-Null
icacls $target /grant '*S-1-5-32-544:F' /grant 'SYSTEM:F' | Out-Null
exit 0
""" % (str(Path(key_file.name).resolve()).replace("'", "''"), str(target).replace("'", "''"))
            with script_file:
                script_file.write(script)
            command = (ps, "-NoProfile", "-Command", "$p=Start-Process -FilePath '%s' -Verb RunAs -Wait -PassThru -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File','%s'); exit $p.ExitCode" % (ps.replace("'", "''"), str(Path(script_file.name).resolve()).replace("'", "''")))
            result = runner(command, 300.0)
            if result.returncode != 0:
                raise RuntimeError("Elevated authorized_keys update failed with exit code %d." % result.returncode)
        finally:
            try: Path(script_file.name).unlink()
            except OSError: pass
    finally:
        try: Path(key_file.name).unlink()
        except OSError: pass
    return True

def install_gateway_public_key(public_key: str, *, system_name: Optional[str] = None, runner: CommandRunner = _run, home: Optional[Path] = None, program_data: Optional[Path] = None) -> AuthorizedKeyResult:
    normalized = validate_public_key(public_key)
    target, admin_target = authorized_keys_target(system_name=system_name, runner=runner, home=home, program_data=program_data)
    if admin_target:
        changed = _install_windows_admin_key(target, normalized, runner)
    else:
        changed = _install_user_authorized_key(target, normalized)
    return AuthorizedKeyResult(target, public_key_fingerprint(normalized), changed, admin_target)

def managed_identity_file(path: Optional[Path] = None) -> Optional[Path]:
    """Return the verified B300 private-key path without reading or exposing its contents."""
    report = inspect_ssh_identity(path)
    return report.private_key if report.ready else None
