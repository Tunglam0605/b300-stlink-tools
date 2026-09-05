"""Shared GUI selections and evidence; no persistence or hardware operations."""
from dataclasses import dataclass
from typing import Optional
from PySide6.QtCore import QObject, Signal
from b300_core.gateway_profiles import GatewayProfile
from b300_core.models import TargetInfo
from b300_core.project_profiles import ProjectProfile


@dataclass(frozen=True)
class ConnectionChoice:
    connection_id: str
    name: str
    gateway: Optional[GatewayProfile] = None

    @property
    def is_local(self) -> bool:
        return self.gateway is None


class AppContext(QObject):
    changed = Signal()

    def __init__(self, gateway_sessions=None, parent=None):
        super().__init__(parent)
        self.gateway_sessions = gateway_sessions
        self.selected_project: Optional[ProjectProfile] = None
        self.selected_connection = ConnectionChoice('local', 'ST-Link cục bộ')
        self.selected_probe: Optional[str] = None
        self.target_info: Optional[TargetInfo] = None
        self.hardware_busy = False
        self.project_profiles = ()
        self.connections = (self.selected_connection,)
        self.probes = ()
        self._profiles_loaded = False

    def set_profiles(self, projects, gateways, default_project_id=None, default_gateway_id=None):
        if self.hardware_busy:
            return False
        projects = tuple(projects)
        connections = (ConnectionChoice('local', 'ST-Link cục bộ'),) + tuple(
            ConnectionChoice('gateway:local' if item.profile_id == 'local' else item.profile_id, item.name, item) for item in gateways)
        project_id = self.selected_project.project_id if self.selected_project else default_project_id
        connection_id = self.selected_connection.connection_id if self._profiles_loaded else ('gateway:local' if default_gateway_id == 'local' else default_gateway_id or 'local')
        project = next((item for item in projects if item.project_id == project_id), None)
        if project is None:
            project = next((item for item in projects if item.project_id == default_project_id), projects[0] if projects else None)
        connection = next((item for item in connections if item.connection_id == connection_id), connections[0])
        if connection != self.selected_connection:
            self.selected_probe = None
            self.probes = ()
            self.target_info = None
        self.project_profiles, self.connections = projects, connections
        self.selected_project, self.selected_connection = project, connection
        self._profiles_loaded = True
        self.changed.emit()

    def select_project(self, project_id):
        if self.hardware_busy:
            return False
        selected = next((item for item in self.project_profiles if item.project_id == project_id), None)
        if selected is None:
            return False
        if selected != self.selected_project:
            self.selected_project = selected
            self.changed.emit()
        return True

    def select_connection(self, connection_id):
        if self.hardware_busy:
            return False
        selected = next((item for item in self.connections if item.connection_id == connection_id), None)
        if selected is None:
            return False
        if selected != self.selected_connection:
            self.selected_connection = selected
            self.selected_probe = None
            self.probes = ()
            self.target_info = None
            self.changed.emit()
        return True

    def select_probe(self, serial):
        if self.hardware_busy:
            return False
        if serial is not None and not any(item.serial == serial for item in self.probes):
            return False
        if serial != self.selected_probe:
            self.selected_probe = serial
            self.target_info = None
            self.changed.emit()
        return True

    def set_probes(self, probes, selected_serial=None):
        probes = tuple(probes)
        serial = selected_serial if any(item.serial == selected_serial for item in probes) else None
        if serial != self.selected_probe or probes != self.probes:
            self.target_info = None
        self.probes = probes
        self.selected_probe = serial
        self.changed.emit()

    def set_target_info(self, info):
        if info != self.target_info:
            self.target_info = info
            self.changed.emit()

    def set_hardware_busy(self, busy):
        if bool(busy) != self.hardware_busy:
            self.hardware_busy = bool(busy)
            self.changed.emit()
