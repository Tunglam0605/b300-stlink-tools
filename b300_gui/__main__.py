"""Launch the B300 ST-Link desktop GUI."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from .main_window import MainWindow
from b300_core.update_platform import detect_update_platform
from b300_core.update_public_key import MINISIGN_PUBLIC_KEY
from b300_core.updater import UpdateClient


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke-test", action="store_true",
                        help="Construct the GUI offscreen and exit without hardware access.")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    app = QApplication.instance() or QApplication([])
    update_client = None
    if not args.smoke_test:
        try:
            update_platform = detect_update_platform(Path(sys.executable))
            update_client = UpdateClient(MINISIGN_PUBLIC_KEY, update_platform.value)
        except RuntimeError:
            update_client = None
    window = MainWindow(
        update_client=update_client,
        automatic_updates=not args.smoke_test,
    )
    if args.smoke_test:
        app.processEvents()
        window.close()
        app.processEvents()
        app.shutdown()
        print("B300 GUI smoke test OK")
        return 0
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
