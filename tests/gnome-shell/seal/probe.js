// SPDX-License-Identifier: GPL-2.0-or-later
// Seal evidence harness: the Shell's automation script. Waits for the session
// to settle, runs eval.js, then ends the Shell.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const OUT = GLib.getenv('SEAL_OUT');
Gio._promisify(Shell.Screenshot.prototype, 'screenshot_stage_to_content');
Gio._promisify(Shell.Screenshot, 'composite_to_stream');

async function shot(name, x, y, width, height) {
    const shooter = new Shell.Screenshot();
    const [content, scale] = await shooter.screenshot_stage_to_content();
    const texture = content.get_texture();
    const file = Gio.File.new_for_path(`${OUT}/${name}.png`);
    const stream = file.replace(null, false, Gio.FileCreateFlags.NONE, null);
    // Stage coordinates to the texture's pixels (2 with a 200% monitor).
    const k = scale;
    await Shell.Screenshot.composite_to_stream(texture, Math.round(x * k), Math.round(y * k),
        Math.round(width * k), Math.round(height * k), scale, null, 0, 0, 1, stream);
    stream.close(null);
}

let started = false;
export function init() {
    GLib.mkdir_with_parents(OUT, 0o755);
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, Number(GLib.getenv('SEAL_SETTLE') ?? 8000), () => {
        if (!started) {
            started = true;
            import(`file://${GLib.getenv('SEAL_EVAL')}`)
                .then(m => m.default({Main, shot}))
                .catch(e => console.error(`[seal] harness error ${e}\n${e.stack}`))
                .finally(() => global.context.terminate());
        }
        return GLib.SOURCE_REMOVE;
    });
}

export async function run() {
    await new Promise(() => {});
}
