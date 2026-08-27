"""Launch the B300 ST-Link desktop GUI."""

from __future__ import annotations

import argparse
import sys

from PySide6.QtWidgets import QApplication

from .main_window import MainWindow


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke-test", action="store_true",
                        help="Construct the GUI offscreen and exit without hardware access.")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    if args.smoke_test:
        app.processEvents()
        window.close()
        print("B300 GUI smoke test OK")
        return 0
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
