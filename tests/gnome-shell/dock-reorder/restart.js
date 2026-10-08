// SPDX-License-Identifier: GPL-2.0-or-later
// The second half of reorder.js: the Shell has restarted with the same
// settings, as after logging out and in. The dock must draw the order the
// first run saved, and favorite-apps must still hold it.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as L from '../shelf-arrange/lib.js';

const short = id => id.replace(/^org\.(projectluma|oracle)\./, '').replace(/\.desktop$/, '');

async function work() {
    await L.waitForShelf();
    let saved = null, start = null;
    try {
        ({start, saved} = JSON.parse(new TextDecoder().decode(GLib.file_get_contents(`${L.OUT}/saved-order.json`)[1])));
    } catch (e) {
        L.check('the first run saved an order to compare with', false, `${e}`);
        return;
    }
    const stored = new Gio.Settings({schema_id: 'org.gnome.shell'}).get_strv('favorite-apps').map(short);
    const pinned = Main.overview.dash._box.get_children()
        .filter(c => c.child?._delegate?.app && c.visible)
        .map(c => short(c.child._delegate.app.get_id()))
        .filter(n => stored.includes(n));
    L.log(`restart: saved ${saved} | stored ${stored} | drawn ${pinned}`);
    // An order nobody managed to change would survive a restart too.
    L.check('the first run rearranged the dock', saved.join() !== start.join(), `${start} -> ${saved}`);
    L.check('the rearranged order is still saved after a restart', stored.join() === saved.join(), `${stored}`);
    L.check('the dock draws the rearranged order after a restart', pinned.join() === saved.join(), `${pinned}`);
    await L.shot('dock-after-restart', L.rectOf(Main.shelf._dockIsland));
}

L.start('reorder-restart', work, 6000);
