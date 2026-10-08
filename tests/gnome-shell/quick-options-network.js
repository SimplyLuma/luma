// SPDX-License-Identifier: GPL-2.0-or-later
// Semantic network visibility check on the exact packaged source. Native
// compositor allocation/input/prompt checks are separate quick-options-native.js.
import Gio from 'gi://Gio';
const [loaded, bytes] = Gio.File.new_for_path(ARGV[0]).load_contents(null);
if (!loaded) throw new Error('Cannot read NetworkManager Shell source');
const source = new TextDecoder().decode(bytes);
const start = source.indexOf('class NMWirelessDeviceItem');
const begin = source.indexOf('    _updateItemsVisibility() {', start);
const end = source.indexOf('\n    }', begin);
if (start < 0 || begin < 0 || end < 0) throw new Error('Missing actual wireless visibility method');
const body = source.slice(source.indexOf('{', begin) + 1, end);
const update = new Function('Main', 'MAX_VISIBLE_NETWORKS', body);
for (const hasWindows of [true, false]) {
    let signals = 0;
    const rows = Array.from({length: 30}, (_, i) => ({
        visible: false,
        network: {hasConnections: () => i === 12, canAutoconnect: () => i === 20},
    }));
    const actor = {_itemSorter: rows, emit(name) {
        if (name !== 'networks-changed') throw new Error('Wrong visibility signal');
        signals++;
    }};
    update.call(actor, {sessionMode: {hasWindows}}, 8);
    const expected = hasWindows ? 30 : 2;
    if (rows.filter(row => row.visible).length !== expected || actor.nVisibleNetworks !== expected)
        throw new Error(`eligible networks hidden: expected ${expected}, got ${actor.nVisibleNetworks}`);
    if (signals !== 1) throw new Error('No visibility update emitted');
}
print('PASS: all30 desktop fixture networks retained; greeter eligibility preserved');
