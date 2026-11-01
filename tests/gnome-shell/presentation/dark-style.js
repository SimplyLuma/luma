// SPDX-License-Identifier: Apache-2.0
// Native Quick Options regression: explicit treatment and GNOME preference agree.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as SurfaceMaterials from 'resource:///org/gnome/shell/ui/lumaSurfaceMaterials.js';
const sleep = ms => new Promise(r => GLib.timeout_add_once(GLib.PRIORITY_DEFAULT, ms, r));
const results = [];
function check(name, ok, detail = null) {results.push({name, ok: Boolean(ok), detail}); if (!ok) throw new Error(name);}
async function run() {
    const p = Main.presentation, snapshot = p._state, q = p._handheldOptions;
    const button = Main.panel.statusArea.quickSettings._darkMode.quickSettingsItems[0];
    const settings = new Gio.Settings({schema_id: 'org.project_luma.shell-state'});
    const desktop = new Gio.Settings({schema_id: 'org.gnome.desktop.interface'});
    const treatment = settings.get_user_value('surface-treatment'), scheme = desktop.get_user_value('color-scheme');
    const touch = global.stage.context.get_backend().get_default_seat().create_virtual_device(Clutter.InputDeviceType.TOUCHSCREEN_DEVICE);
    const tap = async () => {
        const [x, y] = button.get_transformed_position(), [w, h] = button.get_transformed_size();
        touch.notify_touch_down(GLib.get_monotonic_time(), 0, x + w / 2, y + h / 2);await sleep(50);
        touch.notify_touch_up(GLib.get_monotonic_time(), 0);await sleep(450);
    };
    try {
        Main.lumaSearch.close();q.menu.close(0);await sleep(300);
        for (const mode of ['mobile', 'desktop']) {
            q.menu.close(0);p._snapshot({...snapshot, presentation: {...snapshot.presentation, effective: mode}});await sleep(300);
            settings.set_string('surface-treatment', 'light');desktop.set_string('color-scheme', 'prefer-dark');await sleep(300);
            check(`${mode}: indicator follows explicit Light despite GNOME dark preference`, !button.checked && SurfaceMaterials.effectiveTreatment() === 'light');
            q.menu.open();await sleep(450);
            check(`${mode}: real Dark Style button is mapped`, button.mapped);
            await tap();
            check(`${mode}: touch enables dark treatment and app preference`, settings.get_string('surface-treatment') === 'dark' && desktop.get_string('color-scheme') === 'prefer-dark');
            check(`${mode}: Shell and search follow dark`, SurfaceMaterials.effectiveTreatment() === 'dark' && button.checked &&
                Main.lumaSearch._dialog._fieldIsland.has_style_class_name('luma-surface-dark'));
            await tap();
            check(`${mode}: touch restores light treatment and app preference`, settings.get_string('surface-treatment') === 'light' && desktop.get_string('color-scheme') === 'default');
            check(`${mode}: indicator and Shell return light`, !button.checked && SurfaceMaterials.effectiveTreatment() === 'light');
            q.menu.close(0);await sleep(150);
        }
        settings.set_string('surface-treatment', 'glass');await sleep(250);
        check('External appearance changes update the indicator', !button.checked && SurfaceMaterials.effectiveTreatment() === 'glass');
        settings.reset('surface-treatment');desktop.set_string('color-scheme', 'prefer-dark');await sleep(250);
        check('Unspecified treatment still follows GNOME dark preference', button.checked && SurfaceMaterials.effectiveTreatment() === 'dark');
        desktop.set_string('color-scheme', 'default');await sleep(250);
        check('Unspecified treatment still follows GNOME light preference', !button.checked && SurfaceMaterials.effectiveTreatment() === 'light');
    } finally {
        q.menu.close(0);touch.run_dispose();
        if (treatment) settings.set_value('surface-treatment', treatment);else settings.reset('surface-treatment');
        if (scheme) desktop.set_value('color-scheme', scheme);else desktop.reset('color-scheme');
        p._snapshot(snapshot);await sleep(350);p.showHome();q.open();
    }
    check('Test preserves whether the original treatment was unset', Boolean(settings.get_user_value('surface-treatment')) === Boolean(treatment));
}
GLib.timeout_add_once(GLib.PRIORITY_DEFAULT,2400,()=>run().catch(e=>results.push({name:'Native test error',ok:false,detail:String(e.stack??e)}))
    .finally(()=>GLib.file_set_contents('/tmp/dark-style.json',JSON.stringify({results}))));
