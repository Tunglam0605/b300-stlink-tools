"""Saved workspace + ELF/AXF profiles shared by Monitor and Debug."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from .remote_profile import default_remote_profile_path

_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")


def default_project_profiles_path() -> Path:
    return default_remote_profile_path().with_name("debug_projects.json")


def _project_id(name: str, workspace: Path) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", str(name)).strip("-._") or "project"
    digest = hashlib.sha256(str(workspace).encode("utf-8")).hexdigest()[:8]
    return (base[:40] + "-" + digest).lower()


@dataclass(frozen=True)
class ProjectProfile:
    project_id: str
    name: str
    workspace: Path
    symbols: Path

    def validate(self, *, require_exists: bool = False) -> "ProjectProfile":
        project_id = str(self.project_id).strip()
        if not project_id or not _SAFE_ID.fullmatch(project_id):
            raise ValueError("Project profile id contains unsupported characters.")
        name = str(self.name).strip()
        if not name or len(name) > 100:
            raise ValueError("Project profile name must contain 1..100 characters.")
        workspace = Path(self.workspace).expanduser()
        symbols = Path(self.symbols).expanduser()
        if symbols.suffix.lower() not in {".elf", ".axf"}:
            raise ValueError("Project symbols must be an ELF/AXF file.")
        if require_exists:
            workspace = workspace.resolve(); symbols = symbols.resolve()
            if not workspace.is_dir(): raise ValueError("Project workspace directory does not exist: %s" % workspace)
            if not symbols.is_file(): raise ValueError("Project ELF/AXF file does not exist: %s" % symbols)
        return ProjectProfile(project_id, name, workspace, symbols)

    def record(self) -> dict:
        item = self.validate()
        return {"id": item.project_id, "name": item.name, "workspace": str(item.workspace), "symbols": str(item.symbols)}

    @classmethod
    def create(cls, name: str, workspace: Path, symbols: Path, *, project_id: Optional[str] = None,
               require_exists: bool = True) -> "ProjectProfile":
        work=Path(workspace).expanduser()
        return cls(project_id or _project_id(name, work), name, work, Path(symbols).expanduser()).validate(require_exists=require_exists)


class ProjectProfileStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path=Path(path or default_project_profiles_path()).expanduser()

    def _read(self):
        if not self.path.is_file(): return (), None
        try: raw=json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError,UnicodeError,json.JSONDecodeError) as error:
            raise RuntimeError("B300 project profile store is unreadable/corrupt: %s" % self.path) from error
        if not isinstance(raw,dict) or set(raw)!={"schema_version","default_id","projects"}:
            raise RuntimeError("B300 project profile store schema is invalid.")
        if raw.get("schema_version")!=1 or not isinstance(raw.get("projects"),list):
            raise RuntimeError("Unsupported B300 project profile store schema.")
        items=[]; seen=set()
        for record in raw["projects"]:
            if not isinstance(record,dict) or set(record)!={"id","name","workspace","symbols"}:
                raise RuntimeError("B300 project profile entry schema is invalid.")
            try:
                item=ProjectProfile(str(record["id"]),str(record["name"]),Path(str(record["workspace"])),Path(str(record["symbols"]))).validate()
            except ValueError as error:
                raise RuntimeError("B300 project profile entry contains invalid values.") from error
            if item.project_id in seen: raise RuntimeError("B300 project profile ids must be unique.")
            seen.add(item.project_id); items.append(item)
        default_id=raw.get("default_id")
        if default_id is not None:
            default_id=str(default_id)
            if default_id not in seen: raise RuntimeError("B300 default project profile does not exist.")
        return tuple(items),default_id

    def _write(self,projects:Iterable[ProjectProfile],default_id:Optional[str])->None:
        items=tuple(item.validate() for item in projects); ids={item.project_id for item in items}
        if default_id is not None and default_id not in ids: raise ValueError("Default project profile must exist before it can be selected.")
        payload={"schema_version":1,"default_id":default_id,"projects":[item.record() for item in items]}
        self.path.parent.mkdir(parents=True,exist_ok=True)
        fd,temp_name=tempfile.mkstemp(prefix=self.path.name+".",suffix=".tmp",dir=str(self.path.parent)); temp=Path(temp_name)
        try:
            with os.fdopen(fd,"w",encoding="utf-8",newline="\n") as handle:
                json.dump(payload,handle,indent=2,sort_keys=True); handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
            if os.name!="nt": os.chmod(str(temp),0o600)
            os.replace(str(temp),str(self.path))
            if os.name!="nt": os.chmod(str(self.path),0o600)
        finally:
            try: temp.unlink()
            except OSError: pass

    def list(self): return self._read()[0]
    def default_id(self): return self._read()[1]
    def default(self):
        items,default_id=self._read(); return next((item for item in items if item.project_id==default_id),None)
    def get(self,project_id:str): return next((item for item in self.list() if item.project_id==str(project_id)),None)
    def upsert(self,project:ProjectProfile,*,make_default:bool=False)->ProjectProfile:
        selected=project.validate(require_exists=True); items,default_id=self._read(); updated=[]; replaced=False
        for item in items:
            if item.project_id==selected.project_id: updated.append(selected); replaced=True
            else: updated.append(item)
        if not replaced: updated.append(selected)
        if make_default or default_id is None: default_id=selected.project_id
        self._write(updated,default_id); return selected
    def delete(self,project_id:str)->bool:
        items,default_id=self._read(); remaining=[item for item in items if item.project_id!=str(project_id)]
        if len(remaining)==len(items): return False
        if default_id==str(project_id): default_id=remaining[0].project_id if remaining else None
        self._write(remaining,default_id); return True
    def set_default(self,project_id:str)->ProjectProfile:
        items,_=self._read(); selected=next((item for item in items if item.project_id==str(project_id)),None)
        if selected is None: raise KeyError("Unknown project profile: %s" % project_id)
        self._write(items,selected.project_id); return selected


__all__=["ProjectProfile","ProjectProfileStore","default_project_profiles_path"]
