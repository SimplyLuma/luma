#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Exercise the actual LumaUI launcher, fresh guest profile and app hot fixes.

Requires the native launcher dependencies and an X11 display (Xvfb is suitable).
All guest changes go to a private tester overlay; the input image is immutable.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time

REPO = Path(__file__).resolve().parents[2]
if not os.environ.get('LUMA_TESTER_QA_INSTALLED'):
    sys.path.insert(0, str(REPO / 'src/luma-tester'))
os.environ['GSETTINGS_BACKEND'] = 'memory'
import gi

gi.require_version('Gtk', '4.0')
from gi.repository import Gio, GLib, Gtk
from luma_tester import gui
from luma_tester.core import digest


def main():
    output = Path(os.environ['DEV_ARTIFACT_DIR'])
    target = os.environ.get('LUMA_TESTER_QA_TARGET', 'mobile')
    if target not in ('mobile', 'desktop'):
        raise ValueError('QA target must be mobile or desktop.')
    state = os.environ['LUMA_TESTER_QA_STATE']
    project = output / 'Hello Luma'
    shutil.copytree(REPO / 'examples/tester-hello', project)
    (project / 'message.txt').write_text('First guest build.\n')
    report = {'target': target, 'tester_source': subprocess.check_output(
        ['git', '-C', str(REPO), 'rev-parse', 'HEAD'], text=True).strip(), 'passed': False}
    report['module_path'] = gui.__file__
    if os.environ.get('LUMA_TESTER_QA_INSTALLED'):
        assert Path(gui.__file__).is_relative_to(Path(os.environ['LUMA_TESTER_QA_INSTALLED']))
    phase = 0
    application = None
    watcher = None
    started = time.monotonic()
    failures = []
    watcher_log = open(output / 'watcher.log', 'w')

    def command(session, argv):
        return subprocess.check_output(session.ssh(argv), text=True, timeout=20).strip()

    def capture(app, name):
        xid = subprocess.check_output(['xdotool', 'search', '--name', '^Luma Tester$'], text=True).splitlines()[-1]
        subprocess.run(['import', '-window', xid, str(output / name)], check=True)

    def finish_hotfix(app):
        try:
            assert app.viewer and app.viewer.ready and app.viewer.frames > 0
            capture(app, target + '-hotfix.png')
            app.session.screenshot(output / (target + '-guest-hotfix.ppm'))
            report['frames_received'] = app.viewer.frames
            app.stop()
            report['shutdown_requested'] = True
        except BaseException as exc:
            failures.append(exc)
            app.quit()
        return False

    def verify_guest(app):
        nonlocal watcher
        session = app.session
        try:
            report['guest_image_sha256'] = session.manifest['image']['sha256']
            report['guest_build'] = session.manifest['build']
            marker = command(session, ['cat', '/etc/luma-device-class'])
            assert marker == ('handheld' if target == 'mobile' else 'desktop'), marker
            identity = command(session, ['cat', '/etc/luma/tester-image'])
            assert 'LUMA_TESTER_TARGET=' + target in identity
            assert 'LUMA_PHYSICAL_DEVICE=false' in identity
            assert 'ID=luma' in command(session, ['cat', '/etc/os-release'])
            report['guest_profile'] = marker
            report['guest_source'] = json.loads(command(session, ['cat', '/etc/luma/tester-source.json']))
            enabled = command(session, ['gsettings', 'get', 'org.gnome.shell', 'enabled-extensions'])
            assert ('handheld@project-luma.local' in enabled) == (target == 'mobile'), enabled
            report['enabled_extensions'] = enabled
            if target == 'mobile':
                info = command(session, ['gnome-extensions', 'info', 'handheld@project-luma.local'])
                assert 'State: ACTIVE' in info, info
                report['handheld_state'] = info
            report['package_versions'] = command(session, ['rpm', '-q', 'gnome-shell', 'mutter',
                                                          'luma-developer-platform', 'prairie-core-apps'])
            report['sdk_version'] = command(session, ['luma', '--version'])
            watcher = subprocess.Popen([sys.executable, '-m', 'luma_tester', '--state', str(session.root),
                'dev', app.manifest_path, str(project), '--', 'python3', 'main.py'],
                stdout=watcher_log, stderr=watcher_log)
            def app_pid():
                value = command(session, ['systemctl', '--user', 'show', '-p', 'MainPID', '--value', 'luma-tester-app.service'])
                return int(value or 0)
            deadline = time.monotonic() + 45
            first_pid = 0
            while time.monotonic() < deadline:
                if watcher.poll() is not None:
                    raise AssertionError('App watcher exited: ' + (output / 'watcher.log').read_text())
                first_pid = app_pid()
                if first_pid > 1:
                    time.sleep(2)
                    if app_pid() == first_pid:
                        break
                time.sleep(0.5)
            assert first_pid > 1, 'Example app did not start.'
            (project / 'message.txt').write_text('Hot fix is live.\n')
            deadline = time.monotonic() + 30
            second_pid = first_pid
            while time.monotonic() < deadline:
                second_pid = app_pid()
                if second_pid > 1 and second_pid != first_pid:
                    time.sleep(2)
                    if app_pid() == second_pid:
                        break
                time.sleep(0.5)
            assert second_pid > 1 and second_pid != first_pid, 'Source edit did not restart the guest app.'
            assert command(session, ['cat', 'Projects/app/message.txt']) == 'Hot fix is live.'
            report['hotfix'] = {'first_pid': first_pid, 'second_pid': second_pid,
                                'source_synced': True, 'app_restarted': True}
            watcher.send_signal(signal.SIGINT)
            watcher.wait(timeout=10)
            GLib.timeout_add_seconds(2, finish_hotfix, app)
        except BaseException as exc:
            failures.append(exc)
            GLib.idle_add(app.quit)

    def inspect():
        nonlocal phase, application
        app = Gtk.Application.get_default()
        if not app or not app.get_active_window():
            return True
        application = app
        try:
            if time.monotonic() - started > 210:
                raise AssertionError('Qualification deadline exceeded: ' + app.status_label.get_label())
            if phase == 0:
                app.select_intent('develop')
                app.select_target(target)
                class FolderChoice:
                    def select_folder_finish(self, _result):
                        return Gio.File.new_for_path(str(project))
                app.project_chosen(FolderChoice(), None)
                assert app.project == project
                app.commands.invoke('tester.open')
                phase = 1
            elif phase == 1:
                if app.display_failure:
                    raise AssertionError(app.display_failure)
                if app.graphical_ready and app.viewer and app.viewer.frames > 1:
                    assert app.stack.get_visible_child_name() == 'live'
                    capture(app, target + '-home.png')
                    phase = 2
                    threading.Thread(target=verify_guest, args=(app,), daemon=True).start()
            elif phase == 2 and report.get('shutdown_requested'):
                if app.child is None:
                    assert app.session.status() == 'stopped'
                    assert app.session.disk.exists()
                    report['passed'] = True
                    report['elapsed_seconds'] = round(time.monotonic() - started, 2)
                    app.quit()
                    return False
            if failures:
                raise failures[0]
        except BaseException as exc:
            failures.append(exc)
            app.quit()
            return False
        return True

    GLib.timeout_add(100, inspect)
    try:
        result = gui.launch(state)
        if failures:
            raise failures[0]
        assert report['passed'] and result == 0
        print('PASS actual LumaUI → ' + target + ' profile → native guest → source edit/restart → graceful shutdown.', flush=True)
    finally:
        if watcher and watcher.poll() is None:
            watcher.send_signal(signal.SIGINT)
            watcher.wait(timeout=10)
        if application and application.child and application.child.poll() is None:
            try:
                application.session.screenshot(output / 'failure-guest.ppm')
                command(application.session, ['journalctl', '--user', '-u', 'luma-tester-app', '-n', '80', '--no-pager'])
            except Exception:
                pass
            try:
                application.session.shutdown()
                application.child.wait(timeout=40)
            except Exception:
                application.child.terminate()
                application.child.wait(timeout=15)
        report['errors'] = [str(exc) for exc in failures]
        (output / 'qualification.json').write_text(json.dumps(report, indent=2) + '\n')
        watcher_log.close()


if __name__ == '__main__':
    main()
