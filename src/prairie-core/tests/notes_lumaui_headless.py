# SPDX-License-Identifier: Apache-2.0
"""Run Notes runtime tests on a private Wayland desktop, as conform does.

Run on the host: python3 src/prairie-core/tests/notes_lumaui_headless.py
Optional arguments name unittest cases. No installed app, session bus or user
settings are used. Every process this runner starts is stopped on exit.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import time


def wait_socket(path, process):
    for _ in range(100):
        if path.exists():
            return
        if process.poll() is not None:
            raise RuntimeError(f'private desktop process exited: {process.returncode}')
        time.sleep(.05)
    raise RuntimeError(f'private desktop did not create {path.name}')


def main():
    root = Path(__file__).resolve().parents[3]
    processes = []
    with tempfile.TemporaryDirectory(prefix='notes-runtime-') as scratch:
        work = Path(scratch)
        runtime = work / 'runtime'
        runtime.mkdir(mode=0o700)
        env = {k: v for k, v in os.environ.items()
               if not k.startswith('XDG_') and k not in (
                   'DISPLAY', 'WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS',
                   'DBUS_SYSTEM_BUS_ADDRESS', 'WAYLAND_SOCKET', 'GTK_THEME', 'GTK_DEBUG', 'GDK_DEBUG', 'PYTHONPATH')}
        env.update(XDG_RUNTIME_DIR=str(runtime), XDG_SESSION_TYPE='wayland',
                   GSETTINGS_BACKEND='memory', GTK_A11Y='none', GSK_RENDERER='cairo',
                   LUMA_NOTES_LIVE_SETTLED='1', PYTHONNOUSERSITE='1')
        for kind in ('CONFIG', 'DATA', 'CACHE', 'STATE'):
            directory = work / kind.lower()
            directory.mkdir()
            env[f'XDG_{kind}_HOME'] = str(directory)
        env['PYTHONPATH'] = os.pathsep.join(str(root / p) for p in (
            'src/prairie-core', 'src/prairie-core/tests', 'src/luma-platform/appkit'))
        config = work / 'bus.conf'
        config.write_text('<busconfig><type>session</type><auth>EXTERNAL</auth>'
                          '<policy context="default"><allow own="*"/>'
                          '<allow send_destination="*"/><allow receive_sender="*"/>'
                          '</policy></busconfig>')
        log_path = work / 'desktop.log'
        with log_path.open('w') as log:
            try:
                for kind in ('SESSION', 'SYSTEM'):
                    socket = runtime / kind.lower()
                    env[f'DBUS_{kind}_BUS_ADDRESS'] = 'unix:path=' + str(socket)
                    process = subprocess.Popen([
                        'systemd-socket-activate', '-E', 'DBUS_SESSION_BUS_ADDRESS',
                        '-E', 'XDG_RUNTIME_DIR', '-l', str(socket),
                        'dbus-broker-launch', '--scope=user', '--config-file=' + str(config)],
                        env=env, stdout=log, stderr=log)
                    processes.append(process)
                    wait_socket(socket, process)
                display = 'notes-runtime'
                compositor = subprocess.Popen([
                    'mutter', '--headless', '--wayland', '--no-x11',
                    '--wayland-display=' + display, '--virtual-monitor', '1920x1200'],
                    env=env, stdout=log, stderr=log)
                processes.append(compositor)
                wait_socket(runtime / display, compositor)
                env.update(WAYLAND_DISPLAY=display, GDK_BACKEND='wayland')
                tests = sys.argv[1:] or ['notes_lumaui_runtime']
                result = subprocess.run([sys.executable, '-W', 'ignore', '-m', 'unittest', *tests],
                                        cwd=root, env=env, timeout=120)
                if result.returncode:
                    log.flush()
                    print(log_path.read_text()[-6000:], file=sys.stderr)
                return result.returncode
            finally:
                for process in reversed(processes):
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(5)
                log.flush()
                if sys.exc_info()[0]:
                    print(log_path.read_text()[-6000:], file=sys.stderr)


if __name__ == '__main__':
    raise SystemExit(main())
