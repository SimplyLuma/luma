// SPDX-License-Identifier: Apache-2.0
// Exercise the packaged Shell owners without the development adapter.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
const results = [];
const sleep = ms => new Promise(resolve => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms,
    () => { resolve(); return GLib.SOURCE_REMOVE; }));
function check(name, ok, detail = '') { results.push({name, ok: Boolean(ok), detail}); }
async function work() {
    check('native Dash and presentation initialize', Main.shelf && Main.presentation);
    if (!Main.shelf || !Main.presentation) return;
    const p = Main.presentation;
    const menu = Main.panel.statusArea.quickSettings.menu;
    const ui = Main.shelf.dockFolders?._desktopUI;
    check('native folder presentation initializes', ui && menu._surfaceLayout);
    if (!ui) return;
    const mode = async value => {
        p._call('SetPresentation', new GLib.Variant('(s)', [value]));
        await sleep(1800);
        check(`service applies ${value}`, p.mode === value);
    };
    await mode('desktop');
    ui._drawer.open(); await sleep(600);
    const drawer = ui._drawer;
    check('real installed app catalog spans pages', drawer.visible && drawer._state.pages > 1,
        `${drawer._model?.items.length} apps / ${drawer._state?.pages} pages`);
    drawer._dots.get_children()[1]?.emit('clicked', 1); await sleep(300);
    check('page control advances the real app drawer', drawer._page === 1 && drawer._tiles.length > 0 && drawer._tiles.length <= 28);
    drawer._search.set_text('Grid fixture 64'); await sleep(300);
    check('search filters the real app catalog', drawer._state.matches.length === 1 && drawer._tiles.length === 1);
    drawer.close();
    for (const value of ['tablet', 'mobile', 'desktop']) {
        await mode(value);
        menu.open(); await sleep(300);
        check(`${value} retains visible native options`, menu.isOpen && menu._mainPage.mapped && menu._mainPage.opacity === 255);
        check(`${value} displays its Experience label`, p._options.title.toLowerCase() === value);
        menu.close();
    }
    check('Desktop restores the native launcher', ui.enabled && ui._button.visible);
    const model = [...Main.shelf.dockFolders._models.values()].find(model => !model.isApplications);
    check('default folders include Downloads', model && Main.shelf.dockFolders.uris.some(uri => uri.endsWith('/Downloads')));
    if (model) {
        Main.shelf.dockFolders.open(model); await sleep(400);
        check('empty folder uses the native folder panel', ui.panelActive && model.items.length === 0);
        Main.shelf.dockFolders.close();
    }
}
export function init() {
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, 6000, () => {
        work().catch(error => check('scenario completes', false, `${error}\n${error.stack}`)).finally(() => {
            GLib.file_set_contents(`${GLib.getenv('PRESENTATION_OUT')}/results.json`, JSON.stringify(results, null, 2));
            global.context.terminate();
        });
        return GLib.SOURCE_REMOVE;
    });
}
export async function run() { await new Promise(() => {}); }
