#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""The real window against a fake Mutter: ask, choose, arrange, keep, name."""
import json, os, tempfile, time
from pathlib import Path

home = Path(tempfile.mkdtemp())
os.environ["HOME"] = str(home)
os.environ["XDG_STATE_HOME"] = str(home / "state")
(home / ".config").mkdir()

import gi
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Gio, GLib, GObject
from luma_displays import application, model, names
from luma_displays.displayconfig import PERSISTENT, TEMPORARY, VERIFY
from test_model import home_state


class FakeConfig(GObject.Object):
    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_LAST, None, ())}
    def __init__(self):
        super().__init__()
        self.current = home_state()
        self.calls = []
    def state(self):
        return self.current
    def apply(self, state, layout, method):
        assert state.serial == self.current.serial, "applies name the latest serial"
        self.calls.append((method, model.apply_arguments(state, layout.copy())))
        if method != VERIFY:
            self.current = model.State(self.current.serial + 1, self.current.monitors, layout.copy(),
                                       self.current.layout_mode, True, False)


def settle(seconds=0.5):
    context = GLib.MainContext.default(); end = time.monotonic() + seconds
    while time.monotonic() < end:
        while context.pending(): context.iteration(False)
        time.sleep(0.01)


config = FakeConfig()
app = application.DisplaysApplication(config_factory=lambda: config)
app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
assert app.register(None)
app.arrive()
assert app.window is None, "Displays waits for the arrangement to hold before asking"
settle((application.ARRIVAL_LOOKS - 1) * application.ARRIVAL_LOOK_MS / 1000 + 0.5)
window = app.window
assert window is not None and window.arrival, "three displays never arranged: Displays asks"
settle()
assert window.title.get_label() == "New Display Arrangement"
assert window.mode_tiles["extend"].get_active()

window.mode_tiles["mirror"].set_active(True); settle()
method, args = config.calls[-1]
assert method == TEMPORARY and len(args) == 1 and len(args[0][5]) == 3, "mirror is shown, not kept"
window.mode_tiles["extend"].set_active(True); settle()
assert not config.current.layout.mirror

laptop = next(p for p in window.layout.placements if p.connector == "eDP-1")
window._moved("eDP-1", -5000, 0); settle()
assert model.connected(list(model.rects(config.current, config.current.layout).values()))

window.name_row.set_text("Home")
window._keep(); settle()
assert config.calls[-1][0] == PERSISTENT, "keeping stores the arrangement"
assert app.window is None, "the question closes once answered"
assert names.name_for(config.current.key, "") == "Home"

(home / ".config" / "monitors.xml").write_text("""<monitors version="2"><configuration>""" + "".join(
    f"<logicalmonitor><x>0</x><y>0</y><scale>1</scale><monitor><monitorspec><connector>{m.spec.connector}</connector>"
    f"<vendor>{m.spec.vendor}</vendor><product>{m.spec.product}</product><serial>{m.spec.serial}</serial></monitorspec>"
    f"<mode><width>1</width><height>1</height><rate>60</rate></mode></monitor></logicalmonitor>"
    for m in config.current.monitors) + "</configuration></monitors>")
app.arrive()
settle((application.ARRIVAL_LOOKS - 1) * application.ARRIVAL_LOOK_MS / 1000 + 0.5)
assert app.window is None, "an arrangement Luma knows is not asked about again"

app.activate(); settle()
assert app.window is not None and not app.window.arrival
assert app.window.title.get_label() == "Home"
app.window._finish(); settle()
print("PASS: Displays asked about a new arrangement, mirrored, extended, arranged, kept and named it")
