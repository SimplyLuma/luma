# SPDX-License-Identifier: Apache-2.0
"""Exercise the preview launcher hook with a separate production-ID owner."""
from pathlib import Path
import os
import subprocess
import sys
import time

assert os.environ.get("LUMA_TASKS_ISOLATED_TEST") == "1"
temporary = Path(os.environ["XDG_DATA_HOME"]).parent
assert os.environ["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=" + str(temporary / "bus")
assert temporary.name.startswith("luma-tasks-test-")
production_id = "org.projectluma.Tasks"
preview_id = production_id + ".LumaUIPreview"
ready = temporary / "identity-owner-ready"
activated = temporary / "identity-production-activated"
from gi.repository import Gio, GLib


def wait(predicate, message, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for _ in range(50):
            if not GLib.MainContext.default().pending():
                break
            GLib.MainContext.default().iteration(False)
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError(message)


if "--sentinel" in sys.argv:
    sentinel = Gio.Application(application_id=production_id)
    sentinel.connect("activate", lambda *_: activated.write_text("Unexpected production activation\n"))
    assert sentinel.register(None) and not sentinel.get_is_remote()
    ready.write_text(production_id)
    GLib.MainLoop().run()
    raise SystemExit(0)

root = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(root / "src/prairie-core"), str(root / "src/luma-platform/appkit")]
os.environ["LUMA_TASKS_FIXTURE"] = str(root / "tests/fixtures/tasks-v70.json")
fixture = Path(os.environ["LUMA_TASKS_FIXTURE"])
fixture_before = fixture.read_bytes()
from prairie_apps import tasks

assert tasks.APP_ID == production_id
assert tasks.APP_ICON_ID == production_id
if "--preview" in sys.argv:
    # This is the exact module mutation used by sync-lumaui-preview.sh's bin/run.
    if "--wrong-hook" in sys.argv:
        tasks.APPLICATION_ID = preview_id  # Negative control for the Photos mismatch.
    else:
        tasks.APP_ID = getattr(tasks, "APP_ID", production_id) + ".LumaUIPreview"
    application = tasks.TasksApplication()
    window = None
    try:
        assert application.get_application_id() == preview_id, "Launcher override missed actual GApplication ID"
        assert tasks.APP_ICON_ID == production_id, "Preview changed the production icon identity"
        assert application.register(None) and not application.get_is_remote(), "Preview would activate another instance"
        bus = application.get_dbus_connection()
        production_owner = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "GetNameOwner", GLib.Variant("(s)", (production_id,)), GLib.VariantType.new("(s)"), Gio.DBusCallFlags.NONE, 1000, None).unpack()[0]
        assert production_owner != bus.get_unique_name(), "Preview shares the production owner's connection"
        application.activate()
        window = application.props.active_window
        assert window is not None and window.get_application() is application
        assert window.get_application().get_application_id() == preview_id
        wait(lambda: len(window.data["tasks"]) == 16, "Isolated fixture load")
        assert not activated.exists(), "Preview activated the production-ID owner"
        window.close()
        wait(lambda: window.closed and not application.get_windows(), "Preview remained open")
        print("PASS: actual Tasks Gtk/GApplication ID=" + preview_id + "; remote=False; preview owner=" + bus.get_unique_name() + "; production owner=" + production_owner + "; fixture loaded; windows=0", flush=True)
    finally:
        if window is not None and not window.closed:
            window.close()
        application.quit()
        tasks.APP_ID = production_id
    raise SystemExit(0)

connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)


def owns(name):
    return connection.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "NameHasOwner", GLib.Variant("(s)", (name,)), GLib.VariantType.new("(b)"), Gio.DBusCallFlags.NONE, 1000, None).unpack()[0]


sentinel = subprocess.Popen([sys.executable, __file__, "--sentinel"])
try:
    wait(lambda: ready.exists() or sentinel.poll() is not None, "Production-ID owner did not start")
    assert sentinel.poll() is None and ready.read_text() == production_id
    assert owns(production_id) and not owns(preview_id)
    rejected = subprocess.run([sys.executable, __file__, "--preview", "--wrong-hook"], capture_output=True, text=True, timeout=35)
    assert rejected.returncode != 0 and "Launcher override missed actual GApplication ID" in rejected.stderr, "Identity check did not reject the mismatched launcher hook"
    assert owns(production_id) and not owns(preview_id) and not activated.exists()
    print("PASS: negative control rejects APPLICATION_ID/APP_ID mismatch before registration or activation", flush=True)
    subprocess.run([sys.executable, __file__, "--preview"], check=True, timeout=35)
    assert owns(production_id) and not owns(preview_id), "Preview process or bus identity remained live"
    assert not activated.exists(), "Installed identity received activation"
    assert fixture.read_bytes() == fixture_before, "Preview identity check changed the fixture"
    production_probe = tasks.TasksApplication()
    assert production_probe.get_application_id() == production_id
    assert production_probe.register(None) and production_probe.get_is_remote(), "Production identity was changed"
    assert not activated.exists()
    print("PASS: separate production owner retained; production ID unchanged; zero production activations; preview bus name released", flush=True)
finally:
    sentinel.terminate()
    try:
        sentinel.wait(timeout=5)
    except subprocess.TimeoutExpired:
        sentinel.kill()
        sentinel.wait()
