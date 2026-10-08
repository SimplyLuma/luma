// Headless oracle for Shell 0186: the Shelf island and the notification cards
// lead with the sending app's full-colour icon, a picture only when the
// notification carries one, and a glyph (at glyph size) only when no app can
// be named. Cases: Charlie as it sends (desktop-entry + its icon), Charlie with
// a symbolic icon and a symbolic image file, Charlie under a loose name, a
// Prairie app with a sender picture, a Flatpak app through org.gtk.Notifications,
// a capsule app named only by its display name, and an unnamed sender.
//
// run.sh runs it in a container from the Shell oracle image with the built
// Shell installed, /oracle = the shared oracle tree (read-only) and /gon =
// this directory plus icons/ (the apps' real icons, copied from an install).
// Run without the patch as well: before 0186 every island mark is glyph16.
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import Clutter from 'gi://Clutter';
import St from 'gi://St';

const OUT = GLib.getenv('ORACLE_OUT');
const find = (root, pred, acc = []) => {
    for (const c of root.get_children()) {
        if (!c.visible) continue;
        if (pred(c)) acc.push(c);
        find(c, pred, acc);
    }
    return acc;
};
const has = (a, n) => a instanceof St.Widget && a.has_style_class_name?.(n);
const geom = a => { const [x, y] = a.get_transformed_position(); const [w, h] = a.get_transformed_size();
    return [Math.round(x), Math.round(y), Math.round(w), Math.round(h)]; };
const spawn = argv => Gio.Subprocess.new(argv, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE);
const fdo = spec => spawn(['python3', '/oracle/notif/notify.py', JSON.stringify(spec)]);
const gtk = (app, id, title, body, icon) => spawn(['python3', '/gon/gtknotify.py', app, id, title, body, icon]);
const SYMBOLIC_FILE = ['/usr/share/icons/Adwaita/symbolic/status/mail-unread-symbolic.svg',
    '/usr/share/icons/Adwaita/symbolic/actions/mail-message-new-symbolic.svg']
    .find(p => GLib.file_test(p, GLib.FileTest.EXISTS)) ?? '';
const AVATAR = '/gon/icons/capsule-chatgpt.png';

const CASES = [
    ['charlie', () => fdo({app: 'Charlie', icon: 'org.projectluma.Charlie', title: 'Kari Keefe', body: 'Re: Tech Central',
        hints: {'desktop-entry': 'org.projectluma.Charlie'}})],
    ['charlie-symbolic', () => fdo({app: 'Charlie', icon: 'mail-message-new-symbolic', title: 'Kari Keefe', body: 'Re: Tech Central',
        hints: {'desktop-entry': 'org.projectluma.Charlie', 'image-path': SYMBOLIC_FILE}})],
    ['charlie-loose', () => fdo({app: 'Charlie', icon: 'mail-message-new-symbolic', title: 'Kari Keefe', body: 'Re: Tech Central',
        hints: {'desktop-entry': 'charlie'}})],
    ['messages-avatar', () => fdo({app: 'Messages', icon: 'org.projectluma.Messages', title: 'Nora Feld', body: 'Ten minutes?',
        hints: {'desktop-entry': 'org.projectluma.Messages', 'image-path': AVATAR}})],
    ['flatpak', () => gtk('org.example.FlatpakApp', 'n1', 'Upload finished', 'Three files', 'media-playback-start-symbolic')],
    ['capsule', () => fdo({app: 'ChatGPT (Second)', icon: 'dialog-information-symbolic', title: 'Reply ready', body: 'Your answer is ready'})],
    ['unknown-glyph', () => fdo({app: 'Luma test', icon: 'dialog-information-symbolic', title: 'Backup finished', body: 'No app named'})],
];

const isGlyphName = g => g instanceof Gio.ThemedIcon && g.get_names().some(n => n.endsWith('-symbolic'));
const describe = icon => icon ? {gicon: icon.gicon?.to_string() ?? null, symbolic: icon.is_symbolic,
    glyphName: isGlyphName(icon.gicon), geom: geom(icon)} : null;

export default async function ({Main, Scripting, shot}) {
    const MessageTray = await import('resource:///org/gnome/shell/ui/messageTray.js');
    const sleep = ms => Scripting.sleep(ms);
    const tray = Main.messageTray;
    const seat = Clutter.get_default_backend().get_default_seat();
    const pointer = seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
    let flip = 0;
    const nudge = () => pointer.notify_absolute_motion(GLib.get_monotonic_time(), 40 + (flip++ % 2), 40);
    const beaconOf = () => find(Main.layoutManager.uiGroup, a => has(a, 'luma-notification-beacon'))[0] ?? null;
    const report = {beacon: [], tray: []};
    const procs = [];
    const clear = async () => {
        for (const s of tray.getSources())
            s.destroy(MessageTray.NotificationDestroyedReason.DISMISSED);
        await sleep(600);
    };
    // 1. The island (beacon) the newest notification shows in the Shelf, one
    // case at a time, in order, so a glyph case precedes an app case.
    for (const [name, send] of CASES) {
        await clear();
        procs.push(send());
        for (let i = 0; i < 25; i++) { nudge(); await sleep(100); }
        const bc = beaconOf();
        if (!bc) { report.beacon.push({name, beacon: null}); continue; }
        report.beacon.push({name, mark: bc._mark?.style_class, app: bc._newest?.source?.app?.get_id() ?? null,
            source: bc._newest?.source?.title ?? null, icon: describe(bc._markIcon)});
        const [x, y, w, h] = geom(bc);
        await shot(`beacon-${name}`, Math.max(0, x - 8), Math.max(0, y - 8), w + 16, h + 16);
    }
    // 2. The tray: every case waiting at once, as cards.
    await clear();
    for (const [, send] of CASES) { procs.push(send()); await sleep(400); }
    for (let i = 0; i < 20; i++) { nudge(); await sleep(100); }
    const bc = beaconOf();
    bc?.emit('clicked', 1);
    for (let i = 0; i < 15; i++) { nudge(); await sleep(100); }
    const cards = find(Main.layoutManager.uiGroup, a => has(a, 'luma-notification-card') && a.mapped);
    for (const card of cards) {
        const tile = find(card, a => has(a, 'luma-notification-tile'))[0];
        const src = card._header?.sourceIcon, img = card._tileImage;
        report.tray.push({title: card.notification?.title, source: card.notification?.source?.title,
            app: card.notification?.source?.app?.get_id() ?? null, tile: tile?.style_class,
            shows: src?.visible ? 'app' : img?.visible ? 'image' : 'none',
            icon: describe(src?.visible ? src : img)});
    }
    const trayActor = find(Main.layoutManager.uiGroup, a => has(a, 'luma-notification-tray') && a.mapped)[0];
    if (trayActor) {
        const [x, y, w, h] = geom(trayActor);
        await shot('tray', Math.max(0, x - 8), Math.max(0, y - 8), w + 16, h + 16);
    }
    GLib.file_set_contents(`${OUT}/icons.json`, JSON.stringify(report, null, 1));
    for (const p of procs) p.force_exit();
}
