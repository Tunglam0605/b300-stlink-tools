"""Stable snapshot handlers for signed CLI update checks and downloads."""

from __future__ import annotations

from pathlib import Path
from threading import Event
from typing import Mapping, Optional

from b300_cli.reporting import emit_snapshot
from b300_core.cli_update import (
    CliUpdateRuntime,
    build_cli_update_runtime,
    default_cli_update_cache,
)
from b300_core.release_manifest import ManifestError, ReleaseAsset, SignatureError
from b300_core.updater import DownloadCancelled, UpdateDownloadError


def _asset_record(asset: Optional[ReleaseAsset]):
    if asset is None:
        return None
    return {
        "filename": asset.filename,
        "size": asset.size,
        "sha256": asset.sha256,
    }


def _result_record(command: str, current_version: str, runtime: CliUpdateRuntime, result):
    return {
        "schema_version": 1,
        "command": command,
        "status": "ok",
        "current_version": current_version,
        "latest_version": str(result.release.version),
        "update_available": result.available,
        "platform": runtime.platform.value,
        "asset": _asset_record(result.asset),
    }


def _format_result(record: Mapping[str, object]) -> str:
    asset = record["asset"]
    lines = [
        "current_version=%s" % record["current_version"],
        "latest_version=%s" % record["latest_version"],
        "update_available=%s" % str(record["update_available"]).lower(),
        "platform=%s" % record["platform"],
    ]
    if isinstance(asset, dict):
        lines.extend((
            "filename=%s" % asset["filename"],
            "size=%s" % asset["size"],
            "sha256=%s" % asset["sha256"],
        ))
    else:
        lines.append("asset=-")
    if "path" in record:
        lines.append("path=%s" % record["path"])
    return "\n".join(lines)


def _emit_failure(args, command: str, reason_code: str, error: Exception) -> int:
    message = str(error)
    emit_snapshot(
        {
            "schema_version": 1,
            "command": command,
            "status": "error",
            "reason_code": reason_code,
            "message": message,
        },
        args.json,
        "%s: %s" % (reason_code, message),
    )
    return 1


def run_update_command(
        args, current_version: str, *, runtime: Optional[CliUpdateRuntime] = None,
        cancel: Optional[Event] = None,
        environ: Optional[Mapping[str, str]] = None,
        home: Optional[Path] = None) -> int:
    action = getattr(args, "update_command", None)
    command = "update" if action is None else "update %s" % action
    if action not in {"check", "download"}:
        return _emit_failure(
            args, "update", "UPDATE_SUBCOMMAND_REQUIRED",
            ValueError("The update command requires check or download."),
        )
    try:
        selected_runtime = runtime or build_cli_update_runtime()
        result = selected_runtime.client.check(current_version)
        record = _result_record(command, current_version, selected_runtime, result)
        if action == "check" or not result.available:
            if action == "download":
                record["downloaded"] = False
                record["path"] = None
            emit_snapshot(record, args.json, _format_result(record))
            return 0

        if result.asset is None:
            raise ManifestError("Available update is missing its signed CLI asset.")
        destination = (
            Path(args.dest) if args.dest is not None else
            default_cli_update_cache(
                selected_runtime.platform, environ=environ, home=home,
            )
        ).expanduser().resolve()
        final_path = selected_runtime.client.download(
            result.asset,
            destination,
            lambda _received, _total: None,
            cancel if cancel is not None else Event(),
        ).resolve()
        record["downloaded"] = True
        record["path"] = str(final_path)
        emit_snapshot(record, args.json, _format_result(record))
        return 0
    except (SignatureError, ManifestError) as error:
        return _emit_failure(args, command, "UPDATE_SECURITY_FAILURE", error)
    except DownloadCancelled as error:
        return _emit_failure(args, command, "UPDATE_CANCELLED", error)
    except UpdateDownloadError as error:
        reason = "UPDATE_CHECK_FAILED" if action == "check" else "UPDATE_DOWNLOAD_FAILED"
        return _emit_failure(args, command, reason, error)
    except (OSError, RuntimeError, ValueError) as error:
        reason = (
            "UNSUPPORTED_UPDATE_PLATFORM"
            if str(error).startswith("Unsupported CLI update platform:")
            else ("UPDATE_CHECK_FAILED" if action == "check" else "UPDATE_DOWNLOAD_FAILED")
        )
        return _emit_failure(args, command, reason, error)
