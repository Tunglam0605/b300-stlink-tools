"""Process-lifetime SSH sessions shared by B300 remote workspaces."""
from __future__ import annotations

import threading
from typing import Dict, Optional

from .remote_profile import RemoteGatewayProfile
from .remote_session import CredentialStore, RemoteSession, RemoteSessionState


class MemoryCredentialStore:
    """Remember SSH passwords only for the lifetime of the running B300 process."""
    def __init__(self)->None:
        self._values:Dict[str,str]={}; self._lock=threading.RLock()
    @staticmethod
    def _key(profile:RemoteGatewayProfile)->str:
        item=profile.validate(); return "%s@%s:%d"%(item.user,item.host,item.port)
    def load(self,profile:RemoteGatewayProfile)->Optional[str]:
        with self._lock: return self._values.get(self._key(profile))
    def save(self,profile:RemoteGatewayProfile,password:str)->None:
        secret=str(password)
        if not secret: raise ValueError("SSH password cannot be empty.")
        with self._lock: self._values[self._key(profile)]=secret
    def clear(self,profile:RemoteGatewayProfile)->bool:
        with self._lock: return self._values.pop(self._key(profile),None) is not None
    def clear_all(self)->None:
        with self._lock: self._values.clear()


class GatewaySessionManager:
    """Reuse authenticated RemoteSession objects and never persist their passwords."""
    def __init__(self,*,credential_store:Optional[CredentialStore]=None,session_factory=RemoteSession)->None:
        self.credential_store=credential_store or MemoryCredentialStore(); self._session_factory=session_factory
        self._sessions:Dict[str,RemoteSession]={}; self._lock=threading.RLock()
    @staticmethod
    def _key(profile:RemoteGatewayProfile)->str:
        item=profile.validate(); return "%s@%s:%d"%(item.user,item.host,item.port)
    def session(self,profile:RemoteGatewayProfile)->RemoteSession:
        selected=profile.validate(); key=self._key(selected)
        with self._lock:
            current=self._sessions.get(key)
            if current is None:
                current=self._session_factory(selected,credential_store=self.credential_store); self._sessions[key]=current
            return current
    def connected(self,profile:RemoteGatewayProfile)->bool:
        with self._lock: current=self._sessions.get(self._key(profile))
        return bool(current is not None and current.connected)
    def has_cached_password(self,profile:RemoteGatewayProfile)->bool:
        try: return bool(self.credential_store.load(profile))
        except Exception: return False
    def connect(self,profile:RemoteGatewayProfile,password:Optional[str]=None,*,timeout_seconds:float=12.0)->RemoteSessionState:
        return self.session(profile).ensure_connected(password=password,remember=True,timeout_seconds=timeout_seconds)
    def disconnect(self,profile:RemoteGatewayProfile,*,forget_for_session:bool=False)->bool:
        key=self._key(profile)
        with self._lock: current=self._sessions.get(key)
        if current is None:
            if forget_for_session: self.credential_store.clear(profile)
            return False
        current.disconnect(forget_password=forget_for_session); return True
    def disconnect_all(self)->None:
        with self._lock: sessions=tuple(self._sessions.values()); self._sessions.clear()
        for current in sessions:
            try: current.disconnect()
            except Exception: pass
        if hasattr(self.credential_store,"clear_all"): self.credential_store.clear_all()


__all__=["GatewaySessionManager","MemoryCredentialStore"]
