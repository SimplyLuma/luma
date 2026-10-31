// SPDX-License-Identifier: Apache-2.0
// Mutter's frame-utilization-min (patch 0026) on the running Shell: the
// compositor's main thread takes the clamp (the kernel allows it without
// privileges on a task's own threads), keeps its policy and nice level, and
// nothing it starts inherits the clamp. Run through run.sh with
// PRESENTATION_AUTOMATION=<this file>.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

const results = [];
const out = GLib.getenv('PRESENTATION_OUT');
const source = GLib.getenv('PRESENTATION_SOURCE');
const sleep = ms => new Promise(resolve => GLib.timeout_add(GLib.PRIORITY_DEFAULT,
    ms, () => { resolve(); return GLib.SOURCE_REMOVE; }));
function check(name, condition, detail = '') {
    results.push({name, ok: Boolean(condition), detail});
    console.log(`[frame-utilization] ${condition ? 'PASS' : 'FAIL'} ${name} ${detail}`);
}
// Read a thread's scheduling attributes from a process the Shell starts.
function sched(tid) {
    const [, stdout, stderr] = Gio.Subprocess.new(
        ['python3', `${source}/tests/gnome-shell/presentation/thread-sched.py`, `${tid}`],
        Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE).communicate_utf8(null, null);
    try {
        return JSON.parse(stdout);
    } catch {
        return {error: stderr || stdout};
    }
}

async function work() {
    const pid = new Gio.Credentials().get_unix_pid();
    const mutter = new Gio.Settings({schema_id: 'org.gnome.mutter'});
    check('Mutter has the frame-utilization-min setting',
        mutter.settings_schema.has_key('frame-utilization-min'));
    const before = sched(pid);
    mutter.set_uint('frame-utilization-min', 512);
    await sleep(300);
    const boosted = sched(pid);
    check('the compositor main thread takes the clamp', boosted.util_min === 512,
        JSON.stringify(boosted));
    check('it keeps its policy and nice level and passes nothing on to what it starts',
        boosted.policy === before.policy && boosted.nice === before.nice && boosted.reset_on_fork,
        `before ${JSON.stringify(before)}`);
    const child = sched('self');
    check('a process the Shell starts does not inherit the clamp', child.util_min === 0, JSON.stringify(child));
    mutter.set_uint('frame-utilization-min', 0);
    await sleep(300);
    const restored = sched(pid);
    check('setting 0 removes the clamp', restored.util_min === 0, JSON.stringify(restored));
    mutter.reset('frame-utilization-min');
}

export function init() {
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, 4000, () => {
        work().catch(error => check('scenario completed', false, `${error}\n${error.stack}`)).finally(() => {
            GLib.file_set_contents(`${out}/results.json`, JSON.stringify(results, null, 2));
            global.context.terminate();
        });
        return GLib.SOURCE_REMOVE;
    });
}
export async function run() { await new Promise(() => {}); }
