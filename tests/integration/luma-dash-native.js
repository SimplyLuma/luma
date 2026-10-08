import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Clutter from 'gi://Clutter';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as Scripting from 'resource:///org/gnome/shell/ui/scripting.js';

function equal(actual, expected, label) {
    if (actual !== expected)
        throw new Error(`${label}: actual ${actual}, expected ${expected}`);
}
function pixels(actual, expected, label) {
    if (!Number.isFinite(actual) || !Number.isFinite(expected) || Math.abs(actual - expected) > 1)
        throw new Error(`${label}: actual ${actual}, expected ${expected}`);
}
export function init() {
    if (GLib.getenv('LUMA_DASH_DIRECT') !== '1')
        return;
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, 2000, () => {
        runChecks().catch(error => console.error(error.stack)).finally(() => global.context.terminate());
        return GLib.SOURCE_REMOVE;
    });
}
export async function run() {
    if (GLib.getenv('LUMA_DASH_DIRECT') !== '1')
        await runChecks();
}
async function runChecks() {
    console.log('START_NATIVE_DASH');
    const settings = new Gio.Settings({schema_id: 'org.project_luma.shell-state'});
    new Gio.Settings({schema_id: 'org.gnome.desktop.interface'})
        .set_boolean('enable-animations', false);
    await Scripting.sleep(200);
    if (!Main.shelf)
        throw new Error('Native Dash not constructed');
    let cases = 0;
    for (const edge of ['bottom', 'top', 'left', 'right']) {
        for (const protrude of [false, true]) {
            for (const span of [false, true]) {
                for (const islands of [false, true]) {
                    settings.set_string('shelf-edge', edge);
                    settings.set_string('shelf-edge-mode', protrude ? 'protruding' : 'floating');
                    settings.set_boolean('shelf-span-full', span);
                    settings.set_boolean('shelf-float-ends', true);
                    settings.set_string('shelf-surface-mode', islands ? 'separate' : 'connected');
                    // Observe actual Mutter state after queued layout/strut work;
                    // do not depend on unrelated whole-Shell performance-helper quiescence.
                    await Scripting.sleep(300);
                    const monitor = Main.layoutManager.primaryMonitor;
                    const work = global.workspace_manager.get_active_workspace()
                        .get_work_area_for_monitor(monitor.index);
                    const thickness = 50 * St.ThemeContext.get_for_stage(global.stage).scale_factor;
                    const reserve = protrude && span ? thickness : 0;
                    pixels(work.x, monitor.x + (edge === 'left' ? reserve : 0), 'work-area x');
                    pixels(work.y, monitor.y + (edge === 'top' ? reserve : 0), 'work-area y');
                    pixels(work.width, monitor.width - (edge === 'left' || edge === 'right' ? reserve : 0), 'work-area width');
                    pixels(work.height, monitor.height - (edge === 'top' || edge === 'bottom' ? reserve : 0), 'work-area height');
                    const vertical = edge === 'left' || edge === 'right';
                    pixels(vertical ? Main.shelf.width : Main.shelf.height, thickness, 'Dash thickness');
                    equal(Main.shelf._dash._box.layout_manager.orientation,
                        vertical ? Clutter.Orientation.VERTICAL : Clutter.Orientation.HORIZONTAL,
                        'native dock orientation');
                    pixels(Main.shelf._surface.translation_x, 0, 'reduced-motion x');
                    pixels(Main.shelf._surface.translation_y, 0, 'reduced-motion y');
                    for (const [actor, ownsSurface] of [[Main.shelf._surface, !islands],
                        ...Main.shelf._group.get_children().map(actor => [actor, islands])]) {
                        equal(actor._stroke.visible, ownsSurface, 'stroke ownership');
                        for (const shadow of actor._shadows)
                            equal(shadow.visible, ownsSurface && !protrude && !span, 'shadow policy');
                    }
                    cases++;
                    console.log('NATIVE_DASH_CASE', cases, edge, protrude, span, islands);
                }
            }
        }
    }
    console.log(`PASS ${cases} native Dash cases: Mutter work areas, native orientation, thickness, reduced motion`);
}
export function finish() {}
