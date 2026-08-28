#!/usr/bin/env python3
"""PyInstaller-safe B300 GUI entry point."""

from __future__ import annotations

import sys


def main(argv=None) -> int:
    selected = list(sys.argv[1:] if argv is None else argv)
    if selected and selected[0] == "--apply-verified-update":
        from b300_core.update_helper import main as update_helper_main
        return update_helper_main(selected[1:])
    from b300_gui.__main__ import main as gui_main
    return gui_main(selected)


if __name__ == "__main__":
    raise SystemExit(main())
