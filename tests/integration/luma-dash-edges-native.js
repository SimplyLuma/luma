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
        runChecks().catch(error => console.error(`${error.message}\n${error.stack}`)).finally(() => global.context.terminate());
        return GLib.SOURCE_REMOVE;
    });
}
export async function run() {
    if (GLib.getenv('LUMA_DASH_DIRECT') === '1') {
        // The init hook owns completion and terminates this private compositor.
        // Keep the perf runner alive until then; otherwise its helper cleanup
        // can race the scheduled checks before they have even started.
        await new Promise(() => {});
    } else {
        await runChecks();
    }
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
                for (const floatEnds of [false, true]) {
                for (const islands of [false, true]) {
                    settings.set_string('shelf-edge', edge);
                    settings.set_string('shelf-edge-mode', protrude ? 'protruding' : 'floating');
                    settings.set_boolean('shelf-span-full', span);
                    settings.set_boolean('shelf-float-ends', floatEnds);
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
                    const surfaces = islands ? Main.shelf._group.get_children().filter(actor => actor.mapped) : [Main.shelf._surface];
                    for (const island of surfaces) {
                        console.log('ISLAND_BOUNDS',island.style_class,JSON.stringify(island.get_transformed_position()),JSON.stringify([island.x,island.y,island.width,island.height,island.mapped]));
                        const [ix, iy] = island.get_transformed_position();
                        const [iw, ih] = island.get_transformed_size();
                        for (const painted of [island._content, island._stroke, ...island._shadows].filter(actor => actor.visible)) {
                            const [x,y] = painted.get_transformed_position();
                            const [w,h] = painted.get_transformed_size();
                            pixels(x,ix,'painted surface x'); pixels(y,iy,'painted surface y');
                            pixels(w,iw,'painted surface width'); pixels(h,ih,'painted surface height');
                            pixels(vertical ? w : h, thickness, 'painted island thickness');
                            const distance = {top:y-monitor.y, bottom:monitor.y+monitor.height-y-h,
                                left:x-monitor.x, right:monitor.x+monitor.width-x-w}[edge];
                            pixels(distance, protrude ? 0 : 14, 'painted edge gap');
                            // Derive expected corners from observed physical contact, independently
                            // of production corner selection or logical child order.
                            const touches = {top:Math.abs(y-monitor.y)<1,
                                bottom:Math.abs(y+h-monitor.y-monitor.height)<1,
                                left:Math.abs(x-monitor.x)<1,
                                right:Math.abs(x+w-monitor.x-monitor.width)<1};
                            const expected = [touches.top||touches.left, touches.top||touches.right,
                                touches.bottom||touches.right, touches.bottom||touches.left];
                            const corners = [St.Corner.TOPLEFT,St.Corner.TOPRIGHT,St.Corner.BOTTOMRIGHT,St.Corner.BOTTOMLEFT];
                            corners.forEach((corner,index)=>pixels(painted.get_theme_node().get_border_radius(corner),
                                expected[index] ? 0 : 15, 'painted corner radius'));
                        }
                    }
                    cases++;
                    console.log('NATIVE_DASH_CASE', cases, edge, protrude, span, islands, floatEnds);
                }
            }
        }
    }
    }
    console.log(`PASS ${cases} native Dash edge cases: material thickness, physical gaps/corners, Mutter work areas`);
}
export function finish() {}
