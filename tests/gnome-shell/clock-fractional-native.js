// SPDX-License-Identifier: GPL-2.0-or-later
// Run only in a disposable Shell with private buses and settings.
// Actual monitor scaling is essential: changing ThemeContext.scale_factor
// alone leaves the text resource scale at one and misses this regression.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Pango from 'gi://Pango';
import Shell from 'gi://Shell';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Clock} from 'resource:///org/gnome/shell/ui/prairieLogin.js';
import * as Scripting from 'resource:///org/gnome/shell/ui/scripting.js';

let assertions = 0;
function check(value, message) {
    if (!value)
        throw new Error(message);
    assertions++;
}
function measure(label) {
    const text = label.clutter_text;
    const layout = text.get_layout();
    const resourceScale = text.get_resource_scale();
    const [ink] = layout.get_pixel_extents();
    const [x, y] = text.get_transformed_position();
    return {text: label.text, resourceScale,
        baseline: y + layout.get_baseline() / (Pango.SCALE * resourceScale),
        ink: {x: x + ink.x / resourceScale, y: y + ink.y / resourceScale,
            width: ink.width / resourceScale, height: ink.height / resourceScale}};
}
const Fixture = GObject.registerClass(class Fixture extends St.Widget {
    _init(clock) {
        super._init({x_expand: true, y_expand: true});
        this._clock = clock;
        this.add_child(clock);
    }
    vfunc_allocate(box) {
        this.set_allocation(box);
        const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor;
        this._clock.allocatePoster(box.get_width(), box.get_height(), 390 * scale, scale, false);
    }
});

export async function run() {
    const settings = new Gio.Settings({schema_id: 'org.gnome.desktop.interface'});
    settings.set_string('clock-format', '12h');
    settings.set_boolean('enable-animations', false);
    Main.overview.hide();
    const context = St.ThemeContext.get_for_stage(global.stage);
    context.set_font(Pango.FontDescription.from_string('Figtree 11'));
    const bus = Gio.DBus.session;
    const call = (method, args) => new Promise((resolve, reject) => {
        bus.call('org.gnome.Mutter.DisplayConfig', '/org/gnome/Mutter/DisplayConfig',
            'org.gnome.Mutter.DisplayConfig', method, args, null, Gio.DBusCallFlags.NONE,
            4000, null, (connection, result) => {
                try { resolve(connection.call_finish(result)); }
                catch (error) { reject(error); }
            });
    });
    const clock = new Clock();
    const fixture = new Fixture(clock);
    Main.layoutManager.uiGroup.add_child(fixture);
    try {
        for (const scale of [1, 1.25]) {
            const [serial, monitors] = (await call('GetCurrentState', null)).deep_unpack();
            const [spec, modes] = monitors[0];
            const mode = modes.find(item => item[6]['is-current']?.deep_unpack()) ?? modes[0];
            check(mode[5].some(value => Math.abs(value - scale) < 0.001),
                `Virtual monitor must support actual ${scale} scale`);
            await call('ApplyMonitorsConfig', new GLib.Variant('(uua(iiduba(ssa{sv}))a{sv})',
                [serial, 1, [[0, 0, scale, 0, true, [[spec[0], mode[0], {}]]]],
                    {'layout-mode': new GLib.Variant('u', 1)}]));
            await Scripting.sleep(500);
            clock._weekday.text = 'Thursday';
            clock._time.text = '8:21';
            clock._ampm.text = 'PM';
            clock._ampm.visible = true;
            clock._date.text = 'October 8';
            fixture.set_size(global.stage.width, global.stage.height);
            fixture.queue_relayout();
            await Scripting.sleep(500);
            const poster = [clock._time, clock._ampm, clock._date].map(measure);
            const quick = Main.panel.statusArea.quickSettings;
            const shelf = [quick._statusTime, quick._statusAmPm, quick._statusDateLabel].map(measure);
            print(`CLOCK_MEASURE ${JSON.stringify({scale, poster, shelf})}`);
            check(Math.abs(poster[0].baseline - poster[1].baseline) <= 1,
                `${scale}: login time and period must share a logical baseline`);
            check(Math.abs(poster[0].baseline - poster[2].baseline) <= 1,
                `${scale}: login date must align with the time`);
            check(Math.abs(shelf[0].baseline - shelf[1].baseline) <= 1,
                `${scale}: shelf time and period must share a logical baseline`);
            check(scale === 1 || poster.every(item => item.resourceScale > 1),
                'Fractional case must exercise scaled Pango resources');
            const [, frameY] = quick._statusClock.get_transformed_position();
            const [, frameHeight] = quick._statusClock.get_transformed_size();
            for (const item of shelf)
                check(item.ink.y >= frameY - 1 && item.ink.y + item.ink.height <= frameY + frameHeight + 1,
                    `${scale}: ${item.text} ink must fit its clock surface`);
            print(`CLOCK_FRACTIONAL ${JSON.stringify({scale, poster, shelf, frameY, frameHeight})}`);
            const output = GLib.getenv('LUMA_CLOCK_QA_OUT');
            if (output) {
                const directory = Gio.File.new_for_path(output);
                if (!directory.query_exists(null)) directory.make_directory_with_parents(null);
                const stream = directory.get_child(`clock-${scale}.png`)
                    .replace(null, false, Gio.FileCreateFlags.NONE, null);
                try { await new Shell.Screenshot().screenshot(false, stream); }
                finally { stream.close(null); }
            }
        }
        print(`CLOCK_FRACTIONAL_PASS ${assertions} assertions`);
    } finally {
        fixture.destroy();
    }
}
