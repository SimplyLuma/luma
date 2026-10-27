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
        xid = subprocess.check_output(['xdotool', 'search', '--name', '^Try Luma$'], text=True).splitlines()[-1]
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
            if target == 'mobile':
                report['phone_process'] = command(session, ['pgrep', '-u', 'luma', '-f', '^/opt/luma/phosh/libexec/phosh( |$)'])
                command(session, ['test', '-f', '/run/luma-display/home-ready'])
            else:
                enabled = command(session, ['gsettings', 'get', 'org.gnome.shell', 'enabled-extensions'])
                assert 'handheld@project-luma.local' not in enabled, enabled
                report['enabled_extensions'] = enabled
                display_probe = '''import json
from gi.repository import Gio
proxy = Gio.DBusProxy.new_for_bus_sync(Gio.BusType.SESSION, 0, None,
    'org.gnome.Mutter.DisplayConfig', '/org/gnome/Mutter/DisplayConfig',
    'org.gnome.Mutter.DisplayConfig', None)
state = proxy.call_sync('GetCurrentState', None, 0, 5000, None).unpack()
print(json.dumps(state))
'''
                display_state = json.loads(command(session, ['python3', '-c', display_probe]))
                monitors, logical = display_state[1:3]
                assert len(monitors) == len(logical) == 1, display_state
                assert monitors[0][0][1:3] == ['LMA', 'Desktop'], monitors
                assert monitors[0][2]['display-name'] == 'Luma Desktop', monitors
                current_modes = [mode for mode in monitors[0][1] if mode[6].get('is-current')]
                assert len(current_modes) == 1 and current_modes[0][1:3] == [1920, 1080], current_modes
                assert logical[0][2] == 1.0, logical
                report['desktop_display'] = display_state
            report['graphics'] = json.loads((session.directory / 'graphics.json').read_text())
            expected_graphics = os.environ.get('LUMA_TESTER_QA_GRAPHICS')
            if expected_graphics:
                assert report['graphics']['mode'] == expected_graphics, report['graphics']
                # Diagnostics are installed only in the disposable QA overlay.
                with (output / 'graphics-tools.log').open('w') as log:
                    subprocess.run(session.ssh(['sudo', 'dnf', '-y', 'install', 'egl-utils']),
                                   stdout=log, stderr=log, timeout=120, check=True)
                renderer = subprocess.run(session.ssh(['env', 'EGL_PLATFORM=surfaceless', 'eglinfo', '-B']),
                                          capture_output=True, text=True, timeout=30)
                (output / 'guest-renderer.txt').write_text(renderer.stdout + renderer.stderr)
                if expected_graphics == 'virgl':
                    assert 'OpenGL core profile renderer: virgl ' in renderer.stdout, renderer.stdout
                    report['guest_virgl_renderer_verified'] = True
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
                if app.catalog_targets == {'desktop'}:
                    assert app.target == 'desktop'
                    assert app.device_rows['mobile'].get_sensitive()
                    report['available_target_selected'] = True
                app.select_intent('develop')
                app.select_target(target)
                app.save_settings(agent_ssh=True, folders=[dict(path=str(project), writable=True)])
                app.commands.invoke('tester.open')
                phase = 1
            elif phase == 1:
                if not app.busy and app.child is None and time.monotonic() - started > 10:
                    log = app.session.directory / 'launcher.log'
                    raise AssertionError(app.status_label.get_label() + '\n' + (log.read_text()[-2500:] if log.exists() else ''))
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
