#!/usr/bin/gjs -m
// A GTK 4 window that accepts any drop and logs what it was offered and what
// it read: droptarget.js LOG TITLE. GDK_BACKEND=x11 runs it under Xwayland.
import Gdk from 'gi://Gdk?version=4.0';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Gtk from 'gi://Gtk?version=4.0';

const [logPath, title] = ARGV;
const write = obj => {
    const stream = Gio.File.new_for_path(logPath).append_to(Gio.FileCreateFlags.NONE, null);
    stream.write_all(new TextEncoder().encode(`${JSON.stringify(obj)}\n`), null);
    stream.close(null);
};
const read = (drop, mime) => new Promise(resolve => {
    drop.read_async([mime], GLib.PRIORITY_DEFAULT, null, (d, res) => {
        let stream;
        try {
            [stream] = d.read_finish(res);
        } catch (e) {
            resolve({error: `${e}`});
            return;
        }
        const out = Gio.MemoryOutputStream.new_resizable();
        out.splice_async(stream, Gio.OutputStreamSpliceFlags.CLOSE_SOURCE | Gio.OutputStreamSpliceFlags.CLOSE_TARGET,
            GLib.PRIORITY_DEFAULT, null, (o, r) => {
                try {
                    o.splice_finish(r);
                    const bytes = out.steal_as_bytes();
                    resolve({size: bytes.get_size(),
                        text: mime.startsWith('image/') ? null : new TextDecoder().decode(bytes.toArray()).slice(0, 300)});
                } catch (e) {
                    resolve({error: `${e}`});
                }
            });
    });
});

const app = new Gtk.Application({application_id: `org.oracle.Drop${title}`, flags: Gio.ApplicationFlags.NON_UNIQUE});
app.connect('activate', () => {
    const win = new Gtk.ApplicationWindow({application: app, title, default_width: 700, default_height: 500});
    const label = new Gtk.Label({label: `Drop here (${title})`});
    win.set_child(label);
    const target = new Gtk.DropTargetAsync({actions: Gdk.DragAction.COPY});
    target.connect('accept', () => true);
    target.connect('drag-enter', () => {
        write({event: 'enter', title});
        return Gdk.DragAction.COPY;
    });
    target.connect('drag-motion', () => Gdk.DragAction.COPY);
    target.connect('drop', (_t, drop) => {
        const formats = drop.get_formats().get_mime_types() ?? [];
        write({event: 'drop-start', title, formats});
        (async () => {
            const got = {};
            for (const mime of ['text/uri-list', 'x-special/gnome-copied-files', 'image/png'])
                if (formats.includes(mime))
                    got[mime] = await read(drop, mime);
            write({event: 'drop', title, backend: Gdk.Display.get_default().constructor.name, formats, got});
            drop.finish(Gdk.DragAction.COPY);
        })().catch(e => write({event: 'drop-error', title, error: `${e}`}));
        return true;
    });
    label.add_controller(target);
    win.present();
    write({event: 'ready', title, backend: Gdk.Display.get_default().constructor.name});
});
app.run([]);
