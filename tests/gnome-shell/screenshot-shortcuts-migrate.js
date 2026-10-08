import GLib from "gi://GLib";
import Gio from "gi://Gio";
const M = await import(`file://${ARGV[0]}`);
let failures = 0;
const check = (name, a, e) => { if (JSON.stringify(a) !== JSON.stringify(e)) { failures++; printerr(`FAIL ${name}: ${JSON.stringify(a)} != ${JSON.stringify(e)}`); } };
check("normalize", M.normalize("<Ctrl><Shift>1"), M.normalize("<Shift><Control>1"));
check("primary", M.normalize("<Primary><Shift>R"), M.normalize("<Control><Shift>r"));
const s = new Gio.Settings({schema_id: "org.gnome.shell.keybindings"});
// Nick: the values set by hand; another person: a custom binding; a third: GNOME defaults set explicitly.
s.set_strv("show-screenshot-ui", ["<Control><Shift>1"]);
s.set_strv("screenshot-window", ["<Super>w"]);
s.set_strv("show-screen-recording-ui", ["<Ctrl><Shift><Alt>R"]);
s.set_value("screenshot", new GLib.Variant("as", []));
// The old default for a selection, set explicitly.
s.set_strv("show-screenshot-ui-area", ["<Ctrl><Shift>3"]);
const reset = M.migrate(s);
check("reset keys", reset.sort(), ["screenshot", "show-screen-recording-ui", "show-screenshot-ui", "show-screenshot-ui-area"]);
check("custom kept", s.get_strv("screenshot-window"), ["<Super>w"]);
// Each shortcut opens its own mode: 1 a selection, 3 the entire screen, and
// the tool on its own has none.
check("defaults applied", [s.get_strv("show-screenshot-ui"), s.get_strv("show-screen-recording-ui"), s.get_strv("screenshot"),
      s.get_strv("show-screenshot-ui-area"), s.get_strv("show-screenshot-ui-screen")],
      [[], ["<Ctrl><Shift>4"], [], ["<Ctrl><Shift>1"], ["<Ctrl><Shift>3"]]);
print(failures ? `migration: ${failures} failure(s)` : "migration: PASS");
