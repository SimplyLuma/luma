#!/usr/bin/python3
# SPDX-License-Identifier: MPL-2.0
"""The first-unlock sheet against a real keyring daemon, on a real display.

A GTK fixture: run it in a throwaway session with its own HOME, bus and
display, never as a person with a keyring, e.g.

    HOME=$(mktemp -d) xvfb-run -a dbus-run-session -- python3 tests/first_unlock_smoke.py

It makes a login keyring, locks it, stands up a stand-in for the shell's
prompter (it allows replacement, as GNOME Shell does), and then checks that
an app's prompt is declined while the sheet holds the prompter, that a wrong
password stays on the sheet, that the right one unlocks and enrols, that the
shell gets its prompter back, and that the next app gets its secret with no
prompt. LUMA_KEYRING_SHOTS=DIR saves pictures of the sheet; LUMA_POSTURE=tv
draws it for a TV.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gio, GLib, Graphene, Gtk  # noqa: E402

from luma_keyring import autounlock, first_unlock  # noqa: E402
from luma_keyring.first_unlock import Kind  # noqa: E402
from luma_keyring.prompt_gate import PROMPTER_NAME, PromptGate  # noqa: E402
from luma_keyring.secrets import Keyring  # noqa: E402
from luma_keyring.sheet import FIRST_LINE, FIRST_TITLE, Posture, UnlockSheet, Use  # noqa: E402

PASSWORD = "first-unlock-smoke"
SHOTS = os.environ.get("LUMA_KEYRING_SHOTS")
CLIENT = """
import gi, sys
gi.require_version('Secret', '1')
from gi.repository import Secret
service = Secret.Service.get_sync(Secret.ServiceFlags.LOAD_COLLECTIONS, None)
login = [c for c in service.get_collections() if c.get_object_path().endswith('/login')][0]
count, _ = service.unlock_sync([login], None)
print('unlocked' if count and not login.get_locked() else 'dismissed', flush=True)
"""
SHELL = """
from gi.repository import Gio, GLib
Gio.bus_own_name(Gio.BusType.SESSION, 'org.gnome.keyring.SystemPrompter',
                 Gio.BusNameOwnerFlags.ALLOW_REPLACEMENT, None, None, None)
GLib.MainLoop().run()
"""
failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'} {name}{f' -- {detail}' if detail else ''}", flush=True)
    if not ok:
        failures.append(name)


def spin(until, seconds: float = 10) -> bool:
    context = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if until():
            return True
        context.iteration(False)
        time.sleep(0.01)
    return until()


def client(seconds: float = 8) -> str:
    """An app asking for the login keyring; this process keeps serving its bus meanwhile."""
    child = subprocess.Popen([sys.executable, "-c", CLIENT], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
    if not spin(lambda: child.poll() is not None, seconds):
        child.kill()
        child.wait()
        return "waiting (a prompt is up)"
    out, err = child.communicate()
    return out.strip() or err.strip()[-200:]


def owner(bus) -> str:
    return bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                         "GetNameOwner", GLib.Variant("(s)", (PROMPTER_NAME,)),
                         GLib.VariantType.new("(s)"), Gio.DBusCallFlags.NONE, -1, None).unpack()[0]


def shot(window: Gtk.Window, name: str) -> None:
    if not SHOTS:
        return
    spin(lambda: False, 0.4)
    width, height = window.get_width(), window.get_height()
    snapshot = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(window).snapshot(snapshot, width, height)
    node = snapshot.to_node()
    texture = window.get_renderer().render_texture(node, Graphene.Rect().init(0, 0, width, height))
    Path(SHOTS).mkdir(parents=True, exist_ok=True)
    texture.save_to_png(str(Path(SHOTS) / f"{name}.png"))


def tv_sizes(sheet) -> None:
    """In TV posture the sheet is drawn in TV units: the real sizes, at this screen's unit."""
    view = sheet.view
    spin(lambda: view.sheet.get_width() > 0, 5)
    unit = view.unit
    def drawn(widget, axis):
        # As drawn: the border box (get_width() is the content box, inside the CSS padding).
        ok, box = widget.compute_bounds(view)
        return (box.get_width() if axis == "w" else box.get_height()) if ok else -1

    expected = {"sheet width": (drawn(view.sheet, "w"), 860), "field height": (drawn(view.field, "h"), 76),
                "switch row height": (drawn(view.switch_row, "h"), 84),
                "switch": (drawn(view.switch_row.switch, "w"), 72),
                "Unlock height": (drawn(view.primary, "h"), 76), "Cancel height": (drawn(view.cancel, "h"), 76)}
    for name, (actual, units) in expected.items():
        check(f"TV: {name} is {units} units", abs(actual - units * unit) <= 2,
              f"{actual} px at unit {unit:.3f} (want {units * unit:.1f})")
    ok, box = view.sheet.compute_bounds(view)
    centre = box.get_x() + box.get_width() / 2 if ok else -1
    check("TV: the sheet is centred", ok and abs(centre - view.get_width() / 2) <= 2, f"{centre}")
    check("TV: the field takes the remote's focus first", view.focus.focused is view.field.text)


def seal(key, user=None):
    if os.environ.get("LUMA_KEYRING_REAL_SEAL"):
        return autounlock.seal_key(key)
    return b"sealed:" + key.encode()


def main() -> int:
    keyring = Keyring()
    if autounlock.state_dir().exists() or (Path.home() / ".local/share/keyrings/login.keyring").exists():
        raise SystemExit("refusing to run with a keyring or key slots in this HOME")
    keyring.create_login(PASSWORD)
    keyring.lock()
    check("the login keyring starts locked", keyring.collection().locked)

    shell = subprocess.Popen([sys.executable, "-c", SHELL])
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    spin(lambda: bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                               "org.freedesktop.DBus", "NameHasOwner",
                               GLib.Variant("(s)", (PROMPTER_NAME,)), None,
                               Gio.DBusCallFlags.NONE, -1, None).unpack()[0], 5)
    shell_owner = owner(bus)
    check("asked, for a locked unenrolled automatic sign-in",
          first_unlock.should_ask(keyring, autologin=True))

    app = Adw.Application(application_id="org.projectluma.KeyringSmoke",
                          flags=Gio.ApplicationFlags.NON_UNIQUE)
    app.register(None)
    app.hold()

    gate = PromptGate()
    check("the sheet takes the prompter from the shell", gate.take() and owner(bus) != shell_owner)
    started = time.monotonic()
    answer = client(5)
    check("an app's prompt meanwhile is declined at once",
          answer == "dismissed" and time.monotonic() - started < 3, answer)

    done: list = []
    sheet = UnlockSheet(app, Use.FIRST, keyring, noun=first_unlock.device_noun(),
                        work=lambda password, automatic: first_unlock.unlock(
                            password, automatic, keyring, seal=seal,
                            verify=lambda candidate: candidate == PASSWORD),
                        on_done=done.append)
    if sheet.presentation is Posture.TV:
        sheet.set_default_size(1920, 1080)  # a TV's screen; no window manager here to maximise it
    sheet.present()
    spin(lambda: sheet.get_mapped(), 5)
    title, line, switch_label = sheet.view.texts()
    check("title, line and switch say what the design says",
          title == FIRST_TITLE and line == FIRST_LINE and sheet.view.automatic
          and switch_label.startswith("Unlock automatically on this "), switch_label)
    if sheet.presentation is Posture.TV:
        tv_sizes(sheet)
    shot(sheet, "1-sheet")

    sheet.password = "not-the-password"
    sheet._on_primary()
    spin(lambda: not sheet.busy and bool(sheet.view.message_text()), 15)
    check("a wrong password stays on the sheet with an error",
          not done and sheet.view.message_is_error() and keyring.collection().locked,
          sheet.view.message_text())
    check("the field is cleared after an answer", sheet.password == "")
    shot(sheet, "2-wrong")

    sheet.password = PASSWORD
    sheet._on_primary()
    spin(lambda: bool(done), 30)
    result = done[0] if done else None
    check("the right password unlocks and enrols",
          result is not None and result.kind is Kind.AUTOMATIC and not keyring.collection().locked,
          repr(result))
    check("the device seal and the recovery copy exist",
          autounlock.credential_path().exists() and autounlock.wrap_path().exists())

    gate.release()
    spin(lambda: owner(bus) == shell_owner, 5)
    check("the shell has its prompter back", owner(bus) == shell_owner)
    answer = client(8)
    check("the next app gets its secret with no prompt", answer == "unlocked", answer)

    # The next session: the seal opens it with no question.
    keyring.lock()
    opened = autounlock.open_with_slots(
        keyring, unseal=lambda path: (autounlock.unseal_key(path)
                                      if os.environ.get("LUMA_KEYRING_REAL_SEAL")
                                      else path.read_bytes()[len(b"sealed:"):].decode()))
    check("the next session unlocks by itself", opened == autounlock.Result.UNLOCKED, opened)
    check("and is not asked again", not first_unlock.should_ask(keyring, autologin=True))

    # On an unlocked keyring only enrolling checks the password, and changes nothing.
    cred = autounlock.credential_path().read_bytes()
    after = first_unlock.unlock("not-it", True, keyring, seal=seal,
                                verify=lambda candidate: candidate == PASSWORD)
    check("a wrong password on an unlocked keyring changes nothing",
          after.kind is Kind.WRONG_PASSWORD and not keyring.collection().locked
          and autounlock.credential_path().read_bytes() == cred, repr(after))

    manage = UnlockSheet(app, Use.MANAGE, keyring, autologin=True)
    if manage.presentation is Posture.TV:
        manage.set_default_size(1920, 1080)
    manage.present()
    spin(lambda: manage.get_mapped(), 5)
    check("the keyring's page shows automatic unlock on",
          manage.view.automatic and manage.view.shown()["switch"] and not manage.view.shown()["field"])
    shot(manage, "3-manage")
    manage.view.toggle()
    spin(lambda: manage.view.shown()["field"], 2)
    check("turning it off asks for the password", manage.view.shown()["field"]
          and manage.view.shown()["primary"] == "Turn Off")
    shot(manage, "4-manage-off")
    manage._on_cancel()
    check("cancel puts the switch back", manage.view.automatic and not manage.view.shown()["field"])
    manage.close()

    shell.terminate()
    app.release()
    print(f"{len(failures)} failed" if failures else "all passed", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
