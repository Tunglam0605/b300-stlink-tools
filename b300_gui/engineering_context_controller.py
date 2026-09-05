"""Bind shared frontend selection to the established production operations."""
from .app_context import AppContext
from .widgets.shared_context_bar import SharedContextBar


class EngineeringContextController:
    def __init__(self, window):
        self.window = window
        self.context = AppContext(window._gateway_sessions, window)
        self._selection = None
        self._updating = False
        self.bar = SharedContextBar(self.context, window)
        self.bar.manage_projects_requested.connect(window._open_project_manager)
        self.bar.manage_connections_requested.connect(window._open_gateway_manager)
        self.bar.refresh_probes_requested.connect(self.refresh_probes)

    def bind(self):
        self.context.changed.connect(self.changed)
        self.window.probe_combo.currentIndexChanged.connect(self.local_probe_changed)

    def refresh_probes(self):
        if self.context.selected_connection.is_local:
            self.window.refresh_probes()
        else:
            self.window.append_log('Remote probe discovery chưa được tích hợp trong UI này; dùng DEBUG VS CODE để kiểm tra SSH.')
            self.bar.render()

    def local_probe_changed(self, *_):
        if self.context.selected_connection.is_local:
            self.context.set_probes(self.window._probes, self.window.probe_combo.currentData())

    def changed(self):
        if self._updating:
            return
        self._updating = True
        try:
            window, context = self.window, self.context
            project = context.selected_project
            connection = context.selected_connection
            current = (project, connection, context.selected_probe)
            old = self._selection
            self._selection = current
            if old != current:
                window._invalidate_target()
                if connection.is_local:
                    if old is None or old[1] != connection:
                        context.set_probes(window._probes, window.probe_combo.currentData())
                    elif context.selected_probe != window.probe_combo.currentData():
                        window.probe_combo.setCurrentIndex(window.probe_combo.findData(context.selected_probe))
                if old is None or old[0] != project:
                    window.program_view.clear_project_file()
                    if project and project.application_hex:
                        window.program_view.set_file_path(project.application_hex)
                self._selection = (project, connection, context.selected_probe)
                window.device_view.set_probes(context.probes)
                window.debug_vscode_view.set_probes(context.probes)
            if old != current:
                window._update_controls()
            if not connection.is_local:
                window.program_view.banner.show_info('PROGRAM qua Gateway chưa được hỗ trợ',
                    'Chọn ST-Link cục bộ để nạp; GIÁM SÁT và GỠ LỖI VS CODE dùng kết nối SSH đã chọn.')
            self.bar.render()
        finally:
            self._updating = False
