// SPDX-License-Identifier: Apache-2.0
// Run in a native development session; all appearance/presentation changes restore.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
const sleep = ms => new Promise(r => GLib.timeout_add_once(GLib.PRIORITY_DEFAULT, ms, r));
const results = [];
function check(name, ok, detail = null) {results.push({name, ok: Boolean(ok), detail}); if (!ok) throw new Error(name);}
async function shot(name) {
    const path = `${GLib.get_user_state_dir()}/project-titan/${name}.png`;
    const f = Gio.File.new_for_path(path).replace(null, false, Gio.FileCreateFlags.PRIVATE, null);
    await new Shell.Screenshot().screenshot(false, f); f.close(null);
}
async function run() {
    const p = Main.presentation, snapshot = p._state, dialog = Main.lumaSearch._dialog;
    const settings = new Gio.Settings({schema_id: 'org.project_luma.shell-state'});
    const a11y = new Gio.Settings({schema_id: 'org.gnome.desktop.a11y.interface'});
    const reduce = settings.get_user_value('reduce-transparency'), high = a11y.get_user_value('high-contrast');
    const treatment = settings.get_user_value('surface-treatment');
    const reloadStyle = () => {
        const path = GLib.getenv('LUMA_SEARCH_MATERIAL_CSS');
        if (path) St.ThemeContext.get_for_stage(global.stage).get_theme().load_stylesheet(Gio.File.new_for_path(path));
    };
    const surfaces = [dialog._fieldIsland, dialog._intro, dialog._results];
    const ink = actor => actor.get_theme_node().get_background_color();
    try {
        settings.set_string('surface-treatment', 'dark');
        settings.set_boolean('reduce-transparency', false); a11y.set_boolean('high-contrast', false);await sleep(350);reloadStyle();
        for (const mode of ['mobile', 'desktop']) {
            Main.lumaSearch.close(); await sleep(220);
            p._snapshot({...snapshot, presentation: {...snapshot.presentation, effective: mode}});
            await sleep(250); Main.lumaSearch.open(); await sleep(450);
            check(`${mode}: shared search mapped`, p.mode === mode && dialog.mapped);
            check(`${mode}: one binding per surface`, dialog._surfaceBindings.length === 3 &&
                surfaces.every(a => a.get_effect('luma-surface-backdrop')));
            check(`${mode}: field and intro use dock veil`, surfaces.slice(0, 2).every(a => {
                const c = ink(a);return c.red === 32 && c.green === 33 && c.blue === 36 && Math.abs(c.alpha - 166) <= 1;
            }), surfaces.slice(0, 2).map(a => ink(a).to_string()));
            const m = Main.layoutManager.primaryMonitor;
            check(`${mode}: field fits display`, dialog._fieldIsland.width > 0 && dialog._fieldIsland.width <= m.width);
            await shot(`search-material-${mode}-intro`);
            dialog._entry.text = 'weather'; await sleep(900);
            check(`${mode}: result surface is mapped`, dialog._results.mapped && !dialog._intro.visible);
            check(`${mode}: results use same veil`, Math.abs(ink(dialog._results).alpha - 166) <= 1);
            check(`${mode}: actionable app results remain`, Boolean(dialog._results._defaultResult));
            await shot(`search-material-${mode}-results`);
            const effects = surfaces.map(a => a.get_effect('luma-surface-backdrop'));
            check(`${mode}: existing backdrop renderer active`, effects.every(e => e.enabled && e.radius > 0));
        }
        for (const [appearance, alpha] of [['light', 255], ['glass', 217], ['frost', 166]]) {
            settings.set_string('surface-treatment', appearance); await sleep(350);reloadStyle();await sleep(150);
            check(`${appearance}: search palette follows shared material`, surfaces.every(a => Math.abs(ink(a).alpha - alpha) <= 1));
            check(`${appearance}: search text remains contrasting`, surfaces.every(a => {
                const c = a.get_theme_node().get_foreground_color();return appearance === 'frost' ? c.red > 220 : c.red < 80;
            }));
        }
        settings.set_string('surface-treatment', 'dark');await sleep(350);reloadStyle();
        settings.set_boolean('reduce-transparency', true); await sleep(350);reloadStyle();
        check('Reduce transparency makes every search surface opaque', surfaces.every(a => ink(a).alpha === 255));
        check('Reduce transparency disables every search blur', surfaces.every(a => !a.get_effect('luma-surface-backdrop').enabled));
        settings.set_boolean('reduce-transparency', false); a11y.set_boolean('high-contrast', true); await sleep(350);reloadStyle();
        check('High contrast keeps search opaque', surfaces.every(a => ink(a).alpha === 255));
        check('High contrast disables search blur', surfaces.every(a => !a.get_effect('luma-surface-backdrop').enabled));
        a11y.set_boolean('high-contrast', false); await sleep(350);
        Main.lumaSearch.close(); await sleep(250);
        check('Closing search unmaps all material surfaces', surfaces.every(a => !a.mapped));
    } finally {
        if (treatment) settings.set_value('surface-treatment', treatment);
        else settings.reset('surface-treatment');
        if (reduce) settings.set_value('reduce-transparency', reduce); else settings.reset('reduce-transparency');
        if (high) a11y.set_value('high-contrast', high); else a11y.reset('high-contrast');
        Main.lumaSearch.close();p._snapshot(snapshot);await sleep(300);reloadStyle();p.showHome();Main.lumaSearch.open();
    }
    const same = (current, expected) => expected === null ? current === null : Boolean(current?.equal(expected));
    check('Appearance test restores explicit or unset treatment', same(settings.get_user_value('surface-treatment'), treatment));
    check('Appearance test restores explicit or unset transparency', same(settings.get_user_value('reduce-transparency'), reduce));
    check('Appearance test restores explicit or unset contrast', same(a11y.get_user_value('high-contrast'), high));
}
GLib.timeout_add_once(GLib.PRIORITY_DEFAULT,2400,()=>run().catch(e=>results.push({name:'Native test error',ok:false,detail:String(e.stack??e)}))
    .finally(()=>GLib.file_set_contents('/tmp/search-material.json',JSON.stringify({results}))));
