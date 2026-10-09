// SPDX-License-Identifier: Apache-2.0
// Reuse the release gate's ordinary user-manager + headless Shell actor.
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import Shell from 'gi://Shell';
Gio._promisify(Gio.Subprocess.prototype, 'communicate_utf8_async', 'communicate_utf8_finish');
Gio._promisify(Gio.Subprocess.prototype, 'wait_async', 'wait_finish');

const OUT = GLib.getenv('BROWSER_GATE_OUT');
const URL = GLib.getenv('BROWSER_GATE_URL');
const HTTPS = GLib.getenv('BROWSER_GATE_HTTPS');
const OBSERVER = GLib.getenv('BROWSER_GATE_OBSERVER');
const results = [];
const sleep = ms => new Promise(resolve => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms,
    () => {resolve(); return GLib.SOURCE_REMOVE;}));
function check(name, ok, detail) {results.push({name,ok:!!ok,detail});}
async function until(predicate, milliseconds) {
    const deadline = Date.now()+milliseconds;
    do {const value=predicate(); if (value) return value; await sleep(200);} while (Date.now()<deadline);
    return null;
}
function browser() {return Shell.AppSystem.get_default().lookup_app('com.rhyme.viola.desktop');}
async function capture(process) {
    const [stdout,stderr] = await process.communicate_utf8_async(null,null);
    return {ok:process.get_successful(),stdout,stderr};
}
async function launch(url, requirePage) {
    const app=browser();
    if (!app) throw new Error('Shell does not know the canonical browser');
    if (app.get_windows().length) {
        for (const window of app.get_windows()) window.delete(global.get_current_time());
        if (!await until(()=>app.get_windows().length===0,15000)) throw new Error('browser did not close');
    }
    const launcher=new Gio.SubprocessLauncher({flags:Gio.SubprocessFlags.STDOUT_PIPE|Gio.SubprocessFlags.STDERR_PIPE});
    launcher.setenv('WAYLAND_DISPLAY',GLib.getenv('WAYLAND_DISPLAY') ?? 'wayland-0',true);
    launcher.setenv('GDK_BACKEND','wayland',true);
    launcher.unsetenv('DISPLAY');
    // Publish only this QA compositor's effective child-launcher variables
    // through the same session services used by the command-line tool.
    const names=['WAYLAND_DISPLAY','XDG_CURRENT_DESKTOP','XDG_SESSION_TYPE','GDK_BACKEND'];
    const activation={};
    for (const name of names) {
        const value=launcher.getenv(name);
        if (typeof value !== 'string' || value.length===0)
            throw new Error(`session activation variable is absent: ${name}`);
        activation[name]=value;
    }
    const dbusReply=Gio.DBus.session.call_sync('org.freedesktop.DBus','/org/freedesktop/DBus',
        'org.freedesktop.DBus','UpdateActivationEnvironment',new GLib.Variant('(a{ss})',[activation]),
        null,Gio.DBusCallFlags.NONE,5000,null);
    if (dbusReply.get_type_string() !== '()') throw new Error('unexpected D-Bus activation reply');
    const managerReply=Gio.DBus.session.call_sync('org.freedesktop.systemd1','/org/freedesktop/systemd1',
        'org.freedesktop.systemd1.Manager','SetEnvironment',
        new GLib.Variant('(as)',[names.map(name=>`${name}=${activation[name]}`)]),
        null,Gio.DBusCallFlags.NONE,5000,null);
    if (managerReply.get_type_string() !== '()') throw new Error('unexpected user-manager environment reply');
    // A graphical app may inherit xdg-open's output descriptors after the
    // launcher exits. Wait for the launcher, not for the app to close its pipes.
    const opener=new Gio.SubprocessLauncher({flags:Gio.SubprocessFlags.NONE});
    for (const name of [...names,'DBUS_SESSION_BUS_ADDRESS','XDG_RUNTIME_DIR']) {
        const value=launcher.getenv(name);
        if (value) opener.setenv(name,value,true);
    }
    opener.unsetenv('DISPLAY');
    const prefix=`${OUT}/xdg-open-${requirePage?'http':'https'}`;
    opener.set_stdout_file_path(`${prefix}.stdout`);
    opener.set_stderr_file_path(`${prefix}.stderr`);
    const opening=opener.spawnv(['/usr/bin/timeout','-k','5','90','/usr/bin/xdg-open',url]);
    await opening.wait_async(null);
    const readLog=path=>new TextDecoder().decode(GLib.file_get_contents(path)[1]);
    const opened={ok:opening.get_successful(),stdout:readLog(`${prefix}.stdout`),stderr:readLog(`${prefix}.stderr`)};
    if (!opened.ok) throw new Error(`xdg-open failed: ${opened.stderr}`);
    const window=await until(()=>browser()?.get_windows().find(w=>w.get_frame_rect().width>0 && w.get_frame_rect().height>0 && w.get_compositor_private() && w.showing_on_its_workspace()),60000);
    if (!window) {
        const tracker=Shell.WindowTracker.get_default();
        const mapped=global.get_window_actors().map(actor=>{
            const w=actor.meta_window;
            return {class:w.get_wm_class(),app_id:tracker.get_window_app(w)?.get_id()??null,
                title:w.get_title(),pid:w.get_pid(),frame:w.get_frame_rect(),
                visible:w.showing_on_its_workspace(),compositor:!!w.get_compositor_private()};
        });
        throw new Error(`xdg-open produced no real canonical browser window: ${JSON.stringify({opened,mapped})}`);
    }
    const proof=await capture(launcher.spawnv(['/usr/bin/python3',OBSERVER,`${window.get_pid()}`,url]));
    const observed=JSON.parse(proof.stdout);
    if (!proof.ok || !observed.ok) throw new Error(`window process proof failed: ${proof.stdout}`);
    if (requirePage && !await until(()=>GLib.file_test(`${OUT}/page-requested`,GLib.FileTest.EXISTS),30000))
        throw new Error('the browser did not request the unique local page');
    check(requirePage?'signed-browser-loads-requested-page':'xdg-open-https-launches-viola',true,
        JSON.stringify({window_id:window.get_stable_sequence(),app_id:browser().get_id(),url,proof:observed}));
    window.delete(global.get_current_time());
    if (!await until(()=>!global.get_window_actors().some(actor=>actor.meta_window === window),15000)) throw new Error(`browser did not close after proof: ${JSON.stringify({time:global.get_current_time(),appWindows:app.get_windows().map(w=>({id:w.get_stable_sequence(),title:w.get_title(),pid:w.get_pid(),canClose:w.can_close()})),actors:global.get_window_actors().map(a=>({id:a.meta_window.get_stable_sequence(),title:a.meta_window.get_title()}))})}`);
    // Each URL proof starts a separate disposable Flatpak instance. A closed
    // frame need not stop the app's backend or its GApplication forwarding.
    const instance=observed.record.instance_id;
    if (typeof instance !== 'string' || !/^[0-9]+$/.test(instance))
        throw new Error('the proven browser has no valid Flatpak instance identity');
    const stopped=await capture(launcher.spawnv(['/usr/bin/flatpak','kill',instance]));
    if (!stopped.ok && GLib.file_test(`/proc/${observed.record.pid}`,GLib.FileTest.EXISTS))
        throw new Error(`disposable browser instance cleanup failed: ${stopped.stderr}`);
    if (!await until(()=>!GLib.file_test(`/proc/${observed.record.pid}`,GLib.FileTest.EXISTS),30000))
        throw new Error(`the closed browser host is still alive: ${observed.record.pid}`);
}
export function init() {
    GLib.timeout_add(GLib.PRIORITY_DEFAULT,5000,()=>{
        launch(URL,true).then(()=>launch(HTTPS,false)).catch(error=>check('signed-browser-graphical-launch',false,`${error}\n${error.stack}`))
            .finally(()=>{GLib.file_set_contents(`${OUT}/results.json`,JSON.stringify(results,null,2)); global.context.terminate();});
        return GLib.SOURCE_REMOVE;
    });
}
export async function run() {await new Promise(()=>{});}
