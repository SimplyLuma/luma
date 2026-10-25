// SPDX-License-Identifier: Apache-2.0
// Native package mechanism probe. Final signed Flatpak/OS gates are separate.
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {proveNativeWindow} from './native-proof.js';
Gio._promisify(Gio.Subprocess.prototype, 'communicate_utf8_async', 'communicate_utf8_finish');
Gio._promisify(Gio.Subprocess.prototype, 'wait_async', 'wait_finish');
const sleep = ms => new Promise(resolve => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms,
    () => {resolve(); return GLib.SOURCE_REMOVE;}));
const out = GLib.getenv('BROWSER_GATE_OUT');
const url = GLib.getenv('BROWSER_GATE_URL');
const home = GLib.get_home_dir();
let child = null;
let childOutput = null;
let window = null;
let result = {scope: 'NATIVE_PACKAGE_MECHANISM_NOT_SIGNED_FLATPAK_OR_OS_ACCEPTANCE', ok: false};

async function probe() {
    try {
        Main.overview.hide();
        const launcher = new Gio.SubprocessLauncher({flags:
            Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE});
        launcher.setenv('GDK_BACKEND', 'wayland', true);
        launcher.setenv('WAYLAND_DISPLAY', 'viola-qa-shm-r1', true);
        launcher.unsetenv('DISPLAY');
        child = launcher.spawnv(['/usr/bin/python3',
            '/usr/share/viola-browser/luma-host/integrated_window.py',
            '--engine', '/opt/viola/browser/chrome',
            '--profile', `${home}/.local/share/viola-luma/profile`,
            '--work-root', `${home}/.local/state/viola-luma/sessions`, '--interactive', '--start-url', url]);
        const pid = Number(child.get_identifier());
        const output = child.communicate_utf8_async(null, null).then(reply => {
            const [stdout, stderr] = reply;
            GLib.file_set_contents(`${out}/browser.stdout`, stdout ?? '');
            GLib.file_set_contents(`${out}/browser.stderr`, stderr ?? '');
            return reply;
        });
        childOutput = output;
        const windows = () => global.get_window_actors().map(actor => actor.meta_window)
            .filter(candidate => candidate.get_pid() === pid);
        const deadline = Date.now() + 45000;
        let exited = false;
        output.then(() => {exited = true;});
        while (!windows().length && !exited && Date.now() < deadline) await sleep(200);
        if (windows().length !== 1) throw Error('Owned native browser did not map exactly one window');
        window = windows()[0];
        // The desktop default is taller than this fixture's 800px monitor.
        // Request a real fitting frame; all later proofs still use observed
        // compositor/GTK geometry, including a second, independent resize.
        window.move_resize_frame(false, 80, 60, 1000, 680);
        window.activate(global.get_current_time());
        result.records = await proveNativeWindow(window, launcher, out, url);
        window.delete(global.get_current_time());
        const closeDeadline = Date.now() + 15000;
        while (windows().length && Date.now() < closeDeadline) await sleep(100);
        if (windows().length) throw Error('Owned native browser window did not close');
        // The outer process timeout also bounds communication and engine drain.
        const [stdout, stderr] = await output;
        GLib.file_set_contents(`${out}/browser.stdout`, stdout ?? '');
        GLib.file_set_contents(`${out}/browser.stderr`, stderr ?? '');
        if (!child.get_successful()) throw Error('Native host failed while closing');
        const reports = Gio.File.new_for_path(`${home}/.local/state/viola-luma/sessions`)
            .enumerate_children('standard::name', Gio.FileQueryInfoFlags.NONE, null);
        const paths = [];
        let info;
        while ((info = reports.next_file(null))) {
            if (info.get_name().startsWith('native-window-'))
                paths.push(`${home}/.local/state/viola-luma/sessions/${info.get_name()}/integration-report.json`);
        }
        reports.close(null);
        if (paths.length !== 1) throw Error('Native host did not retain one integration report');
        const report = JSON.parse(new TextDecoder().decode(GLib.file_get_contents(paths[0])[1]));
        if (report.errors.length || report.engine_exit_code !== 0 || report.pending_leases !== 0)
            throw Error('Native engine shutdown did not drain successfully');
        result.integration = report;
        result.ok = true;
    } catch (error) {
        result.error = String(error);
        result.stack = error.stack;
    } finally {
        GLib.file_set_contents(`${out}/RESULT.json`, JSON.stringify(result, null, 2));
        if (!result.ok) {
            try {if (window) window.delete(global.get_current_time());} catch (_) {}
            try {if (child) child.send_signal(15);} catch (_) {}
            if (childOutput) await Promise.race([childOutput.catch(() => {}), sleep(3000)]);
        }
        global.context.terminate();
    }
}
export function init() {
    GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
        (async () => {
            const deadline = Date.now() + 60000;
            while (!Main.layoutManager?.primaryMonitor || Main.layoutManager._startingUp) {
                if (Date.now() > deadline) throw Error('Owned GNOME compositor did not finish startup');
                await sleep(200);
            }
            await probe();
        })().catch(error => {
            result.error = String(error);
            result.stack = error.stack;
            GLib.file_set_contents(`${out}/RESULT.json`, JSON.stringify(result, null, 2));
            global.context.terminate();
        });
        return GLib.SOURCE_REMOVE;
    });
}

// GNOME's automation loader owns this lifecycle; probe() terminates its context.
export async function run() {await new Promise(() => {});}
