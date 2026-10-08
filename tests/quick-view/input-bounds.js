/* Negative packaged renderer tests; fixture directory is provided by the runner. */
imports.gi.versions.Gtk = '3.0';
imports.gi.versions.GtkSource = '4';
const {Gio, GLib, Gtk} = imports.gi;
Gio.Resource.load('/usr/share/sushi/org.gnome.NautilusPreviewer.src.gresource')._register();
imports.searchPath.unshift('resource:///org/gnome/NautilusPreviewer/js');
Gtk.init(null);
const Renderer = imports.viewers.html;
const directory = ARGV[0];
const invalid = `${directory}/invalid.html`;
const large = `${directory}/large.html`;
const nul = `${directory}/nul.html`;
GLib.file_set_contents(invalid, new Uint8Array([0xff, 0xfe, 0xff]));
GLib.file_set_contents(large, new Uint8Array(8 * 1024 * 1024 + 1).fill(65));
GLib.file_set_contents(nul, new Uint8Array([65, 0, 66]));
let remaining = 4;
for (const path of [invalid, large, nul, directory]) {
    const info = new Gio.FileInfo(); info.set_content_type('text/html');
    const view = new Renderer.Klass(Gio.File.new_for_path(path), info);
    GLib.timeout_add_seconds(0, 2, () => {
        if (view.get_visible_child_name() !== 'error') {
            printerr(`FAIL: invalid input not rejected: ${path}`); imports.system.exit(1);
        }
        view.destroy();
        if (!--remaining) { print('PASS: invalid UTF-8, NUL, oversized and directory inputs rejected'); Gtk.main_quit(); }
        return GLib.SOURCE_REMOVE;
    });
}
Gtk.main();
for (const path of [invalid, large, nul]) GLib.unlink(path);
