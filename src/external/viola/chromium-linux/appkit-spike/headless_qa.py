# SPDX-License-Identifier: GPL-3.0-only
"""Run native visual QA on an isolated Mutter Wayland display and private bus.

Uses Fedora's installed Mutter and dbus-broker; no desktop settings, packages
or session bus names are changed. Every subprocess belongs to this run.
"""
import argparse
import ast
import configparser
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time


def stop(process):
    if process and process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--display-scale', type=float, choices=(1.0, 1.25, 1.5, 2.0), default=1.0)
    parser.add_argument('--engine', type=Path, required=True)
    parser.add_argument('--work-root', type=Path, required=True)
    parser.add_argument('--toolkit-resources', type=Path)
    parser.add_argument('--control-capture-stage', choices=('browser', 'address', 'context', 'workspaces', 'workspace-context', 'workspace-editor', 'workspace-colors', 'scroll', 'tiled'), default='browser')
    parser.add_argument('--qualify-manual-identity', action='store_true')
    parser.add_argument('--qualify-controls', action='store_true')
    parser.add_argument('--representative', action='store_true')
    parser.add_argument('--media-fixture', type=Path)
    parser.add_argument('--qualify-input', action='store_true')
    parser.add_argument('--qualify-page-menu', action='store_true')
    args = parser.parse_args()
    if args.qualify_page_menu:
        args.qualify_input = True
    args.work_root.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix='headless-', dir=args.work_root))
    bus_root = Path(tempfile.mkdtemp(prefix='viola-qa-bus-'))
    config = bus_root / 'bus.conf'
    config.write_text('''<busconfig><type>session</type><auth>EXTERNAL</auth>
      <policy context="default"><allow own="*"/><allow send_destination="*"/>
      <allow receive_sender="*"/></policy></busconfig>''')
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(bus_root / 'bus'))
    listener.listen(32)
    desktop_values = {key: subprocess.check_output(
        ['/usr/bin/gsettings', 'get', 'org.gnome.desktop.interface', key],
        text=True, timeout=5).strip() for key in
        ('icon-theme', 'gtk-theme', 'font-name', 'monospace-font-name', 'text-scaling-factor')}
    environment = os.environ.copy()
    environment['PYTHONPATH'] = str(Path(__file__).resolve().parent) + os.pathsep + environment.get('PYTHONPATH', '')
    environment['DBUS_SESSION_BUS_ADDRESS'] = 'unix:path=' + str(bus_root / 'bus')
    environment['XDG_RUNTIME_DIR'] = environment.get('XDG_RUNTIME_DIR', '/run/user/' + str(os.getuid()))
    environment['WAYLAND_DISPLAY'] = 'viola-qa-' + str(os.getpid())
    environment.pop('DISPLAY', None)
    # Luma's desktop tiling belongs to its shell, which is absent from this
    # bare private Mutter. Configure the fixture's own keyfile backend only.
    environment['GSETTINGS_BACKEND'] = 'keyfile'
    environment['XDG_CONFIG_HOME'] = str(run / 'config')
    # This private visual fixture has no accessibility service. Real desktop
    # accessibility remains enabled and is a separate acceptance gate.
    environment['GTK_A11Y'] = 'none'
    broker = compositor = application = capture = None
    audio_module = None
    with (run / 'headless.log').open('w') as log:
        try:
            if args.media_fixture:
                sink = 'viola_qa_' + str(os.getpid())
                audio_module = subprocess.check_output(['/usr/bin/pactl', 'load-module',
                    'module-null-sink', 'sink_name=' + sink], text=True, timeout=5).strip()
                environment['PULSE_SINK'] = sink
            broker_env = environment | {'LISTEN_FDS': '1'}
            descriptor = listener.fileno()
            setup = f'exec 3<&{descriptor}; '
            if descriptor != 3:
                setup += f'exec {descriptor}<&-; '
            # Only the numeric owned descriptor is interpolated. Executable
            # arguments, including the config path, remain separate argv.
            setup += 'export LISTEN_PID=$$; exec "$@"'
            broker = subprocess.Popen(['/bin/bash', '-c', setup, 'viola-private-bus',
                '/usr/bin/dbus-broker-launch', '--scope=user', '--config-file=' + str(config)],
                pass_fds=(descriptor,), env=broker_env, stdout=log, stderr=log, start_new_session=True)
            listener.close()
            for schema, key, value in (
                    ('org.gnome.mutter.keybindings', 'toggle-tiled-left', "['<Super>Left']"),
                    ('org.gnome.desktop.wm.keybindings', 'switch-windows', "['<Alt>Tab']"),
                    ('org.gnome.desktop.interface', 'color-scheme', 'prefer-dark')):
                subprocess.run(['/usr/bin/gsettings', 'set', schema, key, value],
                               env=environment, check=True, timeout=5)
            for key, value in desktop_values.items():
                subprocess.run(['/usr/bin/gsettings', 'set', 'org.gnome.desktop.interface', key, value],
                               env=environment, check=True, timeout=5)
            gtk_config = configparser.ConfigParser()
            gtk_config['Settings'] = {
                'gtk-icon-theme-name': ast.literal_eval(desktop_values['icon-theme']),
                'gtk-theme-name': ast.literal_eval(desktop_values['gtk-theme']),
                'gtk-font-name': ast.literal_eval(desktop_values['font-name'])}
            gtk_directory = run / 'config/gtk-4.0'
            gtk_directory.mkdir(parents=True, exist_ok=True)
            with (gtk_directory / 'settings.ini').open('w') as settings_file:
                gtk_config.write(settings_file)
            (run / 'desktop-settings.json').write_text(__import__('json').dumps(desktop_values, indent=2))
            compositor = subprocess.Popen(['/usr/bin/mutter', '--headless', '--wayland', '--no-x11',
                f'--virtual-monitor={round(1600*args.display_scale)}x{round(1000*args.display_scale)}', '--wayland-display=' + environment['WAYLAND_DISPLAY']],
                env=environment, stdout=log, stderr=log, start_new_session=True)
            display_path = Path(environment['XDG_RUNTIME_DIR']) / environment['WAYLAND_DISPLAY']
            deadline = time.monotonic() + 15
            while not display_path.exists():
                if broker.poll() is not None or compositor.poll() is not None:
                    raise RuntimeError('Private display failed; see ' + str(run / 'headless.log'))
                if time.monotonic() > deadline:
                    raise TimeoutError('Private Wayland display did not become ready')
                time.sleep(.1)
            # The Wayland socket appears before Mutter owns its capture/input
            # bus names. Wait for those services before starting physical QA.
            subprocess.run([sys.executable, '-c', """
import time
from gi.repository import Gio, GLib
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
end = time.monotonic() + 5
while time.monotonic() < end:
    ready = all(bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
        'org.freedesktop.DBus', 'NameHasOwner', GLib.Variant('(s)', (name,)),
        None, Gio.DBusCallFlags.NONE, 1000, None).unpack()[0]
        for name in ('org.gnome.Mutter.ScreenCast', 'org.gnome.Mutter.RemoteDesktop'))
    if ready:
        break
    time.sleep(.05)
else:
    raise TimeoutError('Private Mutter capture/input services did not become ready')
"""], env=environment, check=True, timeout=7, stdout=log, stderr=log)
            if args.display_scale != 1:
                # Change only this run's private compositor. Logical geometry
                # stays 1600x1000 so physical input qualification stays valid.
                scale_script = r"""
import sys,time
from gi.repository import Gio,GLib
c=Gio.bus_get_sync(Gio.BusType.SESSION,None)
def call(name,p=None):
 return c.call_sync('org.gnome.Mutter.DisplayConfig','/org/gnome/Mutter/DisplayConfig',
  'org.gnome.Mutter.DisplayConfig',name,p,None,Gio.DBusCallFlags.NONE,5000,None).unpack()
state=call('GetCurrentState'); monitor=state[1][0]
mode=next((m for m in monitor[1] if m[6].get('is-current')),monitor[1][0])
params=GLib.Variant('(uua(iiduba(ssa{sv}))a{sv})',(state[0],1,
 [(0,0,float(sys.argv[1]),0,True,[(monitor[0][0],mode[0],{})])],{}))
call('ApplyMonitorsConfig',params)
"""
                subprocess.run([sys.executable, '-c', scale_script, str(args.display_scale)],
                               env=environment, check=True, timeout=10, stdout=log, stderr=log)
            diagnostic = ('import faulthandler,runpy,sys; faulthandler.dump_traceback_later(50); '
                          'sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0],run_name="__main__")')
            application_args = [sys.executable, '-c', diagnostic,
                str(Path(__file__).with_name('integrated_window.py')),
                '--engine', str(args.engine), '--work-root', str(run),
                '--seconds', '45' if os.environ.get('VIOLA_QA_RESPONSIVE') == '1' else '45' if os.environ.get('VIOLA_QA_WINDOWS') == '1' else '60' if args.representative else '70' if args.qualify_controls else '30' if args.qualify_input else '22']
            if args.qualify_manual_identity:
                application_args.append('--qualify-manual-identity')
            if args.qualify_input:
                # The adapter fixture controls allocations; it stays unmapped.
                application_args.append('--qualify-input')
                if args.qualify_page_menu:
                    application_args.append('--qualify-page-menu')
            else:
                application_args.append('--qa-present')
                if args.representative:
                    application_args.append('--representative')
                    if args.media_fixture:
                        application_args.extend(['--media-fixture', str(args.media_fixture)])
                if args.qualify_controls:
                    application_args.append('--qualify-controls')
            application_environment = environment.copy()
            # The private bus has no desktop appearance portal. Select the
            # actual installed dark kit sheet and native Adw dark mode for
            # this explicit reference fixture, not in normal launches.
            application_environment['LUMA_APPKIT_STYLE_PATH'] = environment.get(
                'LUMA_APPKIT_STYLE_PATH', '/usr/share/luma-appkit/luma-appkit-dark.css')
            application_environment['VIOLA_QA_APPEARANCE'] = 'dark'
            if args.qualify_controls:
                application_environment['VIOLA_QA_CAPTURE_STAGE'] = args.control_capture_stage
                application_environment['VIOLA_QA_CAPTURE_MARKER'] = str(run / 'controls-capture-ready')
            if args.toolkit_resources:
                root = args.toolkit_resources.resolve(strict=True)
                manifest = __import__('json').loads((root / 'manifest.json').read_text())
                if manifest.get('appearance') != 'dark':
                    raise ValueError('The reference fixture requires a dark toolkit resource candidate')
                for child in ('adw/gtk.css', 'gtk/Default-light.css', 'gtk/Default-dark.css'):
                    if not (root / child).is_file():
                        raise ValueError('Incomplete private toolkit resources: ' + child)
                application_environment['G_RESOURCE_OVERLAYS'] = (
                    '/org/gnome/Adwaita/styles=' + str(root / 'adw') + ':' +
                    '/org/gtk/libgtk/theme/Default=' + str(root / 'gtk'))
            if args.qualify_controls:
                baseline = run / 'empty-monitor.png'
                subprocess.run([sys.executable, str(Path(__file__).with_name('capture_private_display.py')),
                                '--output', str(baseline)], env=environment, check=True, timeout=20)
                application_environment['VIOLA_QA_EMPTY_MONITOR'] = str(baseline)
            application = subprocess.Popen(application_args, env=application_environment, start_new_session=True)
            if args.representative:
                deadline = time.monotonic() + 45
                while not (run / 'native-ready').exists():
                    if application.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError('Representative browser state did not become ready')
                    time.sleep(.1)
                if args.media_fixture:
                    while not (run / 'native-media-ready').exists():
                        if application.poll() is not None or time.monotonic() > deadline:
                            raise RuntimeError('Native video preview did not become ready')
                        time.sleep(.1)
                time.sleep(1)
            elif args.qualify_controls:
                deadline = time.monotonic() + 35
                while not (run / 'controls-capture-ready').exists():
                    if application.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError('Requested native control capture stage was not reached')
                    time.sleep(.1)
            else:
                time.sleep(5)
            capture_code = 0
            # Responsive qualification captures every mapped GTK allocation itself.
            # It does not claim compositor-shadow evidence from a single monitor image.
            if os.environ.get('VIOLA_QA_RESPONSIVE') != '1':
                capture = subprocess.Popen([sys.executable,
                    str(Path(__file__).with_name('capture_private_display.py')),
                    '--output', str(run / 'native-monitor.png')] + (['--focus-native-window'] if args.representative else []), env=environment, start_new_session=True)
                capture_code = capture.wait(timeout=20)
            code = application.wait(timeout=75 if args.qualify_controls else 55)
            print('Isolated native QA:', run)
            if code or capture_code:
                raise SystemExit(code or capture_code)
        finally:
            stop(capture)
            stop(application)
            stop(compositor)
            stop(broker)
            if audio_module:
                subprocess.run(['/usr/bin/pactl', 'unload-module', audio_module], check=True, timeout=5)
            listener.close()
            for path in bus_root.iterdir():
                path.unlink()
            bus_root.rmdir()


if __name__ == '__main__':
    main()
