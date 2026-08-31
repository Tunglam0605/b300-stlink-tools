"""B300-managed SSH client identity and Gateway public-key authorization."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import re
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
    target_verified: bool = False

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
    if platform.system().lower() == "windows":
        system_binary = _windows_openssh_binary(name)
        if system_binary is not None:
            return system_binary
    resolved = shutil.which(name)
    return Path(resolved) if resolved else None


def _trusted_windows_powershell_executable() -> Path:
    """Return the OS-provided PowerShell path; never use PATH for UAC execution."""
    if os.name != "nt":
        return Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
    try:
        import ctypes
        buffer = ctypes.create_unicode_buffer(32768)
        length = ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer))
    except (AttributeError, OSError) as error:
        raise RuntimeError("B300 cannot locate trusted Windows PowerShell for elevation.") from error
    if not length or length >= len(buffer):
        raise RuntimeError("B300 cannot locate trusted Windows PowerShell for elevation.")
    candidate = Path(buffer.value) / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if not candidate.is_file():
        raise RuntimeError("Trusted Windows PowerShell executable was not found.")
    return candidate


def _trusted_windows_sshd_executable() -> Path:
    """Return only the OS OpenSSH server binary that may run after UAC."""
    if os.name != "nt":
        return Path(r"C:\Windows\System32\OpenSSH\sshd.exe")
    try:
        import ctypes
        buffer = ctypes.create_unicode_buffer(32768)
        length = ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer))
    except (AttributeError, OSError) as error:
        raise RuntimeError("B300 cannot locate trusted Windows OpenSSH Server for elevation.") from error
    if not length or length >= len(buffer):
        raise RuntimeError("B300 cannot locate trusted Windows OpenSSH Server for elevation.")
    candidate = Path(buffer.value) / "OpenSSH" / "sshd.exe"
    if not candidate.is_file():
        raise RuntimeError("Trusted Windows OpenSSH Server executable was not found.")
    return candidate


def _windows_authorized_keys_target_from_sshd_output(
        output: str, *, home: Path, program_data: Path,
) -> tuple[Path, bool]:
    """Accept only the two authorized-keys locations B300 can safely manage."""
    values = []
    for line in output.splitlines():
        name, _, value = line.strip().partition(" ")
        if name.lower() == "authorizedkeysfile" and value.strip():
            values.append(value.strip().strip('"'))
    if len(values) != 1:
        raise RuntimeError("B300 cannot safely determine the Windows SSH authorized-keys target from sshd configuration.")
    normalized = tuple(part.replace("\\", "/").lower() for part in values[0].split())
    if normalized in {
        (".ssh/authorized_keys",),
        (".ssh/authorized_keys", ".ssh/authorized_keys2"),
    }:
        return home / ".ssh" / "authorized_keys", False
    if normalized == ("__programdata__/ssh/administrators_authorized_keys",):
        return program_data / "ssh" / "administrators_authorized_keys", True
    raise RuntimeError("B300 cannot safely determine the Windows SSH authorized-keys target: sshd is configured with an unsupported AuthorizedKeysFile.")


def _windows_effective_authorized_keys_target_with_disposable_host_key(
        user: str, *, runner: CommandRunner, home: Path, program_data: Path,
) -> tuple[Path, bool]:
    """Query the real sshd config without reading its protected production host keys.

    Windows may deny even an elevated desktop process access to the service's
    host private keys.  ``sshd -T -h`` needs any valid host key only to parse
    the configuration, so a private temporary key avoids that unrelated
    access check.  It is never installed or exposed and is removed on return.
    """
    sshd = _trusted_windows_sshd_executable()
    keygen = sshd.with_name("ssh-keygen.exe")
    if not keygen.is_file():
        raise RuntimeError("B300 cannot safely query Windows sshd because the trusted ssh-keygen.exe was not found.")
    with tempfile.TemporaryDirectory(prefix="b300-sshd-config-") as directory:
        host_key = Path(directory) / "ssh_host_ed25519_key"
        generated = runner(
            (str(keygen), "-q", "-t", "ed25519", "-N", "", "-f", str(host_key)),
            60.0,
        )
        if generated.returncode != 0:
            raise RuntimeError("B300 could not create a disposable host key for the Windows sshd configuration check.")
        completed = runner(
            (str(sshd), "-T", "-h", str(host_key), "-C", "user=%s,host=localhost,addr=127.0.0.1" % user),
            20.0,
        )
        if completed.returncode != 0:
            raise RuntimeError("B300 could not read the effective Windows sshd configuration with a disposable host key.")
        return _windows_authorized_keys_target_from_sshd_output(
            completed.stdout or "", home=home, program_data=program_data,
        )


def _trusted_windows_known_folder(csidl: int, fallback: Path, label: str) -> Path:
    """Use the Windows shell API instead of caller-controlled environment paths."""
    if os.name != "nt":
        return fallback
    try:
        import ctypes
        buffer = ctypes.create_unicode_buffer(32768)
        result = ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buffer)
    except (AttributeError, OSError) as error:
        raise RuntimeError("B300 cannot locate trusted Windows %s." % label) from error
    if result != 0 or not buffer.value:
        raise RuntimeError("B300 cannot locate trusted Windows %s." % label)
    return Path(buffer.value)


def _trusted_windows_profile_directory() -> Path:
    return _trusted_windows_known_folder(0x0028, Path.home(), "user profile directory")


def _trusted_windows_program_data_directory() -> Path:
    return _trusted_windows_known_folder(0x0023, Path(r"C:\ProgramData"), "ProgramData directory")


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
    ps = str(_trusted_windows_powershell_executable())
    script = (
        "$ErrorActionPreference='Stop'\n"
        "$c=Get-WindowsCapability -Online -Name 'OpenSSH.Client~~~~0.0.1.0'\n"
        "if($c.State -ne 'Installed'){Add-WindowsCapability -Online -Name 'OpenSSH.Client~~~~0.0.1.0' | Out-Null}\n"
        "exit 0\n"
    )
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    command = (
        ps, "-NoProfile", "-NonInteractive", "-Command",
        "$ErrorActionPreference='Stop';try{$p=Start-Process -FilePath '%s' -Verb RunAs -WindowStyle Hidden -Wait -PassThru "
        "-ArgumentList @('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-EncodedCommand','%s');exit $p.ExitCode}catch{exit 1}" %
        (ps.replace("'", "''"), encoded),
    )
    result = runner(command, 900.0)
    if result.returncode != 0:
        raise RuntimeError("Elevated Windows OpenSSH Client setup failed with exit code %d." % result.returncode)


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

def _windows_effective_authorized_keys_target(
        *, runner: CommandRunner, home: Path, program_data: Path,
) -> tuple[Path, bool]:
    """Ask sshd which key file it will use for the current Windows account.

    `Match Group administrators` is evaluated by sshd, not by a potentially
    UAC-filtered GUI token.  Never guess a writable target when that query is
    unavailable or points outside B300's two explicitly supported locations.
    """
    sshd = _trusted_windows_sshd_executable()
    ps = str(_trusted_windows_powershell_executable())
    identity = runner(
        (ps, "-NoProfile", "-NonInteractive", "-Command", "[Security.Principal.WindowsIdentity]::GetCurrent().Name"),
        20.0,
    )
    user = (identity.stdout or "").strip()
    if identity.returncode != 0 or not re.fullmatch(r"[^\\,=\r\n]{1,128}\\[^\\,=\r\n]{1,128}", user):
        raise RuntimeError("B300 cannot safely determine the Windows SSH authorized-keys target for this account identity.")
    completed = runner(
        (str(sshd), "-T", "-C", "user=%s,host=localhost,addr=127.0.0.1" % user), 20.0,
    )
    if completed.returncode != 0:
        if "no hostkeys available" in (completed.stderr or "").lower():
            return _windows_effective_authorized_keys_target_with_disposable_host_key(
                user, runner=runner, home=home, program_data=program_data,
            )
        raise RuntimeError("B300 cannot safely determine the Windows SSH authorized-keys target from sshd configuration.")
    return _windows_authorized_keys_target_from_sshd_output(
        completed.stdout or "", home=home, program_data=program_data,
    )


def authorized_keys_target(*, system_name: Optional[str] = None, runner: CommandRunner = _run, home: Optional[Path] = None, program_data: Optional[Path] = None) -> tuple[Path, bool]:
    system = (system_name or platform.system()).lower()
    user_home = Path(home) if home is not None else (
        _trusted_windows_profile_directory() if system == "windows" else Path.home()
    )
    if system == "windows":
        root = Path(program_data) if program_data is not None else _trusted_windows_program_data_directory()
        return _windows_effective_authorized_keys_target(
            runner=runner, home=user_home, program_data=root,
        )
    return user_home / ".ssh" / "authorized_keys", False

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

def _install_windows_key(
        target: Path, public_key: str, runner: CommandRunner, *, administrator_target: bool,
) -> bool:
    """Install/reconcile one key and its sshd-required Windows ACL without a console."""
    ps = str(_trusted_windows_powershell_executable())
    owner_sid = "S-1-5-32-544" if administrator_target else ""
    script = """$ErrorActionPreference='Stop'
$line='%s'
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
$ownerSid='%s'
if(-not $ownerSid){$ownerSid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value}
$owner=[Security.Principal.SecurityIdentifier]::new($ownerSid)
$system=[Security.Principal.SecurityIdentifier]::new('S-1-5-18')
$acl=New-Object Security.AccessControl.FileSecurity
$acl.SetOwner($owner)
$acl.SetAccessRuleProtection($true,$false)
$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($owner,[Security.AccessControl.FileSystemRights]::FullControl,[Security.AccessControl.AccessControlType]::Allow))
$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($system,[Security.AccessControl.FileSystemRights]::FullControl,[Security.AccessControl.AccessControlType]::Allow))
Set-Acl -LiteralPath $target -AclObject $acl
""" % (
        validate_public_key(public_key).replace("'", "''"), str(target).replace("'", "''"), owner_sid,
    )
    script += _windows_authorized_key_verification_script(
        target, public_key, administrator_target=administrator_target,
    )
    if administrator_target:
        script += """if(-not $verification.key_present){exit 11}
if(-not $verification.acl_safe){exit 12}
exit 0
"""
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        launch = """$ErrorActionPreference='Stop'
try{$p=Start-Process -FilePath '%s' -Verb RunAs -WindowStyle Hidden -Wait -PassThru -ArgumentList @('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-EncodedCommand','%s');exit $p.ExitCode}catch{exit 1}
""" % (ps.replace("'", "''"), encoded)
        result = runner((ps, "-NoProfile", "-NonInteractive", "-Command", launch), 300.0)
        if result.returncode == 11:
            raise RuntimeError("Windows authorized_keys verification did not find the expected public key.")
        if result.returncode == 12:
            raise RuntimeError("Windows authorized_keys ACL verification failed.")
        if result.returncode != 0:
            raise RuntimeError("Windows authorized_keys update failed with exit code %d." % result.returncode)
    else:
        result = runner((ps, "-NoProfile", "-NonInteractive", "-Command", script), 300.0)
        if result.returncode != 0:
            raise RuntimeError("Windows authorized_keys update failed with exit code %d." % result.returncode)
        key_present, acl_safe = _windows_authorized_key_verified(
            target, public_key, runner, administrator_target=False,
        )
        if not key_present:
            raise RuntimeError("Windows authorized_keys verification did not find the expected public key.")
        if not acl_safe:
            raise RuntimeError("Windows authorized_keys ACL verification failed.")
    return True


def _windows_authorized_key_verification_script(
        target: Path, public_key: str, *, administrator_target: bool,
) -> str:
    """Return PowerShell that leaves a validated key/ACL result in `$verification`."""
    key_identity = public_key_identity(public_key).replace("'", "''")
    target_text = str(target).replace("'", "''")
    owner_sid = "S-1-5-32-544" if administrator_target else ""
    script = """$ErrorActionPreference='Stop'
$target='%s'
$needle='%s'
$ownerSid='%s'
if(-not $ownerSid){$ownerSid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value}
$required=@($ownerSid,'S-1-5-18')
$keyPresent=$false
if(Test-Path -LiteralPath $target){
  foreach($existing in (Get-Content -LiteralPath $target -ErrorAction Stop)){
    $parts=$existing.Trim() -split '\\s+'
    if($parts.Length -ge 2 -and ($parts[0]+' '+$parts[1]) -eq $needle){$keyPresent=$true; break}
  }
}
$aclSafe=$false
if(Test-Path -LiteralPath $target){
  $acl=Get-Acl -LiteralPath $target
  $owner=$acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
  $seen=@{}
  $bad=($owner -ne $ownerSid -or -not $acl.AreAccessRulesProtected)
  foreach($rule in $acl.Access){
    if($rule.IsInherited){$bad=$true; continue}
    try{$sid=$rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value}catch{$bad=$true; continue}
    if($rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or $sid -notin $required -or (($rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::FullControl) -ne [Security.AccessControl.FileSystemRights]::FullControl)){$bad=$true; continue}
    $seen[$sid]=$true
  }
  $aclSafe=(-not $bad -and $seen.ContainsKey($required[0]) -and $seen.ContainsKey($required[1]))
}
$verification=[PSCustomObject]@{key_present=$keyPresent;acl_safe=$aclSafe}
""" % (target_text, key_identity, owner_sid)
    return script


def _windows_authorized_key_verified(
        target: Path, public_key: str, runner: CommandRunner, *, administrator_target: bool,
) -> tuple[bool, bool]:
    """Verify a user-owned target after a non-elevated write."""
    ps = str(_trusted_windows_powershell_executable())
    script = _windows_authorized_key_verification_script(
        target, public_key, administrator_target=administrator_target,
    ) + "$verification|ConvertTo-Json -Compress\n"
    completed = runner((ps, "-NoProfile", "-NonInteractive", "-Command", script), 30.0)
    if completed.returncode != 0:
        raise RuntimeError("Windows authorized_keys verification failed.")
    try:
        result = json.loads(completed.stdout or "")
    except ValueError as error:
        raise RuntimeError("Windows authorized_keys verification returned invalid data.") from error
    if not isinstance(result, dict):
        raise RuntimeError("Windows authorized_keys verification returned invalid data.")
    return result.get("key_present") is True, result.get("acl_safe") is True


def install_gateway_public_key(public_key: str, *, system_name: Optional[str] = None, runner: CommandRunner = _run, home: Optional[Path] = None, program_data: Optional[Path] = None) -> AuthorizedKeyResult:
    normalized = validate_public_key(public_key)
    system = (system_name or platform.system()).lower()
    user_home = Path(home) if home is not None else (
        _trusted_windows_profile_directory() if system == "windows" else Path.home()
    )
    root = Path(program_data) if program_data is not None else (
        _trusted_windows_program_data_directory() if system == "windows" else Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
    )
    target, admin_target = authorized_keys_target(
        system_name=system, runner=runner, home=user_home, program_data=root,
    )
    if admin_target:
        changed = _install_windows_key(target, normalized, runner, administrator_target=True)
    elif system == "windows":
        changed = _install_windows_key(target, normalized, runner, administrator_target=False)
    else:
        changed = _install_user_authorized_key(target, normalized)
    return AuthorizedKeyResult(target, public_key_fingerprint(normalized), changed, admin_target, True)

def managed_identity_file(path: Optional[Path] = None) -> Optional[Path]:
    """Return the verified B300 private-key path without reading or exposing its contents."""
    report = inspect_ssh_identity(path)
    return report.private_key if report.ready else None
