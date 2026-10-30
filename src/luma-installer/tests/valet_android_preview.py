# SPDX-License-Identifier: Apache-2.0
"""Validate Android advice in the real desktop and phone Valet widgets."""
from pathlib import Path
from unittest.mock import patch

import gi
gi.require_version('Adw', '1')
from gi.repository import Adw, GLib
from luma_installer.installer import InstallerWindow
from luma_installer.model import PackageReport

app = Adw.Application(application_id='org.projectluma.ValetAndroidPreviewTest')
assert app.register(None)
advice = 'This computer uses x86. Choose an x86 or universal version from the publisher.'
report = PackageReport(Path('/owned/download.apk'), 'android', 'Example', 'Android application',
                       1234, 'a'*64, 'arm64-v8a', version='2.0.2', warnings=(advice,))
with patch('luma_installer.installer.iter_records', return_value=[]), \
     patch.object(InstallerWindow, '_start_inspection'):
    window = InstallerWindow(app, path=report.path)
    window.inspected(report)
    window.present()
    while GLib.MainContext.default().pending():
        GLib.MainContext.default().iteration(False)
    assert window.card.primary.get_sensitive(), 'Android runtime must decide ABI support'
    assert window.card.notice.get_visible(), 'Architecture advice hidden on the main preview'
    assert window.card.phase_label.get_label() == advice
    assert window.card.phase_label.has_css_class('valet-warning')
    assert window.card.phone_warning() == advice, 'Phone layout lost architecture advice'
    assert window.card.phone_status()[0] == '', 'Phone should not duplicate the advice'
    assert ('Version', '2.0.2') in window.card.phone_facts()
    window.card.set_phase('working', 'Install', 'Installing')
    assert not window.card.phone_warning(), 'Warning leaked into installation progress'
    assert not window.card.phase_label.has_css_class('valet-warning')
    window.close()
print('PASS: Android architecture advice visible, install eligible, version shown, warning cleared')
