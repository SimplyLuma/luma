// SPDX-License-Identifier: GPL-2.0-or-later
// Run in the same disposable installed-Shell fixture as creator-native-followup.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import St from 'gi://St';
import * as AppDisplay from 'resource:///org/gnome/shell/ui/appDisplay.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as Scripting from 'resource:///org/gnome/shell/ui/scripting.js';
export async function run() {
    function check(value, message) {
        if (!value) throw new Error(message);
        print(`PASS ${message}`);
    }
    const app = Shell.AppSystem.get_default().lookup_app('org.projectluma.Disks.Preview.desktop');
    check(!!app, 'actual Disks Preview launcher exists');
    const gicon = app.get_app_info().get_icon();
    const info = new St.IconTheme().lookup_by_gicon(gicon, 64, St.IconLookupFlags.FORCE_SIZE);
    print(`ACTUAL native Disks icon ${info?.get_filename()}`);
    check(info?.get_filename() === '/usr/share/icons/Prairie/scalable/apps/org.projectluma.Disks.Preview.svg',
        'actual launcher resolves approved Prairie artwork before old hicolor');
    const [,alias] = GLib.file_get_contents(info.get_filename());
    const [,canonical] = GLib.file_get_contents('/usr/share/icons/Prairie/scalable/apps/org.projectluma.Disks.svg');
    const digest = bytes => GLib.compute_checksum_for_bytes(GLib.ChecksumType.SHA256, GLib.Bytes.new(bytes));
    check(digest(alias) === digest(canonical), 'alias uses exact canonical artwork without another rim or mask');
    const pixbuf = info.load_icon();
    check(pixbuf.get_width() === 64 && pixbuf.get_height() === 64 && pixbuf.get_has_alpha(),
        'native renderer produces square64 RGBA canvas');
    const pixels = pixbuf.get_pixels();
    check(pixels[3] === 0, 'rounded family artwork keeps a transparent corner');
    const item = new AppDisplay.AppIcon(app);
    item.icon.setIconSize(64);
    const box = new St.BoxLayout({style: 'padding:20px; background-color:#f1f3f5;',x:80,y:100});
    box.add_child(item); Main.uiGroup.add_child(box);
    await Scripting.sleep(500);
    check(item.icon.icon.width === 64 && item.icon.icon.height === 64,
        'actual drawer renders corrected icon on64px square canvas');
    const stream = Gio.File.new_for_path('/var/tmp/luma-shell100-native/captures/disks-preview36.png')
        .replace(null, false, Gio.FileCreateFlags.NONE, null);
    try { await new Shell.Screenshot().screenshot(false, stream); }
    finally { stream.close(null); box.destroy(); }
    print('NATIVE DISKS PREVIEW36 PASS 6 assertions');
}
