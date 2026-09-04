"""Launch the B300 ST-Link desktop GUI."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

if __name__ == "__main__" and not __package__:
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

try:
    from .main_window_v18 import MainWindowV18 as MainWindow
except ImportError:
    from b300_gui.main_window_v18 import MainWindowV18 as MainWindow

from b300_core.update_platform import detect_update_platform
from b300_core.update_public_key import MINISIGN_PUBLIC_KEY
from b300_core.updater import UpdateClient


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--smoke-test", action="store_true",
        help="Construct the v0.18 GUI offscreen and exit without probing USB/ST-Link or the MCU.",
    )
    parser.add_argument(
        "--first-run-setup", action="store_true",
        help="Prepare a fresh workstation using bundled prerequisites.",
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    app = QApplication.instance() or QApplication([])

    update_client = None
    if not args.smoke_test:
        try:
            update_platform = detect_update_platform(Path(sys.executable))
            update_client = UpdateClient(MINISIGN_PUBLIC_KEY, update_platform.value)
        except RuntimeError:
            update_client = None

    kwargs = dict(
        update_client=update_client,
        automatic_updates=not args.smoke_test,
        first_run_setup=args.first_run_setup and not args.smoke_test,
    )
    if args.smoke_test:
        # MainWindow normally refreshes probes during construction.  CI/package
        # smoke must prove UI startup without making any USB/hardware request.
        kwargs["probe_loader"] = lambda: ()

    window = MainWindow(**kwargs)
    if args.smoke_test:
        app.processEvents()
        window.close()
        app.processEvents()
        app.quit()
        return 0
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
