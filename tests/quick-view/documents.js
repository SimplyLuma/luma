/* Runtime contract: run under Xvfb as an ordinary user, with packaged resources. */
imports.gi.versions.Gtk = '3.0';
imports.gi.versions.GtkSource = '4';
const {Gio, GLib, Gtk} = imports.gi;
const resource = Gio.Resource.load('/usr/share/sushi/org.gnome.NautilusPreviewer.src.gresource');
resource._register();
imports.searchPath.unshift('resource:///org/gnome/NautilusPreviewer/js');
Gtk.init(null);
const Document = imports.viewers.html;
const file = Gio.File.new_for_path(ARGV[0]);
const info = file.query_info('standard::content-type', 0, null);
const view = new Document.Klass(file, info);
const window = new Gtk.Window({default_width: 720, default_height: 600});
window.add(view);
window.show_all();
let loaded = false;
view._web.connect('notify::title', () => {
    if (!view._web.get_title()) return;
    loaded = true;
    if (view._web.get_title() !== 'Unchanged') throw new Error('Script changed title or page failed');
    if (view._web.get_settings().enable_javascript) throw new Error('Scripts enabled');
    const before = window.get_size().join(',');
    view.viewSwitch.get_children()[1].clicked();
    if (view.get_visible_child_name() !== 'source') throw new Error('Source switch failed');
    view.viewSwitch.get_children()[0].clicked();
    if (view.get_visible_child_name() !== 'rendered') throw new Error('Page switch failed');
    if (window.get_size().join(',') !== before) throw new Error('Switch resized window');
    GLib.timeout_add(0, 1000, () => {
        print('PASS: document rendered, scripts off, stable source switching');
        window.destroy(); Gtk.main_quit(); return GLib.SOURCE_REMOVE;
    });
});
GLib.timeout_add_seconds(0, 15, () => {
    { printerr('FAIL: no document loaded: title=' + view._web.get_title() + ' ' + view.get_visible_child_name() + ' ' + view.get_children().map(c => c.label).join('|'));  imports.system.exit(1); }
    return GLib.SOURCE_REMOVE;
});
Gtk.main();
