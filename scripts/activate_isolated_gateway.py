#!/usr/bin/env python3
"""Audited two-phase activation for the isolated Ubuntu B300 Gateway.

PREPARE performs host administration while keeping remote Application flash
fail-closed. ACTIVATE flips the root-owned marker only after the existing
boundary verifier proves the USB, mount, service, and hardware-owner boundary.
ROLLBACK is allowed only from a quiescent state and never touches MCU flash.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

try:
    from scripts import install_isolated_gateway as installer
except ImportError:  # packaged tools/ layout
    import install_isolated_gateway as installer


SYSTEM_ROOT = Path("/opt/b300-stlink")
ACTIVE_ROOT = SYSTEM_ROOT / "bin"
RECEIPT_PATH = SYSTEM_ROOT / "ACTIVATION-RECEIPT.json"
MARKER_PATH = Path("/etc/b300-stlink/isolated-gateway.json")
SYSTEM_UNIT_PATH = Path("/etc/systemd/system") / installer.SYSTEM_UNIT
MOUNT_UNIT_PATH = Path("/etc/systemd/system") / installer.MOUNT_UNIT
UDEV_RULE_PATH = Path("/etc/udev/rules.d/99-b300-agent.rules")
SYSTEM_STATE_ROOT = Path("/var/lib/b300-stlink/gateway")
SYSTEM_INGRESS_ROOT = Path("/var/spool/b300-stlink/ingress")
SYSTEM_SOCKET_PATH = Path("/run/b300-stlink/agent.sock")
MAX_RECEIPT_BYTES = 4 * 1024 * 1024
UDEV_RULE = (
    'SUBSYSTEM=="usb", ATTR{idVendor}=="0483", ATTR{idProduct}=="3748", '
    'MODE="0660", GROUP="b300-probe", TAG-="uaccess"\n'
).encode("ascii")
_NAME = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


class ActivationError(RuntimeError):
    def __init__(self, reason_code: str, detail: str = "") -> None:
        super().__init__(detail or reason_code)
        self.reason_code = reason_code
        self.detail = detail or reason_code


def _mapped(root: Path, absolute: Path) -> Path:
    selected = Path(absolute)
    text = selected.as_posix()
    if not text.startswith("/"):
        raise ActivationError("PATH_UNSAFE")
    base = Path(root)
    if base != Path("/"):
        return base / text.lstrip("/")
    return selected


def _run(command: tuple[str, ...], *, timeout: float = 30.0):
    return subprocess.run(command, capture_output=True, text=True,
                          timeout=timeout, check=False)


def _required(command: tuple[str, ...], runner: Callable = _run,
              *, reason: str = "SYSTEM_COMMAND_FAILED", timeout: float = 30.0):
    try:
        result = runner(command, timeout=timeout)
    except TypeError:
        result = runner(command)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ActivationError(reason, "%s: %s" % (reason, command[0])) from error
    if result.returncode != 0:
        detail = (str(result.stderr or result.stdout).strip()[:1000]
                  or "exit=%d" % result.returncode)
        raise ActivationError(reason, detail)
    return result


def _atomic_bytes(path: Path, payload: bytes, *, mode: int,
                  fsync_dir: Callable = installer._fsync_directory) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        raise ActivationError("PATH_UNSAFE")
    fd, name = tempfile.mkstemp(prefix="." + target.name + "-", dir=str(target.parent))
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            if hasattr(os, "fchmod"):
                os.fchmod(stream.fileno(), mode)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if not hasattr(os, "fchmod"):
            os.chmod(temporary, mode)
        os.replace(temporary, target)
        temporary = None
        fsync_dir(target.parent)
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _atomic_json(path: Path, record: dict, *, mode: int = 0o600,
                 fsync_dir: Callable = installer._fsync_directory) -> None:
    payload = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    if len(payload) > MAX_RECEIPT_BYTES:
        raise ActivationError("RECEIPT_TOO_LARGE")
    _atomic_bytes(path, payload, mode=mode, fsync_dir=fsync_dir)


def _read_json(path: Path, *, maximum: int = MAX_RECEIPT_BYTES) -> dict:
    selected = Path(path)
    try:
        info = selected.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_size <= 0 or info.st_size > maximum
                or (os.name == "posix" and stat.S_IMODE(info.st_mode) & 0o077)):
            raise ActivationError("RECEIPT_UNSAFE")
        with selected.open("rb") as stream:
            raw = stream.read(maximum + 1)
        record = json.loads(raw.decode("utf-8"))
        if not isinstance(record, dict):
            raise ValueError("not object")
        return record
    except ActivationError:
        raise
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as error:
        raise ActivationError("RECEIPT_INVALID") from error


def _lookup_identities(operator_name: str) -> dict:
    try:
        import grp
        import pwd
        agent = pwd.getpwnam("b300-agent")
        operator = pwd.getpwnam(operator_name)
        groups = {name: grp.getgrnam(name) for name in
                  ("b300-agent", "b300-probe", "b300-upload", "b300-operator")}
    except (KeyError, OSError) as error:
        raise ActivationError("IDENTITY_INVALID") from error
    return {
        "agent_uid": agent.pw_uid,
        "agent_gid": agent.pw_gid,
        "probe_gid": groups["b300-probe"].gr_gid,
        "upload_gid": groups["b300-upload"].gr_gid,
        "operator_access_gid": groups["b300-operator"].gr_gid,
        "operator_uid": operator.pw_uid,
        "operator_gid": operator.pw_gid,
    }


def _ensure_identities(operator_name: str, runner: Callable = _run) -> dict:
    try:
        import grp
        import pwd
    except ImportError as error:
        raise ActivationError("ROOT_LINUX_REQUIRED") from error
    if _NAME.fullmatch(operator_name) is None:
        raise ActivationError("OPERATOR_INVALID")
    try:
        pwd.getpwnam(operator_name)
    except (KeyError, OSError) as error:
        raise ActivationError("OPERATOR_MISSING") from error
    for group_name in ("b300-agent", "b300-probe", "b300-upload", "b300-operator"):
        try:
            grp.getgrnam(group_name)
        except KeyError:
            _required(("/usr/sbin/groupadd", "--system", group_name), runner,
                      reason="IDENTITY_CREATE_FAILED")
    try:
        pwd.getpwnam("b300-agent")
    except KeyError:
        _required((
            "/usr/sbin/useradd", "--system", "--gid", "b300-agent",
            "--groups", "b300-probe,b300-upload,b300-operator",
            "--home-dir", "/nonexistent", "--no-create-home",
            "--shell", "/usr/sbin/nologin", "b300-agent",
        ), runner, reason="IDENTITY_CREATE_FAILED")
    else:
        _required((
            "/usr/sbin/usermod", "--append",
            "--groups", "b300-probe,b300-upload,b300-operator", "b300-agent",
        ), runner, reason="IDENTITY_UPDATE_FAILED")
    _required((
        "/usr/sbin/usermod", "--append",
        "--groups", "b300-upload,b300-operator", operator_name,
    ), runner, reason="IDENTITY_UPDATE_FAILED")
    identities = _lookup_identities(operator_name)
    if identities["agent_uid"] == identities["operator_uid"]:
        raise ActivationError("IDENTITY_INVALID")
    return identities


def _copy_runtime(candidate: Path, destination: Path,
                  *, fsync_dir: Callable = installer._fsync_directory) -> None:
    source = Path(candidate)
    target = Path(destination)
    if target.exists() or target.is_symlink():
        raise ActivationError("ACTIVE_RUNTIME_OCCUPIED")
    temporary = target.parent / (".bin-" + os.urandom(8).hex())
    if temporary.exists() or temporary.is_symlink():
        raise ActivationError("ACTIVE_RUNTIME_OCCUPIED")
    temporary.mkdir(mode=0o755)
    try:
        for root, directories, files in os.walk(source, topdown=True, followlinks=False):
            root_path = Path(root)
            rel_root = root_path.relative_to(source)
            if rel_root.parts and rel_root.parts[0] == "systemd":
                directories[:] = []
                continue
            directories[:] = [item for item in directories if item != "systemd"]
            out_root = temporary / rel_root
            out_root.mkdir(parents=True, exist_ok=True)
            out_root.chmod(0o755)
            for name in files:
                if name == "STAGE-RECEIPT.json":
                    continue
                src = root_path / name
                info = src.lstat()
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or (os.name == "posix" and stat.S_IMODE(info.st_mode) & 0o022)):
                    raise ActivationError("STAGED_RUNTIME_UNSAFE")
                dst = out_root / name
                with src.open("rb") as input_stream, dst.open("xb") as output_stream:
                    shutil.copyfileobj(input_stream, output_stream, 1024 * 1024)
                    output_stream.flush()
                    os.fsync(output_stream.fileno())
                dst.chmod(0o755 if stat.S_IMODE(info.st_mode) & 0o111 else 0o644)
        fsync_dir(temporary)
        os.rename(temporary, target)
        temporary = None
        fsync_dir(target.parent)
    finally:
        if temporary is not None and temporary.exists():
            shutil.rmtree(temporary)


def _install_regular(source: Path, target: Path, *, mode: int,
                     fsync_dir: Callable = installer._fsync_directory) -> None:
    src = Path(source)
    info = src.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ActivationError("STAGED_RUNTIME_UNSAFE")
    payload = src.read_bytes()
    _atomic_bytes(target, payload, mode=mode, fsync_dir=fsync_dir)


def _marker_record(identities: dict, *, flash_enabled: bool) -> dict:
    return {
        "schema_version": 1,
        "socket_path": SYSTEM_SOCKET_PATH.as_posix(),
        "state_root": SYSTEM_STATE_ROOT.as_posix(),
        "ingress_root": SYSTEM_INGRESS_ROOT.as_posix(),
        "operator_uid": identities["operator_uid"],
        "operator_gid": identities["operator_access_gid"],
        "flash_enabled": bool(flash_enabled),
    }


def _service_command(*args: str) -> tuple[str, ...]:
    return ("/usr/bin/systemctl", *args)


def _legacy_service_command(operator_name: str, *args: str) -> tuple[str, ...]:
    return ("/usr/bin/systemctl", "--user",
            "--machine=%s@.host" % operator_name, *args)


def _prepare(
        plan, bundle: Path, expected_sha256: str, *, host,
        operator_name: str = "aubot", probe_serial: Optional[str] = None,
        root: Path = Path("/"), runner: Callable = _run,
        identities_provider: Optional[Callable[[], dict]] = None,
        stager: Callable = installer.stage_candidate,
        fsync_dir: Callable = installer._fsync_directory,
        effective_uid: Callable = getattr(os, "geteuid", lambda: -1),
        system_name: str = sys.platform) -> dict:
    if system_name != "linux" or effective_uid() != 0:
        raise ActivationError("ROOT_LINUX_REQUIRED")
    if not getattr(plan, "ready", False):
        raise ActivationError("PLAN_NOT_READY")
    selected = Path(bundle)
    digest = str(expected_sha256).lower()
    if (plan.candidate.get("path") != str(selected)
            or plan.candidate.get("sha256") != digest):
        raise ActivationError("PLAN_NOT_FRESH")
    identities = (identities_provider() if identities_provider is not None
                  else _ensure_identities(operator_name, runner))
    required = ("agent_uid", "agent_gid", "probe_gid", "upload_gid",
                "operator_access_gid", "operator_uid", "operator_gid")
    if any(type(identities.get(key)) is not int or identities[key] < 0 for key in required):
        raise ActivationError("IDENTITY_INVALID")
    install_root = _mapped(root, SYSTEM_ROOT)
    install_root.mkdir(parents=True, exist_ok=True)
    stage = stager(
        plan, selected, digest, host=host, install_root=install_root,
        trust_root=Path("/") if Path(root) == Path("/") else Path(root),
        trusted_uid=0, probe_serial=probe_serial,
    )
    candidate = Path(stage["candidate_dir"])
    active_root = _mapped(root, ACTIVE_ROOT)
    marker = _mapped(root, MARKER_PATH)

    receipt = {
        "schema_version": 1,
        "status": "PREPARING",
        "version": plan.candidate.get("version"),
        "bundle_sha256": digest,
        "candidate_dir": str(candidate),
        "active_root": ACTIVE_ROOT.as_posix(),
        "operator_name": operator_name,
        "operator_uid": identities["operator_uid"],
        "operator_access_gid": identities["operator_access_gid"],
        "probe_serial": probe_serial,
        "rollback_inventory": plan.rollback_inventory,
        "prepared_at": time.time(),
    }
    receipt_path = _mapped(root, RECEIPT_PATH)
    _atomic_json(receipt_path, receipt, fsync_dir=fsync_dir)

    # The rollback inventory is durable before the first active-system target
    # is installed. A crash after this point is recoverable without guessing.
    _copy_runtime(candidate, active_root, fsync_dir=fsync_dir)
    systemd = candidate / "systemd"
    _install_regular(systemd / installer.SYSTEM_UNIT,
                     _mapped(root, SYSTEM_UNIT_PATH), mode=0o644, fsync_dir=fsync_dir)
    _install_regular(systemd / "b300-stlink-ingress.mount.rendered",
                     _mapped(root, MOUNT_UNIT_PATH), mode=0o644, fsync_dir=fsync_dir)
    _atomic_bytes(_mapped(root, UDEV_RULE_PATH), UDEV_RULE,
                  mode=0o644, fsync_dir=fsync_dir)

    legacy = plan.rollback_inventory.get("services", {}).get("legacy_user", {})
    _required(_legacy_service_command(operator_name, "stop", installer.SYSTEM_UNIT),
              runner, reason="LEGACY_SERVICE_STOP_FAILED")
    if legacy.get("enabled") is True:
        _required(_legacy_service_command(operator_name, "disable", installer.SYSTEM_UNIT),
                  runner, reason="LEGACY_SERVICE_DISABLE_FAILED")

    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.parent.chmod(0o755)
    _atomic_json(marker, _marker_record(identities, flash_enabled=False),
                 mode=0o600, fsync_dir=fsync_dir)

    for directory in (
        _mapped(root, Path("/var/lib/b300-stlink")),
        _mapped(root, Path("/var/spool/b300-stlink")),
        _mapped(root, SYSTEM_INGRESS_ROOT),
    ):
        directory.mkdir(parents=True, exist_ok=True)
    _required(_service_command("daemon-reload"), runner)
    _required(_service_command("enable", "--now", installer.MOUNT_UNIT),
              runner, reason="INGRESS_MOUNT_START_FAILED")
    _required(_service_command("enable", "--now", installer.SYSTEM_UNIT),
              runner, reason="SYSTEM_AGENT_START_FAILED")
    _required(("/usr/bin/udevadm", "control", "--reload-rules"), runner,
              reason="UDEV_RELOAD_FAILED")
    _required(("/usr/bin/udevadm", "trigger", "--subsystem-match=usb",
               "--attr-match=idVendor=0483", "--attr-match=idProduct=3748"),
              runner, reason="UDEV_TRIGGER_FAILED")
    _required(("/usr/bin/udevadm", "settle", "--timeout=5"), runner,
              reason="UDEV_SETTLE_FAILED")

    receipt["status"] = "PREPARED_PENDING_BOUNDARY"
    receipt["prepared_at"] = time.time()
    _atomic_json(receipt_path, receipt, fsync_dir=fsync_dir)
    return {
        "state": "PREPARED_PENDING_BOUNDARY",
        "flash_enabled": False,
        "candidate_dir": str(candidate),
        "receipt": str(receipt_path),
        "next_action": "Replug ST-Link if required, then run isolated Gateway activate.",
    }


class _ProductionProbes:
    def __init__(self, *, runner: Callable = _run,
                 state_root: Path = SYSTEM_STATE_ROOT) -> None:
        self.runner = runner
        self.state_root = Path(state_root)

    def _status(self) -> Optional[dict]:
        path = self.state_root / "agent-status.json"
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
                return None
            record = json.loads(path.read_text(encoding="utf-8"))
            return record if isinstance(record, dict) else None
        except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
            return None

    def service_idle(self) -> bool:
        try:
            active = _required(_service_command("is-active", installer.SYSTEM_UNIT),
                               self.runner, reason="SYSTEM_AGENT_NOT_ACTIVE")
        except ActivationError:
            return False
        status = self._status()
        return active.stdout.strip() == "active" and status is not None and status.get("state") == "IDLE"

    def agent_idle(self) -> bool:
        status = self._status()
        return status is not None and status.get("state") == "IDLE"

    def jobs_idle(self) -> bool:
        counts = installer.inspect_job_states(self.state_root / "program-jobs")
        return all(state in installer.TERMINAL_JOBS for state in counts)

    def openocd_quiescent(self) -> bool:
        return installer._openocd_quiescent()


def _wait_isolated_capability(state_root: Path, *, timeout: float = 8.0,
                              sleep: Callable[[float], None] = time.sleep,
                              clock: Callable[[], float] = time.monotonic) -> dict:
    deadline = clock() + timeout
    path = Path(state_root) / "agent-status.json"
    last = None
    while clock() < deadline:
        try:
            last = json.loads(path.read_text(encoding="utf-8"))
            if (isinstance(last, dict)
                    and last.get("state") == "IDLE"
                    and "remote_application_flash_isolated_v1" in last.get("capabilities", [])):
                return last
        except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
            pass
        sleep(min(0.2, max(0.0, deadline - clock())))
    raise ActivationError("ISOLATED_CAPABILITY_NOT_READY",
                          json.dumps(last, sort_keys=True)[:1000] if isinstance(last, dict) else "")


def _activate(*, probe_serial: Optional[str] = None,
              probes=None, runner: Callable = _run,
              boundary_verifier: Callable = installer.verify_boundary,
              receipt_path: Path = RECEIPT_PATH,
              state_root: Path = SYSTEM_STATE_ROOT,
              fsync_dir: Callable = installer._fsync_directory,
              effective_uid: Callable = getattr(os, "geteuid", lambda: -1),
              system_name: str = sys.platform) -> dict:
    if system_name != "linux" or effective_uid() != 0:
        raise ActivationError("ROOT_LINUX_REQUIRED")
    receipt = _read_json(receipt_path)
    if receipt.get("status") not in {"PREPARED_PENDING_BOUNDARY", "ACTIVE"}:
        raise ActivationError("PREPARE_REQUIRED")
    selected_probes = probes or _ProductionProbes(runner=runner, state_root=state_root)
    result = boundary_verifier(
        probe_serial=probe_serial if probe_serial is not None else receipt.get("probe_serial"),
        probes=selected_probes,
    )
    status = _wait_isolated_capability(state_root)
    receipt["status"] = "ACTIVE"
    receipt["activated_at"] = time.time()
    _atomic_json(receipt_path, receipt, fsync_dir=fsync_dir)
    return {
        "state": "ACTIVE",
        "flash_enabled": True,
        "boundary": result,
        "capabilities": status.get("capabilities", []),
    }


def _remove_tree(path: Path) -> None:
    target = Path(path)
    try:
        info = target.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode):
        raise ActivationError("PATH_UNSAFE")
    if stat.S_ISDIR(info.st_mode):
        shutil.rmtree(target)
    elif stat.S_ISREG(info.st_mode):
        target.unlink()
    else:
        raise ActivationError("PATH_UNSAFE")


def _rollback(*, runner: Callable = _run, probes=None,
              receipt_path: Path = RECEIPT_PATH, root: Path = Path("/"),
              fsync_dir: Callable = installer._fsync_directory,
              effective_uid: Callable = getattr(os, "geteuid", lambda: -1),
              system_name: str = sys.platform) -> dict:
    if system_name != "linux" or effective_uid() != 0:
        raise ActivationError("ROOT_LINUX_REQUIRED")
    receipt = _read_json(receipt_path)
    operator = receipt.get("operator_name")
    if not isinstance(operator, str) or _NAME.fullmatch(operator) is None:
        raise ActivationError("RECEIPT_INVALID")
    selected_probes = probes or _ProductionProbes(runner=runner)
    marker = _mapped(root, MARKER_PATH)
    if marker.exists():
        try:
            record = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as error:
            raise ActivationError("MARKER_INVALID") from error
        if record.get("flash_enabled") is True:
            try:
                installer.transition_active_to_pending(probes=selected_probes)
            except Exception as error:
                raise ActivationError(getattr(error, "reason_code", "ROLLBACK_NOT_QUIESCENT")) from error
    if not (selected_probes.agent_idle() and selected_probes.jobs_idle()
            and selected_probes.openocd_quiescent()):
        raise ActivationError("ROLLBACK_NOT_QUIESCENT")

    for unit in (installer.SYSTEM_UNIT, installer.MOUNT_UNIT):
        _required(_service_command("disable", "--now", unit), runner,
                  reason="ROLLBACK_SERVICE_STOP_FAILED")
    for path in (marker, _mapped(root, UDEV_RULE_PATH),
                 _mapped(root, SYSTEM_UNIT_PATH), _mapped(root, MOUNT_UNIT_PATH)):
        try:
            Path(path).unlink()
        except FileNotFoundError:
            pass
    _remove_tree(_mapped(root, ACTIVE_ROOT))
    _required(_service_command("daemon-reload"), runner)
    _required(("/usr/bin/udevadm", "control", "--reload-rules"), runner)
    _required(("/usr/bin/udevadm", "trigger", "--subsystem-match=usb",
               "--attr-match=idVendor=0483", "--attr-match=idProduct=3748"), runner)
    _required(("/usr/bin/udevadm", "settle", "--timeout=5"), runner)

    legacy = receipt.get("rollback_inventory", {}).get("services", {}).get("legacy_user", {})
    if legacy.get("enabled") is True:
        _required(_legacy_service_command(operator, "enable", installer.SYSTEM_UNIT), runner,
                  reason="LEGACY_SERVICE_RESTORE_FAILED")
    if legacy.get("active") is True:
        _required(_legacy_service_command(operator, "start", installer.SYSTEM_UNIT), runner,
                  reason="LEGACY_SERVICE_RESTORE_FAILED")
    receipt["status"] = "ROLLED_BACK"
    receipt["rolled_back_at"] = time.time()
    _atomic_json(receipt_path, receipt, fsync_dir=fsync_dir)
    return {"state": "ROLLED_BACK", "flash_enabled": False,
            "preserved_state_root": SYSTEM_STATE_ROOT.as_posix()}


def _require_confirmation(args) -> None:
    if not getattr(args, "confirm_system_change", False):
        raise ActivationError("CONFIRM_SYSTEM_CHANGE_REQUIRED")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)

    prepare = sub.add_parser("prepare", help="install isolated Gateway in fail-closed pending mode")
    prepare.add_argument("--bundle", required=True, type=Path)
    prepare.add_argument("--expected-sha256", required=True)
    prepare.add_argument("--operator", default="aubot")
    prepare.add_argument("--probe-serial")
    prepare.add_argument("--confirm-system-change", action="store_true")
    prepare.add_argument("--json", action="store_true")

    activate = sub.add_parser("activate", help="verify USB boundary and enable remote Application flash")
    activate.add_argument("--probe-serial")
    activate.add_argument("--confirm-system-change", action="store_true")
    activate.add_argument("--json", action="store_true")

    rollback = sub.add_parser("rollback", help="disable isolated mode and restore legacy user service")
    rollback.add_argument("--confirm-system-change", action="store_true")
    rollback.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    try:
        _require_confirmation(args)
        if args.action == "prepare":
            digest = args.expected_sha256.lower()
            host = installer.LinuxHostProbe(operator_name=args.operator)
            plan = installer.build_plan(args.bundle, digest, host=host,
                                        probe_serial=args.probe_serial)
            if not plan.ready:
                raise ActivationError("PLAN_NOT_READY",
                                      ",".join(item["code"] for item in plan.blockers))
            result = _prepare(plan, args.bundle, digest, host=host,
                              operator_name=args.operator, probe_serial=args.probe_serial)
        elif args.action == "activate":
            result = _activate(probe_serial=args.probe_serial)
        else:
            result = _rollback()
        print(json.dumps({"status": "ok", **result}, sort_keys=True))
        return 0
    except (ActivationError, installer.StageError, installer.TransitionError,
            installer.BoundaryError) as error:
        record = {"status": "error",
                  "reason_code": getattr(error, "reason_code", type(error).__name__),
                  "message": str(error)}
        print(json.dumps(record, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
