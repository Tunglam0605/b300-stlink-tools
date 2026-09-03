"""PySide6 desktop interface for safe B300 Application provisioning."""

from b300_version import __version__


# v0.15 keeps the proven v0.14 DebugTab implementation as a compatibility base and
# layers the engineering-workstation integration in DebugTabV15.  Patch the exported
# class once at package import so existing MainWindow/test imports continue to use the
# same module path without a high-risk rewrite of the large legacy tab implementation.
# This affects GUI imports only; b300_core and CLI packages do not import b300_gui.
def _activate_v15_debug_tab() -> None:
    from . import debug_tab as _debug_tab_module
    from .debug_tab_v15 import DebugTabV15

    _debug_tab_module.DebugTab = DebugTabV15


_activate_v15_debug_tab()
