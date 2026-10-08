/* Packaged Markdown regression. Run in a normal sandbox-capable Linux session. */
imports.gi.versions.Gtk = '3.0';
imports.gi.versions.GtkSource = '4';
const {Gio, GLib, Gtk} = imports.gi;
const resource = Gio.Resource.load('/usr/share/sushi/org.gnome.NautilusPreviewer.src.gresource');
resource._register();
imports.searchPath.unshift('resource:///org/gnome/NautilusPreviewer/js');
Gtk.init(null);
const file = Gio.File.new_for_path(ARGV[0]);
const info = file.query_info('standard::content-type', 0, null);
const view = new imports.viewers.html.Klass(file, info);
const window = new Gtk.Window({default_width: 720, default_height: 600});
window.add(view); window.show_all();
let checked = false;
view._web.connect('load-changed', (_web, event) => {
    if (event !== imports.gi.WebKit2.LoadEvent.FINISHED || checked) return;
    const main = view._web.get_main_resource();
    if (!main) return;
    main.get_data(null, (resource, result) => {
        try {
            const html = imports.byteArray.toString(resource.get_data_finish(result));
            if (!html.includes('<h1>Quick View test</h1>')) throw new Error('Markdown heading missing');
            if (!html.includes('<table>')) throw new Error('GFM table missing');
            if (!html.includes('type="checkbox"')) throw new Error('GFM task list missing');
            const source = view._buffer.text;
            if (!source.startsWith('# Quick View test')) throw new Error('Source missing');
            view.viewSwitch.get_children()[1].clicked();
            if (view.get_visible_child_name() !== 'source') throw new Error('Source switch failed');
            view.viewSwitch.get_children()[0].clicked();
            if (view.get_visible_child_name() !== 'rendered') throw new Error('Preview switch failed');
            checked = true;
            print('PASS: sandboxed GFM headings/table/task list; selectable source switch');
            window.destroy(); Gtk.main_quit();
        } catch (e) { printerr(e); imports.system.exit(1); }
    });
});
GLib.timeout_add_seconds(0, 15, () => {
    printerr('FAIL: Markdown did not render: ' + view.get_visible_child_name());
    imports.system.exit(1);
});
Gtk.main();
