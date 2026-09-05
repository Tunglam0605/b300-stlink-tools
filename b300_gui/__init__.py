"""PySide6 desktop interface for safe B300 Application provisioning."""

from b300_version import __version__


def _preserve_debug_tab_compatibility() -> None:
    # Package-level callers historically import b300_gui.debug_tab.DebugTab directly.
    # COMPAT import contract only. The executable uses MainWindowV18 and does not
    # construct this legacy workbench.
    from . import debug_tab as _debug_tab_module
    from .debug_tab_compat import DebugTabCompat

    _debug_tab_module.DebugTab = DebugTabCompat


_preserve_debug_tab_compatibility()
