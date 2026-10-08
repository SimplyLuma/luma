#!/usr/bin/env python3
"""End-to-end check of Do Not Disturb, hotspot, approval and Luma Keyboard on an emulator.

Same setup as `emulator_e2e.py`: a temporary desktop identity and loopback listener on
the host, the real debug APK on an Android emulator reached through `adb forward`.

    PYTHONPATH=src/luma-continuity python3 src/luma-connect-android/tools/emulator_features_e2e.py \
        --adb ~/Library/Android/sdk/platform-tools/adb \
        --apk src/luma-connect-android/app/build/outputs/apk/play/debug/app-play-debug.apk

It changes emulator state only for the duration of the run (Do Not Disturb access, the
selected keyboard) and restores the defaults it found. It does not set a screen lock, so
the signed approval path is expected to stop at "Set a screen lock". Never run it against
a personal phone or a real identity directory.
"""
import argparse
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import tempfile
import time

os.environ['LUMA_CONNECT_TEST_LOOPBACK'] = '1'
from luma_continuity.bootstrap import create_identity  # noqa: E402
from luma_continuity.companion import CompanionListener, DesktopAdapters, PairingSession, Registry, call  # noqa: E402
from luma_continuity.companion_approval import ApprovalBroker  # noqa: E402

PACKAGE = 'org.projectluma.connect'
PHONE_PORT = 47811
IME = f'{PACKAGE}/.ime.LumaKeyboardService'


class Adb:
    def __init__(self, binary, serial):
        self.binary, self.serial = binary, serial

    def __call__(self, *args, check=True, timeout=60):
        result = subprocess.run([self.binary, '-s', self.serial, *args], capture_output=True, text=True, timeout=timeout)
        if check and result.returncode != 0:
            raise RuntimeError(f'adb {" ".join(args)} failed: {result.stderr.strip()}')
        return result.stdout

    def shell(self, *args, **options):
        return self('shell', *args, **options)

    def nodes(self):
        self.shell('uiautomator', 'dump', '/sdcard/luma-ui.xml', check=False)
        xml = self('exec-out', 'cat', '/sdcard/luma-ui.xml', check=False)
        for node in re.finditer(r'<node [^>]*>', xml):
            attributes = dict(re.findall(r' ([a-z-]+)="([^"]*)"', node.group(0)))
            bounds = re.search(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', attributes.get('bounds', ''))
            if bounds: attributes['center'] = ((int(bounds[1]) + int(bounds[3])) // 2, (int(bounds[2]) + int(bounds[4])) // 2)
            yield attributes

    def find(self, pattern, timeout=15, field='text', flags=re.I):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for node in self.nodes():
                if re.search(pattern, node.get(field, ''), flags) and 'center' in node: return node
            time.sleep(1)
        return None

    def tap(self, pattern, timeout=15):
        node = self.find(pattern, timeout)
        if node: self.shell('input', 'tap', *map(str, node['center']))
        return node is not None


def step(name, ok, detail=''):
    print(f'{"PASS" if ok else "FAIL"}  {name}{"  " + detail if detail else ""}', flush=True)
    return ok


def notification_text(adb):
    return adb.shell('dumpsys', 'notification', '--noredact', check=False)


def wait_for(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value: return value
        time.sleep(.5)
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--adb', required=True)
    parser.add_argument('--apk', required=True)
    parser.add_argument('--serial', default='emulator-5554')
    parser.add_argument('--screenshots', default=None)
    parser.add_argument('--only', default='dnd,hotspot,approval,keyboard', help='comma-separated stages to run')
    options = parser.parse_args()
    adb = Adb(os.path.expanduser(options.adb), options.serial)
    results = []

    def screenshot(name):
        if not options.screenshots: return
        Path(options.screenshots).mkdir(parents=True, exist_ok=True)
        data = subprocess.run([adb.binary, '-s', adb.serial, 'exec-out', 'screencap', '-p'], capture_output=True).stdout
        (Path(options.screenshots) / f'{name}.png').write_bytes(data)

    adb('wait-for-device')
    original_ime = adb.shell('settings', 'get', 'secure', 'default_input_method').strip()
    adb('install', '-r', '-g', options.apk, timeout=180)
    adb.shell('pm', 'clear', PACKAGE)
    adb.shell('pm', 'grant', PACKAGE, 'android.permission.POST_NOTIFICATIONS', check=False)
    adb('forward', f'tcp:{PHONE_PORT}', f'tcp:{PHONE_PORT}')

    events = queue.Queue()
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        directory = root / 'identity'
        create_identity(directory)
        broker = ApprovalBroker(directory)
        adapters = DesktopAdapters(
            directory, downloads=root / 'Downloads',
            clipboard_set=lambda text, sensitive: events.put(('clipboard', text)),
            dnd=lambda peer, on: events.put(('dnd', on)) or on,
            approval=broker)
        listener = CompanionListener(directory, addresses=['127.0.0.1'], adapter_factory=adapters.for_peer, port=0,
                                     after=adapters.after_request)
        port = listener.start()
        paired = queue.Queue()
        session = PairingSession(
            directory, name='Emulator Desk', addresses=['127.0.0.1'], listen_port=port,
            phone_to_desktop=['device.status', 'clipboard.write', 'dnd.set', 'auth.response'],
            desktop_to_phone=['clipboard.write', 'dnd.set', 'hotspot.request', 'auth.request', 'input.control'],
            on_paired=paired.put)
        uri = session.start().replace('host=127.0.0.1', 'host=10.0.2.2')
        try:
            adb.shell('am', 'start', '-W', '-a', 'android.intent.action.VIEW', '-d', f"'{uri}'", PACKAGE)
            try: result = paired.get(timeout=40)
            except queue.Empty: result = None
            results.append(step('pairing grants the new capabilities', result is not None and
                                {'dnd.set', 'hotspot.request', 'auth.request', 'input.control'} <= set(result['outgoing']) and
                                {'dnd.set', 'auth.response'} <= set(result['incoming']), result and str(result['outgoing'])))
            if result is None: return 1
            fingerprint = result['fingerprint']
            adb.tap('codes match', timeout=10)
            # The phone's link service reports status once its listener is up.
            reported = wait_for(lambda: (Registry(directory).active().get(fingerprint) or {}).get('status'), 40)
            results.append(step('phone link service is up (device.status)', bool(reported), str(reported)))

            stages = set(options.only.split(','))
            # ---- Do Not Disturb, desktop to phone
            if 'dnd' in stages:
                # An enabled notification listener also holds Do Not Disturb access, so only check the
                # "needs-user" path when another test has not left Luma Connect's listener enabled.
                adb.shell('cmd', 'notification', 'disallow_dnd', PACKAGE, check=False)
                receipt = call(directory, fingerprint, 'dnd.set', {'on': True})
                listener_on = f'{PACKAGE}/{PACKAGE}.service.NotificationMirrorService' in adb.shell('dumpsys', 'notification', check=False)
                if listener_on and receipt['result'] == {'on': True}:
                    print('SKIP  dnd.set without access: access is implied by the enabled notification listener', flush=True)
                    call(directory, fingerprint, 'dnd.set', {'on': False})
                else:
                    results.append(step('dnd.set without Do Not Disturb access asks the person', receipt['result'] == {'error': 'needs-user'},
                                        str(receipt)))
                adb.shell('cmd', 'notification', 'allow_dnd', PACKAGE)
                receipt = call(directory, fingerprint, 'dnd.set', {'on': True})
                zen_on = wait_for(lambda: adb.shell('settings', 'get', 'global', 'zen_mode').strip() != '0', 10)
                rule = 'Luma Connect' in adb.shell('dumpsys', 'notification', check=False).split('ZenModeHelper', 1)[-1]
                results.append(step('dnd.set on turns on the Luma Connect mode', receipt['result'] == {'on': True} and bool(zen_on) and rule,
                                    f'{receipt} zen_mode={adb.shell("settings", "get", "global", "zen_mode").strip()} rule_listed={rule}'))
                receipt = call(directory, fingerprint, 'dnd.set', {'on': False})
                zen_off = wait_for(lambda: adb.shell('settings', 'get', 'global', 'zen_mode').strip() == '0', 10)
                results.append(step('dnd.set off ends it', receipt['result'] == {'on': False} and bool(zen_off), str(receipt)))
                try:
                    echoed = events.get(timeout=4)
                except queue.Empty:
                    echoed = None
                results.append(step('no echo back to the desktop (LinkService observer not wired in this build)', echoed is None, str(echoed)))

            # ---- Hotspot
            if 'hotspot' in stages:
                receipt = call(directory, fingerprint, 'hotspot.request', {})
                posted = wait_for(lambda: 'Share your mobile connection' in notification_text(adb), 10)
                results.append(step('hotspot.request posts a tap-to-open notification', receipt['result'] == {'error': 'needs-user'} and bool(posted),
                                    str(receipt)))
                adb.shell('cmd', 'statusbar', 'expand-notifications')
                tapped = adb.tap('Share your mobile connection', timeout=10)
                time.sleep(2)
                resumed = adb.shell('dumpsys', 'activity', 'activities', check=False)
                top = re.search(r'topResumedActivity=ActivityRecord\{\S+ \S+ (\S+)', resumed)
                screenshot('hotspot-settings')
                results.append(step('tapping it opens the Settings hotspot screen', tapped and top is not None and 'com.android.settings' in top.group(1),
                                    top.group(1) if top else 'no resumed activity'))
                adb.shell('input', 'keyevent', 'KEYCODE_HOME')

            # ---- Approve with your phone (deny path, and approve without a screen lock)
            if 'approval' in stages:
                future = broker.request(fingerprint, 'Pair a new Bluetooth keyboard', 'Settings', timeout=60)
                posted = wait_for(lambda: 'Approve on Emulator Desk' in notification_text(adb), 10)
                results.append(step('auth.request posts an approval notification', bool(posted) and not future.done()))
                adb.shell('cmd', 'statusbar', 'expand-notifications')
                adb.tap('Approve on Emulator Desk', timeout=10)
                shown = adb.find('Pair a new Bluetooth keyboard', timeout=10)
                screenshot('approval')
                results.append(step('approval screen shows what is being approved', shown is not None))
                adb.tap('^Approve$', timeout=5)
                lock_message = adb.find('Set a screen lock', timeout=8)
                results.append(step('Approve without a screen lock explains what is needed and signs nothing',
                                    lock_message is not None and not future.done()))
                adb.tap('^Close$', timeout=5)
                future_deny = broker.request(fingerprint, 'Pair a new Bluetooth keyboard', 'Settings', timeout=60)
                try: future.result(0)
                except Exception: pass
                wait_for(lambda: 'Approve on Emulator Desk' in notification_text(adb), 10)
                adb.shell('cmd', 'statusbar', 'expand-notifications')
                adb.tap('Approve on Emulator Desk', timeout=10)
                adb.tap('^Deny$', timeout=10)
                try: denied = future_deny.result(20)
                except Exception as error: denied = error
                told = adb.find('Denied', timeout=10)
                results.append(step('Deny sends an unsigned denial and the desktop resolves False', denied is False and told is not None, repr(denied)))
                adb.tap('^Close$', timeout=5)

            # ---- Luma Keyboard
            if 'keyboard' in stages:
                receipt = call(directory, fingerprint, 'input.control', {'events': [{'type': 'text', 'text': 'hi'}]})
                results.append(step('input.control without Luma Keyboard asks the person', receipt['result'] == {'error': 'needs-user'}, str(receipt)))
                adb.shell('ime', 'enable', IME)
                adb.shell('ime', 'set', IME)
                adb.shell('am', 'start', '-W', '-a', 'com.android.settings.action.SETTINGS_SEARCH', check=False)
                time.sleep(3)
                screenshot('keyboard')
                receipt = call(directory, fingerprint, 'input.control', {'events': [
                    {'type': 'text', 'text': 'luma keyboard'}, {'type': 'key', 'key': 'backspace', 'pressed': True},
                    {'type': 'key', 'key': 'backspace', 'pressed': False}, {'type': 'text', 'text': 'D'}]})
                typed = adb.find('^luma keyboarD$', timeout=8, flags=0)
                screenshot('keyboard-typed')
                receipt = call(directory, fingerprint, 'input.control', {'events': [
                    {'type': 'key', 'key': 'left', 'pressed': True}, {'type': 'key', 'key': 'left', 'pressed': False},
                    {'type': 'text', 'text': 'X'}]})
                moved = adb.find('^luma keyboarXD$', timeout=8, flags=0)
                results.append(step('arrow keys and text in one batch stay in order', receipt['result'] == {'accepted': True} and moved is not None,
                                    str(receipt)))
                call(directory, fingerprint, 'input.control', {'events': [
                    {'type': 'key', 'key': 'right', 'pressed': True}, {'type': 'key', 'key': 'right', 'pressed': False},
                    {'type': 'key', 'key': 'left', 'pressed': True}, {'type': 'key', 'key': 'left', 'pressed': False},
                    {'type': 'key', 'key': 'backspace', 'pressed': True}, {'type': 'key', 'key': 'backspace', 'pressed': False}]})
                results.append(step('the computer types into the focused field through the keyboard', receipt['result'] == {'accepted': True} and typed is not None,
                                    str(receipt)))
                # The emulator has a hardware keyboard, so the input view stays hidden until forced.
                hard_keyboard = adb.shell('settings', 'get', 'secure', 'show_ime_with_hard_keyboard', check=False).strip()
                adb.shell('settings', 'put', 'secure', 'show_ime_with_hard_keyboard', '1', check=False)
                field = adb.find('^luma keyboarD$', timeout=3, flags=0)
                if field: adb.shell('input', 'tap', *map(str, field['center']))
                time.sleep(2)
                screenshot('keyboard-bar')
                windows = adb.shell('dumpsys', 'window', 'windows', check=False)
                section = re.search(r'Window #\d+ Window\{[^}]*InputMethod\}:(.*?)(?=\n  Window #|\Z)', windows, re.S)
                shown = bool(section and 'isOnScreen=true' in section.group(1))
                # Not counted: with a hardware keyboard attached Android may keep any input view hidden.
                print(f'INFO  Luma Keyboard input view on screen: {shown} (see keyboard-bar.png; hardware keyboard present on most emulators)',
                      flush=True)
                if hard_keyboard in {'', 'null'}: adb.shell('settings', 'delete', 'secure', 'show_ime_with_hard_keyboard', check=False)
                else: adb.shell('settings', 'put', 'secure', 'show_ime_with_hard_keyboard', hard_keyboard, check=False)

                while not events.empty(): events.get_nowait()
                call(directory, fingerprint, 'clipboard.write', {'text': 'from the desktop', 'sensitive': False})
                try: echo = events.get(timeout=5)
                except queue.Empty: echo = None
                results.append(step('clipboard written by the computer is not sent back', echo is None, str(echo)))

                receipt = call(directory, fingerprint, 'input.control', {'events': [
                    {'type': 'key', 'key': 'control', 'pressed': True}, {'type': 'key', 'key': 'a', 'pressed': True},
                    {'type': 'key', 'key': 'a', 'pressed': False}, {'type': 'key', 'key': 'c', 'pressed': True},
                    {'type': 'key', 'key': 'c', 'pressed': False}, {'type': 'key', 'key': 'control', 'pressed': False}]})
                try: copied = events.get(timeout=10)
                except queue.Empty: copied = None
                screenshot('keyboard-copied')
                results.append(step('Ctrl+A, Ctrl+C in the field sends the copy to the computer', copied == ('clipboard', 'luma keyboarD'),
                                    f'{receipt["result"]} {copied}'))
        finally:
            adb.shell('input', 'keyevent', 'KEYCODE_HOME', check=False)
            if original_ime and original_ime != 'null': adb.shell('ime', 'set', original_ime, check=False)
            adb.shell('ime', 'disable', IME, check=False)
            adb.shell('cmd', 'notification', 'set_dnd', 'off', check=False)
            adb.shell('cmd', 'notification', 'disallow_dnd', PACKAGE, check=False)
            broker.close()
            listener.stop()
            session.cancel()
            adb('forward', '--remove', f'tcp:{PHONE_PORT}', check=False)
    passed = sum(1 for ok in results if ok)
    print(f'{passed}/{len(results)} checks passed')
    return 0 if passed == len(results) else 1


if __name__ == '__main__':
    sys.exit(main())
