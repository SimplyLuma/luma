// SPDX-License-Identifier: Apache-2.0
// Which desktop entries Home leaves out; runs under gjs -m or node.
import {hiddenFromHome} from '../../../src/luma-shell-presentation/layout.js';
function assert(value, message) { if (!value) throw Error(message); }

for (const [id, name] of [['org.gnome.Settings.desktop', 'Settings'], ['org.gnome.Nautilus.desktop', 'Filer'],
    ['viola-browser.desktop', 'Viola'], ['org.projectluma.Depot.desktop', 'Depot'],
    ['org.projectluma.Terminal.desktop', 'Terminal'], ['org.projectluma.Calendar.desktop', 'Calendar']])
    assert(!hiddenFromHome(id, name), `${id} shows on Home`);
for (const [id, name] of [['org.gnome.Settings.Display.desktop', 'Displays'], ['org.gnome.Shell.Extensions.desktop', 'Extensions'],
    ['luma-search-files.desktop', 'Files search'], ['search.provider.desktop', 'Provider'],
    ['org.projectluma.SearchSettings.desktop', ' Search ']])
    assert(hiddenFromHome(id, name), `${id} stays off Home`);
(globalThis.print ?? console.log)('PASS Home catalog: Settings shows, its panels, Extensions and search entries do not');
