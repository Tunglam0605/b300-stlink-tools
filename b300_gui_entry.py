#!/usr/bin/env python3
"""PyInstaller-safe B300 GUI entry point."""

from b300_gui.__main__ import main


if __name__ == "__main__":
    raise SystemExit(main())
