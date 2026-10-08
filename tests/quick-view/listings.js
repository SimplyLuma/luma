/* Packaged renderer check in the disposable GUI session. */
imports.gi.versions.Gtk = '3.0';
const {Gio, GLib, Gtk} = imports.gi;
const resource = Gio.Resource.load('/usr/share/sushi/org.gnome.NautilusPreviewer.src.gresource');
resource._register();
imports.searchPath.unshift('resource:///org/gnome/NautilusPreviewer/js');
Gtk.init(null);
const Listing = imports.viewers.listing;
const file = Gio.File.new_for_path(ARGV[0]);
const info = file.query_info('standard::*,time::modified', 0, null);
const view = new Listing.Klass(file, info);
const window = new Gtk.Window({default_width: 520, default_height: 280});
window.add(view); window.show_all();
GLib.timeout_add(0, 3000, () => {
    try {
        if (view._summary.get_text().includes('There is no preview') || view._summary.get_text().includes('Preparing'))
            throw new Error(view._summary.get_text());
        const rows = view._list.get_children();
        if (!rows.length) throw new Error('No rows');
        if (view._folder) {
            const shown = rows.filter(row => row.get_visible());
            if (shown.length >= rows.length) throw new Error('Folder overflow was not hidden');
            if (!view._more.get_text().startsWith('and ')) throw new Error('No more count');
            print('PASS folder: ' + view._summary.get_text() + '; ' + view._more.get_text());
        } else {
            print('PASS archive: ' + view._summary.get_text() + '; rows=' + rows.length);
        }
        window.destroy(); Gtk.main_quit();
    } catch (error) {
        printerr('FAIL: ' + error.message); imports.system.exit(1);
    }
    return GLib.SOURCE_REMOVE;
});
Gtk.main();
