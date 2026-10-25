// SPDX-License-Identifier: Apache-2.0
// Native package mechanism probe. Final signed Flatpak/OS gates are separate.
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {proveNativeWindow} from './native-proof.js';
Gio._promisify(Gio.Subprocess.prototype, 'communicate_utf8_async', 'communicate_utf8_finish');
Gio._promisify(Gio.Subprocess.prototype, 'wait_async', 'wait_finish');
Gio._promisify(Shell.Screenshot.prototype, 'screenshot');
const sleep = ms => new Promise(resolve => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms,
    () => {resolve(); return GLib.SOURCE_REMOVE;}));
const out = GLib.getenv('BROWSER_GATE_OUT');
const url = GLib.getenv('BROWSER_GATE_URL');
const home = GLib.get_home_dir();
let child = null;
let childOutput = null;
let window = null;
let inputSeat = null;
let monitorStream = null;
let result = {scope: 'NATIVE_PACKAGE_MECHANISM_NOT_SIGNED_FLATPAK_OR_OS_ACCEPTANCE', ok: false, diagnostic_only: 'REQUEST_TIMING_NOT_RELEASE_ACCEPTANCE'};
export const METRICS = {};

async function probe() {
    print("LUMA_NATIVE_PHASE probe-entered");
    try {
        Main.overview.hide();
        const launcher = new Gio.SubprocessLauncher({flags:
            Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE});
        launcher.setenv('GDK_BACKEND', 'wayland', true);
        launcher.setenv('WAYLAND_DISPLAY', 'viola-qa-shm-r1', true);
        launcher.unsetenv('DISPLAY');
        const seatLauncher = new Gio.SubprocessLauncher({flags: Gio.SubprocessFlags.STDIN_PIPE});
        const seatReady = `${out}/virtual-seat.json`;
        inputSeat = seatLauncher.spawnv(['/usr/bin/python3', '-B',
            GLib.getenv('BROWSER_GATE_INPUT_HELPER'), 'seat', '--ready', seatReady]);
        const seatDeadline = Date.now() + 10000;
        while (!GLib.file_test(seatReady, GLib.FileTest.EXISTS)) {
            if (Date.now() > seatDeadline) throw Error('Owned headless virtual seat did not become ready');
            await sleep(100);
        }
        const hostLauncher = new Gio.SubprocessLauncher({flags: Gio.SubprocessFlags.NONE});
        hostLauncher.setenv('GDK_BACKEND', 'wayland', true);
        hostLauncher.setenv('WAYLAND_DISPLAY', 'viola-qa-shm-r1', true);
        hostLauncher.unsetenv('DISPLAY');
        hostLauncher.set_stdout_file_path(`${out}/browser.stdout`);
        hostLauncher.set_stderr_file_path(`${out}/browser.stderr`);
        result.desktop_state = {screen_locked: String(Main.screenShield?.locked),
            screen_active: String(Main.screenShield?.active),session_mode: String(Main.sessionMode?.currentMode)};
        const powerArgs = ['/usr/bin/busctl', '--user', 'get-property',
            'org.gnome.Mutter.DisplayConfig', '/org/gnome/Mutter/DisplayConfig',
            'org.gnome.Mutter.DisplayConfig', 'PowerSaveMode'];
        const power = launcher.spawnv(powerArgs);
        const [powerOut, powerError] = await power.communicate_utf8_async(null, null);
        if (!power.get_successful()) throw Error(`Display power state unavailable: ${powerError}`);
        result.desktop_state.power_before = powerOut.trim();
        // Waking this disposable monitor is an actual GUI-test precondition.
        // Never unlock a screen or replace the application's input proof.
        if (/^i [123]$/.test(powerOut.trim())) {
            const wake = launcher.spawnv(['/usr/bin/busctl', '--user', 'set-property',
                ...powerArgs.slice(3), 'i', '0']);
            const [, wakeError] = await wake.communicate_utf8_async(null, null);
            if (!wake.get_successful()) throw Error(`Owned display wake failed: ${wakeError}`);
        }
        GLib.file_set_contents(`${out}/desktop-state.json`, JSON.stringify(result.desktop_state, null, 2));
        monitorStream = seatLauncher.spawnv(['/usr/bin/python3', '-B',
            GLib.getenv('BROWSER_GATE_INPUT_HELPER'), 'monitor', '--ready', `${out}/monitor-stream.json`]);
        child = hostLauncher.spawnv(['/usr/bin/python3',
            GLib.getenv('BROWSER_GATE_REQUEST_DIAGNOSTIC'),
            '--engine', '/opt/viola/browser/chrome',
            '--profile', `${home}/.local/share/viola-luma/profile`,
            '--work-root', `${home}/.local/state/viola-luma/sessions`, '--interactive', '--start-url', url]);
        const pid = Number(child.get_identifier());
        const output = child.wait_async(null).then(() => ['', '']);
        childOutput = output;
        const windows = () => global.get_window_actors().map(actor => actor.meta_window)
            .filter(candidate => candidate.get_pid() === pid);
        const deadline = Date.now() + 45000;
        let exited = false;
        output.then(() => {exited = true;});
        const ready = () => {
            const owned = windows();
            if (owned.length !== 1 || !GLib.file_test(
                `${home}/.local/state/viola-luma/sessions/native-ready`, GLib.FileTest.EXISTS)) return false;
            const frame = owned[0].get_frame_rect();
            return frame.width > 200 && frame.height > 200;
        };
        // Meta exposes a new Wayland window before its first configure/map.
        // Use the shipped host's readiness marker and actual nonzero frame.
        while (!ready() && !exited && Date.now() < deadline) await sleep(200);
        if (!ready()) throw Error('Owned native browser did not finish mapping and host startup');
        if (windows().length !== 1) throw Error('Owned native browser did not map exactly one window');
        window = windows()[0];
        window.activate(global.get_current_time());
        // Headless clients have no preceding user activation serial. Focus
        // the real titlebar through the owned compositor input device before
        // requiring a page load; this never advances the page-input proof.
        const frame = window.get_frame_rect();
        const focus = launcher.spawnv(['/usr/bin/python3', '-B',
            GLib.getenv('BROWSER_GATE_INPUT_HELPER'), 'click',
            '--x', `${Math.round(frame.x + frame.width / 2)}`,
            '--y', `${Math.round(frame.y + 20)}`]);
        const [focusOut, focusError] = await focus.communicate_utf8_async(null, null);
        if (!focus.get_successful()) throw Error(`Owned titlebar focus failed: ${focusError}`);
        result.titlebar_focus = JSON.parse(focusOut);
        const focusDeadline = Date.now() + 10000;
        while (!window.has_focus() && Date.now() < focusDeadline) await sleep(100);
        if (!window.has_focus()) throw Error('Owned compositor click did not focus the browser window');
        result.records = await proveNativeWindow(window, launcher, out, url);
        window.delete(global.get_current_time());
        const closeDeadline = Date.now() + 15000;
        while (windows().length && Date.now() < closeDeadline) await sleep(100);
        if (windows().length) throw Error('Owned native browser window did not close');
        // The outer process timeout also bounds communication and engine drain.
        const [stdout, stderr] = await output;

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
        // Capture the live failure before closing the window changes its
        // active state, geometry and frame diagnostics. This is evidence,
        // never a replacement for the strict successful-page proof.
        try {
            const report = `${home}/.local/state/viola-luma/sessions/presentation-live.json`;
            GLib.file_set_contents(`${out}/live-report-before-cleanup.json`,
                GLib.file_get_contents(report)[1]);
            result.failure_windows = global.get_window_actors().map(actor => ({
                pid: actor.meta_window.get_pid(), title: actor.meta_window.get_title(),
                frame: (() => {const r=actor.meta_window.get_frame_rect();return {x:r.x,y:r.y,width:r.width,height:r.height};})(),
                buffer: (() => {const r=actor.meta_window.get_buffer_rect();return {x:r.x,y:r.y,width:r.width,height:r.height};})(),
                focused: actor.meta_window.has_focus(),
                minimized: actor.meta_window.minimized,
                workspace: actor.meta_window.get_workspace()?.index(),
                showing: actor.meta_window.showing_on_its_workspace(),
            }));
            const stream = Gio.File.new_for_path(`${out}/failure-screen.png`)
                .replace(null, false, Gio.FileCreateFlags.NONE, null);
            try {await new Shell.Screenshot().screenshot(false, stream);} finally {stream.close(null);}
        } catch (diagnosticError) {result.diagnostic_error = String(diagnosticError);}
    } finally {
        GLib.file_set_contents(`${out}/RESULT.json`, JSON.stringify(result, null, 2));
        if (!result.ok) {
            try {if (window) window.delete(global.get_current_time());} catch (_) {}
            try {if (child) child.send_signal(15);} catch (_) {}
            if (childOutput) await Promise.race([childOutput.catch(() => {}), sleep(3000)]);
        }
        if (monitorStream) {
            monitorStream.get_stdin_pipe().close(null);
            await Promise.race([monitorStream.wait_async(null), sleep(3000)]);
        }
        if (inputSeat) {
            inputSeat.get_stdin_pipe().close(null);
            await Promise.race([inputSeat.wait_async(null), sleep(3000)]);
        }
        global.context.terminate();
    }
}
// Use GNOME's automation lifecycle after its own performance helper maps;
// scheduling from init() bypasses the compositor's readiness barrier.
export function init() {
    print('LUMA_NATIVE_PHASE init-entered');
    Main.layoutManager.connect('startup-complete', () => print('LUMA_NATIVE_PHASE startup-complete'));
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, 20000, () => {
        const diagnostic = {
            scope: 'COMPOSITOR_STARTUP_DIAGNOSTIC_NOT_BROWSER_ACCEPTANCE',
            starting_up: Main.layoutManager._startingUp,
            stage_mapped: global.stage.mapped,
            stage_visible: global.stage.visible,
            monitors: Main.layoutManager.monitors.map(m => ({x:m.x,y:m.y,width:m.width,height:m.height})),
            windows: global.get_window_actors().map(a => ({pid:a.meta_window.get_pid(),title:a.meta_window.get_title()})),
        };
        GLib.file_set_contents(`${out}/startup-diagnostic.json`, JSON.stringify(diagnostic, null, 2));
        print(`LUMA_NATIVE_PHASE startup-diagnostic ${JSON.stringify(diagnostic)}`);
        return GLib.SOURCE_REMOVE;
    });
}
export async function run() {await probe();}
