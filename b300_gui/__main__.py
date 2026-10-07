"""Launch the B300 ST-Link desktop GUI."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

if __name__ == "__main__" and not __package__:
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

try:
    from .main_window_pulse import MainWindowPulse as MainWindow
except ImportError:
    from b300_gui.main_window_pulse import MainWindowPulse as MainWindow

from b300_core.update_platform import detect_update_platform
from b300_core.update_public_key import MINISIGN_PUBLIC_KEY
from b300_core.updater import UpdateClient


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--smoke-test", action="store_true",
        help="Construct the Pulse GUI offscreen and exit without probing USB/ST-Link or the MCU.",
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
        accepted = window.close()
        # Pulse releases sessions off-thread; let that bounded cleanup finish
        # before destroying QObject-owned QThreads in a packaging smoke run.
        if isinstance(getattr(window, "_pulse_cleanup_done", None), bool):
            from PySide6.QtCore import QTimer
            timer = QTimer()
            timer.setInterval(10)
            def check_cleanup():
                if window._pulse_cleanup_done:
                    app.exit(0)
                elif not window._pulse_tasks.busy:
                    app.exit(1)
            timer.timeout.connect(check_cleanup)
            timer.start()
            deadline = QTimer()
            deadline.setSingleShot(True)
            deadline.timeout.connect(lambda: app.exit(1))
            deadline.start(20000)
            smoke_result = app.exec()
            timer.stop()
            deadline.stop()
            if smoke_result != 0:
                return smoke_result
        elif not accepted:
            # Canonical synchronous windows must also accept close before
            # disposal. Never destroy a refused window or unfinished worker.
            return 1
        window.deleteLater()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        app.quit()
        return 0
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
