"""Capture real production widgets offline, without USB/SSH/update discovery.

Screenshots deliberately show no project/probe or target evidence. They do not
represent a successful hardware session. Run from the repository checkout.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QSettings
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QScrollArea
from b300_core.gateway_profiles import GatewayProfileStore
from b300_core.project_profiles import ProjectProfileStore
from b300_gui.main_window_v18 import MainWindowV18
from b300_gui.theme import ThemeManager
from tests.test_gui_smoke import FakeService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'build/engineering-ui-review')
    parser.add_argument('--sizes', nargs='+', default=['1366x768','1600x900','1920x1080','2560x1440'])
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='b300-ui-capture-') as temp:
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, temp)
        app = QApplication.instance() or QApplication([])
        for name in ('segoeui.ttf','seguisb.ttf','consola.ttf','seguisym.ttf','seguiemj.ttf'):
            path = Path('C:/Windows/Fonts') / name
            if path.exists():
                QFontDatabase.addApplicationFont(str(path))
        app.setFont(QFont('Segoe UI', 10))
        ThemeManager.instance()._mode = 'dark'
        ThemeManager.instance().apply()
        root = Path(temp)
        window = MainWindowV18(service=FakeService(), probe_loader=lambda: (),
            automatic_updates=False, first_run_setup=False,
            gateway_store=GatewayProfileStore(root/'gateways.json', legacy_path=root/'legacy.json'),
            project_store=ProjectProfileStore(root/'projects.json'))
        window.openocd_ready = False
        window.settings_view.set_openocd_status(None)
        window._set_status('XEM TRƯỚC · NGOẠI TUYẾN', 'ready', notify=False)
        window._update_controls()
        window.settings_view.set_openocd_status(None)
        window.show()
        evidence = []
        for size in args.sizes:
            width, height = map(int, size.split('x'))
            window.resize(width, height)
            for page in ('program','monitor','debug','device','settings'):
                window.show_page(page)
                for _ in range(5):
                    app.processEvents()
                path = args.output / ('%s-%s.png' % (page, size))
                window.grab().save(str(path))
                overflows = [(s.objectName(),s.horizontalScrollBar().maximum())
                    for s in window.v18_stack.currentWidget().findChildren(QScrollArea)
                    if s.isVisible() and s.horizontalScrollBar().maximum() > 0]
                evidence.append(dict(page=page, requested=[width,height], actual=[window.width(),window.height()],
                    horizontal_overflow=overflows, screenshot=str(path.resolve())))
        (args.output/'layout-evidence.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
        window.close()
        print(json.dumps(evidence, indent=2))


if __name__ == '__main__':
    main()
