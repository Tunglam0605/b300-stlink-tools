"""Named non-secret Gateway profiles shared by B300 GUI workspaces."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from .remote_profile import (
    RemoteGatewayProfile, clear_remote_profile, default_remote_profile_path,
    load_remote_profile, save_remote_profile,
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")


def default_gateway_profiles_path() -> Path:
    return default_remote_profile_path().with_name("gateway_profiles.json")


def _profile_id_for(endpoint: RemoteGatewayProfile) -> str:
    selected = endpoint.validate()
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", selected.host).strip("-._") or "gateway"
    digest = hashlib.sha256(("%s@%s:%d" % (selected.user, selected.host, selected.port)).encode("utf-8")).hexdigest()[:8]
    return (base[:40] + "-" + digest).lower()


@dataclass(frozen=True)
class GatewayProfile:
    profile_id: str
    name: str
    endpoint: RemoteGatewayProfile

    def validate(self) -> "GatewayProfile":
        profile_id = str(self.profile_id).strip()
        if not profile_id or not _SAFE_ID.fullmatch(profile_id):
            raise ValueError("Gateway profile id contains unsupported characters.")
        name = str(self.name).strip()
        if not name or len(name) > 80:
            raise ValueError("Gateway profile name must contain 1..80 characters.")
        return GatewayProfile(profile_id, name, self.endpoint.validate())

    def record(self) -> dict:
        item = self.validate()
        return {"id": item.profile_id, "name": item.name, "host": item.endpoint.host,
                "user": item.endpoint.user, "port": item.endpoint.port}

    @property
    def display_endpoint(self) -> str:
        selected = self.endpoint.validate()
        return "%s@%s:%d" % (selected.user, selected.host, selected.port)

    @classmethod
    def create(cls, name: str, host: str, user: str, port: int = 22,
               *, profile_id: Optional[str] = None) -> "GatewayProfile":
        endpoint = RemoteGatewayProfile(host, user, port).validate()
        return cls(profile_id or _profile_id_for(endpoint), name, endpoint).validate()


class GatewayProfileStore:
    """Atomic multi-profile store with migration/sync for the v0.18 single profile."""

    def __init__(self, path: Optional[Path] = None, *, legacy_path: Optional[Path] = None) -> None:
        self.path = Path(path or default_gateway_profiles_path()).expanduser()
        self.legacy_path = Path(legacy_path or default_remote_profile_path()).expanduser()

    @staticmethod
    def _empty() -> dict:
        return {"schema_version": 1, "default_id": None, "profiles": []}

    def _read_raw(self) -> dict:
        if not self.path.is_file():
            legacy = load_remote_profile(self.legacy_path)
            if legacy is None:
                return self._empty()
            migrated = GatewayProfile.create("Default Gateway", legacy.host, legacy.user, legacy.port)
            payload = {"schema_version": 1, "default_id": migrated.profile_id, "profiles": [migrated.record()]}
            self._write_raw(payload)
            return payload
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise RuntimeError("B300 Gateway profile store is unreadable/corrupt: %s" % self.path) from error
        if not isinstance(raw, dict) or set(raw) != {"schema_version", "default_id", "profiles"}:
            raise RuntimeError("B300 Gateway profile store schema is invalid: %s" % self.path)
        if raw.get("schema_version") != 1 or not isinstance(raw.get("profiles"), list):
            raise RuntimeError("Unsupported B300 Gateway profile store schema.")
        return raw

    def _decode(self, raw: dict):
        items = []
        seen = set()
        for record in raw["profiles"]:
            if not isinstance(record, dict) or set(record) != {"id", "name", "host", "user", "port"}:
                raise RuntimeError("B300 Gateway profile entry schema is invalid.")
            try:
                item = GatewayProfile(str(record["id"]), str(record["name"]),
                    RemoteGatewayProfile(str(record["host"]), str(record["user"]), int(record["port"]))).validate()
            except (TypeError, ValueError) as error:
                raise RuntimeError("B300 Gateway profile entry contains invalid values.") from error
            if item.profile_id in seen:
                raise RuntimeError("B300 Gateway profile ids must be unique.")
            seen.add(item.profile_id)
            items.append(item)
        default_id = raw.get("default_id")
        if default_id is not None:
            default_id = str(default_id)
            if default_id not in seen:
                raise RuntimeError("B300 Gateway default profile does not exist.")
        return tuple(items), default_id

    def _write_raw(self, payload: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=str(self.path.parent))
        temp = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush(); os.fsync(handle.fileno())
            if os.name != "nt": os.chmod(str(temp), 0o600)
            os.replace(str(temp), str(self.path))
            if os.name != "nt": os.chmod(str(self.path), 0o600)
        finally:
            try: temp.unlink()
            except OSError: pass

    def _write(self, profiles: Iterable[GatewayProfile], default_id: Optional[str]) -> None:
        items = tuple(item.validate() for item in profiles)
        ids = {item.profile_id for item in items}
        if default_id is not None and default_id not in ids:
            raise ValueError("Default Gateway profile must exist before it can be selected.")
        self._write_raw({"schema_version": 1, "default_id": default_id, "profiles": [item.record() for item in items]})
        self._sync_legacy(items, default_id)

    def _sync_legacy(self, profiles, default_id: Optional[str]) -> None:
        if default_id is None:
            clear_remote_profile(self.legacy_path)
            return
        selected = next(item for item in profiles if item.profile_id == default_id)
        save_remote_profile(selected.endpoint, self.legacy_path)

    def list(self):
        return self._decode(self._read_raw())[0]

    def default_id(self) -> Optional[str]:
        return self._decode(self._read_raw())[1]

    def default(self) -> Optional[GatewayProfile]:
        items, default_id = self._decode(self._read_raw())
        return next((item for item in items if item.profile_id == default_id), None)

    def get(self, profile_id: str) -> Optional[GatewayProfile]:
        return next((item for item in self.list() if item.profile_id == str(profile_id)), None)

    def upsert(self, profile: GatewayProfile, *, make_default: bool = False) -> GatewayProfile:
        selected = profile.validate()
        items, default_id = self._decode(self._read_raw())
        updated=[]; replaced=False
        for item in items:
            if item.profile_id == selected.profile_id:
                updated.append(selected); replaced=True
            else: updated.append(item)
        if not replaced: updated.append(selected)
        if make_default or default_id is None: default_id = selected.profile_id
        self._write(updated, default_id)
        return selected

    def delete(self, profile_id: str) -> bool:
        items, default_id = self._decode(self._read_raw())
        remaining=[item for item in items if item.profile_id != str(profile_id)]
        if len(remaining)==len(items): return False
        if default_id == str(profile_id): default_id = remaining[0].profile_id if remaining else None
        self._write(remaining, default_id)
        return True

    def set_default(self, profile_id: str) -> GatewayProfile:
        items, _ = self._decode(self._read_raw())
        selected=next((item for item in items if item.profile_id==str(profile_id)),None)
        if selected is None: raise KeyError("Unknown Gateway profile: %s" % profile_id)
        self._write(items, selected.profile_id)
        return selected


__all__ = ["GatewayProfile", "GatewayProfileStore", "default_gateway_profiles_path"]
