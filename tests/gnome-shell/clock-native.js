// SPDX-License-Identifier: GPL-2.0-or-later
// Only in an owned disposable compositor/profile, against installed Shell.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import Pango from 'gi://Pango';
import Shell from 'gi://Shell';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as Scripting from 'resource:///org/gnome/shell/ui/scripting.js';

let assertions = 0;
function check(value, message) {
    if (!value)
        throw new Error(message);
    assertions++;
    print(`PASS ${message}`);
}
async function pause() { await Scripting.sleep(350); }
function measure(label) {
    const text = label.clutter_text;
    const layout = text.get_layout();
    const [ink, logical] = layout.get_pixel_extents();
    const [x, y] = text.get_transformed_position();
    return {text: label.text, ink: {x: x + ink.x, y: y + ink.y,
        width: ink.width, height: ink.height}, logicalHeight: logical.height,
        allocatedHeight: label.height,
        baseline: y + layout.get_baseline() / Pango.SCALE};
}
export async function run() {
    const iface = new Gio.Settings({schema_id: 'org.gnome.desktop.interface'});
    const appearance = new Gio.Settings({schema_id: 'org.project_luma.shell-state'});
    iface.set_boolean('enable-animations', false);
    iface.set_string('clock-format', '12h');
    appearance.set_int('status-time-size', 21);
    appearance.set_int('status-date-size', 11);
    appearance.set_int('shelf-padding', 10);
    Main.overview.hide();
    const context = St.ThemeContext.get_for_stage(global.stage);
    // Headless fixtures have no settings daemon to apply the image's font.
    context.set_font(Pango.FontDescription.from_string('Figtree 11'));
    await pause();
    const qs = Main.panel.statusArea.quickSettings;
    for (const treatment of ['light', 'dark']) {
        appearance.set_string('surface-treatment', treatment);
        for (const scale of [1, 2]) {
            context.scale_factor = scale;
            for (const rtl of [false, true]) {
                qs._statusTimeRow.set_text_direction(rtl
                    ? Clutter.TextDirection.RTL : Clutter.TextDirection.LTR);
                Main.panel.statusArea.dateMenu._timeDisplay.text = '9:02 p.m.';
                await pause();
                const labels = [qs._statusTime, qs._statusDateLabel, qs._statusAmPm];
                const measurements = labels.map(measure);
                const [, frameY] = qs._statusClock.get_transformed_position();
                const [, frameHeight] = qs._statusClock.get_transformed_size();
                for (let index = 0; index < labels.length; index++) {
                    const label = labels[index], measured = measurements[index];
                    check(!label.clutter_text.get_attributes()?.get_iterator()
                        .get(Pango.AttrType.ABSOLUTE_LINE_HEIGHT),
                    `${treatment}/${scale}/RTL=${rtl}: ${measured.text} has natural line metrics`);
                    check(measured.allocatedHeight >= measured.logicalHeight,
                        `${measured.text}: shaped line fits its native label`);
                    check(measured.ink.y >= frameY &&
                        measured.ink.y + measured.ink.height <= frameY + frameHeight,
                    `${measured.text}: full glyph ink fits the actual clock frame`);
                }
                check(Math.abs(measurements[0].baseline - measurements[2].baseline) <= 1,
                    'Digit and period text retain their actual common baseline');
                print(`CLOCK_NATIVE ${JSON.stringify({treatment, scale, rtl, measurements,
                    frameY, frameHeight})}`);
                if (!rtl) {
                    const directory = Gio.File.new_for_path('/var/tmp/luma-clock-20261006/captures');
                    if (!directory.query_exists(null))
                        directory.make_directory_with_parents(null);
                    const stream = directory.get_child(`clock-${treatment}-${scale}.png`)
                        .replace(null, false, Gio.FileCreateFlags.NONE, null);
                    try { await new Shell.Screenshot().screenshot(false, stream); }
                    finally { stream.close(null); }
                }
            }
        }
    }
    if (assertions !== 80)
        throw new Error(`Expected eighty native assertions, got ${assertions}`);
    print(`Clock native line and ink checks: PASS (${assertions} assertions)`);
}
