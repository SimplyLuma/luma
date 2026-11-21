// SPDX-License-Identifier: GPL-2.0-or-later
// Screen sharing across a lock, and the shared window's ring, in a headless
// Luma Shell with the real portal stack (run.sh).
//
// MODE=lock  Three windows are shared through the portal (client.py, one
//            application each). The session locks and unlocks. While it is
//            locked: no frame reaches any application, no picker or other
//            share dialog is shown, a new request and a restored one wait,
//            and the pointer shows when it moves, also under a dialog over
//            the lock screen. After unlock: the same three streams carry on
//            without a new request, the restored share starts without a
//            picker, the new request's picker is shown once, and denying it
//            answers the application.
// MODE=lifecycle  A share's ring and pill go when the application is
//            killed, the portal backend is killed or stopped, or Mutter ends
//            the session underneath.
// MODE=screens  Three monitors at 100%, 125% and 200%: each Screens tile
//            shows its own monitor, display name and resolution.
// MODE=pill  A screen share: the pill is in no frame the application
//            receives; Dismiss and the sharing mark.
// MODE=ring  The ring of a shared window at the Shell's scales and in every
//            surface treatment, on floating, maximized, tiled, fullscreen,
//            moved and resized windows: crops and measurements for measure.py.
//
// Writes checks.json, state.json and PNGs to $LOCK_OUT.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as Capture from 'resource:///org/gnome/shell/ui/lumaCapture.js';
import * as LumaShare from 'resource:///org/gnome/shell/ui/lumaShare.js';
import * as ModalDialog from 'resource:///org/gnome/shell/ui/modalDialog.js';

const OUT = GLib.getenv('LOCK_OUT');
const HERE = GLib.getenv('LOCK_HERE');
const APPS = GLib.getenv('SHARE_APPS');
const MODE = GLib.getenv('LOCK_MODE') ?? 'lock';
const checks = [];
const state = {events: []};

Gio._promisify(Shell.Screenshot.prototype, 'screenshot_stage_to_content');
Gio._promisify(Shell.Screenshot, 'composite_to_stream');

const wait = ms => new Promise(r => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
    r();
    return GLib.SOURCE_REMOVE;
}));
const now = () => GLib.get_monotonic_time();
const log = text => console.log(`[lock-probe] ${text}`);
const event = (what, data = {}) => {
    state.events.push({t: now(), what, ...data});
    log(`${what} ${JSON.stringify(data)}`);
};

function check(name, ok, detail = null) {
    checks.push({name, ok: Boolean(ok), detail});
    log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail !== null ? ` ${JSON.stringify(detail)}` : ''}`);
}

function writeJson(name, data) {
    GLib.file_set_contents(`${OUT}/${name}.json`, JSON.stringify(data, null, 1));
}

async function until(what, fn, ms = 20000) {
    const end = now() + ms * 1000;
    while (now() < end) {
        try {
            if (fn())
                return true;
        } catch {}
        await wait(100);
    }
    log(`timed out waiting for ${what}`);
    return false;
}

function spawn(argv, logName) {
    const quoted = argv.map(a => GLib.shell_quote(a)).join(' ');
    Gio.Subprocess.new(['bash', '-c', `${quoted} >> ${OUT}/${logName}.log 2>&1`],
        Gio.SubprocessFlags.NONE);
}

function readText(path) {
    try {
        return new TextDecoder().decode(GLib.file_get_contents(path)[1]);
    } catch {
        return '';
    }
}

const client = tag => {
    try {
        return JSON.parse(readText(`${OUT}/client-${tag}.json`));
    } catch {
        return {steps: []};
    }
};
const clientStep = (tag, name) => client(tag).steps.find(s => s.step === name);
const frames = tag => readText(`${OUT}/frames-${tag}.txt`).split('\n').filter(Boolean).map(Number);
const framesBetween = (tag, from, to) => frames(tag).filter(t => t >= from && t < to).length;

async function shot(name) {
    Capture.setPaintInCaptures(true);
    try {
        const shooter = new Shell.Screenshot();
        const [content, scale] = await shooter.screenshot_stage_to_content();
        const file = Gio.File.new_for_path(`${OUT}/${name}.png`);
        const stream = file.replace(null, false, Gio.FileCreateFlags.NONE, null);
        await Shell.Screenshot.composite_to_stream(content.get_texture(), 0, 0, -1, -1, scale,
            null, 0, 0, 1, stream);
        stream.close(null);
        return scale;
    } finally {
        Capture.setPaintInCaptures(false);
    }
}

// ── Input ─────────────────────────────────────────────────────────────────

const seat = Clutter.get_default_backend().get_default_seat();
const keyboard = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
const pointer = seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
const cursor = global.backend.get_cursor_tracker();

async function movePointer() {
    for (let i = 0; i < 6; i++) {
        pointer.notify_relative_motion(now(), 9, 5);
        await wait(30);
    }
    await wait(200);
}

async function superKey(keyval) {
    keyboard.notify_keyval(now(), Clutter.KEY_Super_L, Clutter.KeyState.PRESSED);
    keyboard.notify_keyval(now(), keyval, Clutter.KeyState.PRESSED);
    keyboard.notify_keyval(now(), keyval, Clutter.KeyState.RELEASED);
    keyboard.notify_keyval(now(), Clutter.KEY_Super_L, Clutter.KeyState.RELEASED);
    await wait(900);
}

// ── Windows ───────────────────────────────────────────────────────────────

async function openWindow(appId, title) {
    spawn(['python3', `${APPS}/window.py`, appId, title, '--live'], `window-${title.replace(/\W/g, '_')}`);
    let window = null;
    await until(`window ${title}`, () => {
        window = global.get_window_actors().map(a => a.meta_window)
            .find(w => w.get_title() === title && w.get_frame_rect().width > 0);
        return window;
    });
    while (Main.overview.visible) {
        Main.overview.hide();
        await wait(300);
    }
    await wait(600);
    return window;
}

const share = () => LumaShare.getShare();

// Any Luma Share picker or other modal dialog the person could see.
function dialogsOnScreen() {
    return Main.layoutManager.modalDialogGroup.get_children()
        .filter(d => d.visible && d.mapped && d.opacity > 0)
        .map(d => d.constructor.name);
}

// ── MODE=lock ─────────────────────────────────────────────────────────────

async function pickWindow(window) {
    await until('a picker', () => share()._picker);
    const picker = share()._picker;
    await wait(800);
    const tile = picker._tiles.find(t => t.source?.kind === 'window' && t.source.window === window);
    picker._choose(tile);
    picker._share();
}

const lockedNow = () => Main.sessionMode.isLocked || Main.screenShield.active;

async function lockScenario() {
    // A headless backend never inhibits remote access (main.js); a session on
    // a display does. The probe stands for the display.
    global.backend.is_headless = () => false;

    const names = ['A', 'B', 'C'];
    const apps = ['org.luma.test.Notes', 'org.luma.test.Photos', 'org.luma.test.Reel'];
    const windows = [];
    for (let i = 0; i < 3; i++) {
        const w = await openWindow(apps[i], `Shared ${names[i]}`);
        w.move_frame(true, 80 + i * 560, 140 + i * 60);
        windows.push(w);
    }
    for (let i = 0; i < 3; i++) {
        spawn(['python3', `${HERE}/client.py`, names[i]], `client-${names[i]}`);
        await pickWindow(windows[i]);
        await until(`${names[i]} streaming`, () => frames(names[i]).length > 5);
    }
    for (const tag of names)
        check(`${tag}: shared through the portal and streaming`, frames(tag).length > 5, frames(tag).length);
    const tokenA = clientStep('A', 'start')?.token;
    check('A: the portal gave a restore token', Boolean(tokenA));

    // Watch everything a person could see, every 100 ms, from here on.
    const seen = [];
    const watch = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 100, () => {
        seen.push({t: now(), locked: lockedNow(), picker: Boolean(share()._picker),
            dialogs: dialogsOnScreen(), pointer: cursor.get_pointer_visible()});
        return GLib.SOURCE_CONTINUE;
    });

    await wait(1500);
    const tLock = now();
    event('lock');
    Main.screenShield.lock(false);
    await until('locked', () => Main.screenShield.active, 10000);
    event('locked', {mode: Main.sessionMode.currentMode});
    await wait(1500);

    // The lock screen hides the pointer until it moves. A dialog over the
    // lock screen (an authentication request takes the grab the same way)
    // must not keep it hidden.
    check('locked: the pointer is hidden until it moves', !cursor.get_pointer_visible());
    const overLock = new ModalDialog.ModalDialog({styleClass: 'prompt-dialog'});
    overLock.contentLayout.add_child(new St.Label({text: 'A dialog over the lock screen'}));
    overLock.open();
    await wait(600);
    await movePointer();
    check('locked: a moved pointer shows over a dialog above the lock screen',
        cursor.get_pointer_visible());
    overLock.close();
    await wait(600);

    // A restored share (A's token, a second share of A) and a new request.
    spawn(['python3', `${HERE}/client.py`, 'R', '--restore', tokenA ?? ''], 'client-R');
    await wait(1500);
    spawn(['python3', `${HERE}/client.py`, 'D'], 'client-D');
    await wait(3000);
    await movePointer();
    check('locked: the pointer shows when it moves', cursor.get_pointer_visible());
    await shot('locked');
    const tUnlock = now();
    const lockedSeen = seen.filter(s => s.t > tLock && s.t < tUnlock);
    check('locked: no picker or share dialog was ever shown',
        lockedSeen.every(s => !s.picker && !s.dialogs.some(d => d !== 'ModalDialog')),
        lockedSeen.filter(s => s.picker || s.dialogs.some(d => d !== 'ModalDialog')).slice(0, 3));
    check('locked: the restored share waits', !clientStep('R', 'start'), clientStep('R', 'start'));
    check('locked: the new request waits', !clientStep('D', 'start'), clientStep('D', 'start'));
    // A frame already in flight may land just after lock() is called.
    for (const tag of names) {
        const n = framesBetween(tag, tLock + 250000, tUnlock);
        check(`${tag}: no frame reached the application while locked`, n === 0, n);
        check(`${tag}: the share was not ended by the lock`, !clientStep(tag, 'session-closed') &&
            !clientStep(tag, 'stream-ended'), client(tag).steps.slice(-2));
    }

    event('unlock');
    Main.screenShield.deactivate(false);
    await until('unlocked', () => !lockedNow(), 10000);
    const tOpen = now();
    event('unlocked');
    await wait(3000);
    for (const tag of names) {
        const n = framesBetween(tag, tUnlock, now());
        check(`${tag}: the same stream carries on after unlock`, n > 5 && !clientStep(tag, 'session-closed'), n);
    }
    await until('restored', () => clientStep('R', 'start'), 10000);
    const restored = clientStep('R', 'start');
    // The one picker after unlock is the new request's (checked below); the
    // backend restored this one without asking.
    const portalLog = readText(`${OUT}/luma-portal.log`);
    check('the restored share started after unlock, without a picker',
        restored?.response === 0 && restored.t > tUnlock &&
        portalLog.includes('restored a previous choice') &&
        !portalLog.includes('no longer applies'), restored);
    await until('the waiting picker', () => share()._picker, 10000);
    const pickerAt = seen.find(s => s.picker && s.t > tLock)?.t ?? now();
    check('the new request is asked after unlock, in the normal session',
        Boolean(share()._picker) && pickerAt >= tOpen - 200000, {pickerAt, tOpen});
    await shot('asked-after-unlock');
    share()._picker?._cancel();
    await until('D answered', () => clientStep('D', 'start'), 10000);
    check('denying it answers the application', clientStep('D', 'start')?.response === 1,
        clientStep('D', 'start'));
    await wait(1000);
    check('only one picker was shown after unlock', seen.filter((s, i) => s.picker &&
        !(seen[i - 1]?.picker)).length === 1);
    await movePointer();
    check('after unlock the pointer is visible', cursor.get_pointer_visible());
    GLib.source_remove(watch);
    state.times = {tLock, tUnlock, tOpen};
    state.frames = Object.fromEntries([...names, 'R'].map(t => [t, frames(t).length]));
    state.seen = seen;
}

// ── MODE=lifecycle ────────────────────────────────────────────────────────

// Every way a share can end must take its ring and pill away: the
// application killed, the portal backend killed or stopped, the Mutter
// session ended underneath.
const sharesShown = () => share()._sessions.size + share()._edges.length;

function lastMutterSession() {
    const lines = readText(`${OUT}/luma-portal.log`).split('\n')
        .filter(l => l.includes('Mutter screen cast session '));
    return lines.at(-1)?.split('Mutter screen cast session ')[1]?.trim();
}

async function shareOnce(tag, window) {
    spawn(['python3', `${HERE}/client.py`, tag], `client-${tag}`);
    await pickWindow(window);
    await until(`${tag} started`, () => clientStep(tag, 'start'));
    await until(`${tag} ring`, () => share()._edges.length > 0, 5000);
    return share()._edges.length > 0 && share()._sessions.size > 0;
}

async function lifecycleScenario() {
    const window = await openWindow('org.luma.test.Notes', 'Lifecycle');
    const kill = (signal, pattern) => spawn(['pkill', `-${signal}`, '-f', pattern], 'pkill');

    check('E: a share shows its ring', await shareOnce('E', window));
    kill('KILL', `${HERE}/client.py E`);
    check('the application killed: ring and pill go',
        await until('E gone', () => sharesShown() === 0, 8000), sharesShown());

    check('F: a share shows its ring', await shareOnce('F', window));
    kill('KILL', 'luma_portal.service');
    check('the portal backend killed: ring and pill go',
        await until('F gone', () => sharesShown() === 0, 8000), sharesShown());

    check('G: a share shows its ring', await shareOnce('G', window));
    kill('TERM', 'luma_portal.service');
    check('the portal backend stopped: ring and pill go',
        await until('G gone', () => sharesShown() === 0, 8000), sharesShown());
    await wait(1000);
    check('a stopping backend says why', readText(`${OUT}/luma-portal.log`).includes('the backend is stopping'));

    check('H: a share shows its ring', await shareOnce('H', window));
    // Mutter ends the session on its own (only its owner may call Stop on
    // the bus; the compositor's remote access handle stops it from inside).
    const path = lastMutterSession();
    const handle = [...share()._handles].at(-1);
    handle?.stop();
    check('the Mutter session ended underneath: ring and pill go',
        await until('H gone', () => sharesShown() === 0, 8000), {path, left: sharesShown()});
    check('H: the application is told', await until('H closed', () => clientStep('H', 'session-closed'), 8000));
}

// ── MODE=screens ──────────────────────────────────────────────────────────

// Three virtual monitors at different scales (run.sh LOCK_MONITORS), each
// marked with its own colour: each Screens tile must show its own monitor,
// named and sized as that display.
async function layoutMonitors(scales) {
    const call = (method, params) => new Promise((resolve, reject) => Gio.DBus.session.call(
        'org.gnome.Mutter.DisplayConfig', '/org/gnome/Mutter/DisplayConfig',
        'org.gnome.Mutter.DisplayConfig', method, params, null, Gio.DBusCallFlags.NONE, -1, null,
        (c, res) => {
            try {
                resolve(c.call_finish(res));
            } catch (e) {
                reject(e);
            }
        }));
    const [serial, monitors] = (await call('GetCurrentState', null)).recursiveUnpack();
    let x = 0;
    const logical = monitors.map(([spec, modes], i) => {
        const mode = modes.find(m => m[6]['is-current']) ?? modes[0];
        const scale = mode[5].reduce((a, b) => Math.abs(b - scales[i]) < Math.abs(a - scales[i]) ? b : a);
        const entry = [Math.round(x), 0, scale, 0, i === 0, [[spec[0], mode[0], {}]]];
        x += mode[1] / scale;
        return {entry, connector: spec[0], mode: [mode[1], mode[2]], scale};
    });
    await call('ApplyMonitorsConfig', new GLib.Variant('(uua(iiduba(ssa{sv}))a{sv})',
        [serial, 1, logical.map(l => l.entry), {'layout-mode': new GLib.Variant('u', 1)}]));
    await wait(2500);
    return logical;
}

async function screensScenario() {
    const layout = await layoutMonitors([1, 1.25, 2]);
    const manager = global.backend.get_monitor_manager();
    const colours = ['#e0201c', '#1cb43c', '#2050e0'];
    const marks = [];
    layout.forEach((l, i) => {
        const index = manager.get_monitor_for_connector(l.connector);
        const m = Main.layoutManager.monitors[index];
        const mark = new St.Widget({style: `background-color: ${colours[i]};`});
        mark.set_position(m.x, m.y);
        mark.set_size(m.width, m.height);
        Main.layoutManager.addChrome(mark);
        marks.push({mark, index, connector: l.connector, mode: l.mode, scale: l.scale, colour: colours[i]});
    });
    await wait(500);
    const request = await share()._describe({types: 1});
    const picker = new LumaShare.SharePicker(request);
    picker._setMode('screens');
    await wait(2000);
    const scale = await shot('screens-picker');
    const image = await readPng(`${OUT}/screens-picker.png`);
    const tiles = picker._tiles.map(tile => {
        const [x, y] = tile._content.get_transformed_position();
        const [w, h] = tile._content.get_transformed_size();
        return {index: tile.source.index, connector: tile.source.connector, detail: tile.source.detail,
            centre: image.at(Math.round((x + w / 2) * scale / image.scale), Math.round((y + h / 2) * scale / image.scale))};
    });
    for (const m of marks) {
        const tile = tiles.find(t => t.index === m.index);
        const want = [1, 3, 5].map(k => parseInt(m.colour.slice(k, k + 2), 16));
        check(`monitor ${m.index} (${m.connector}, scale ${m.scale}): its tile shows its own screen`,
            tile && tile.centre.every((v, k) => Math.abs(v - want[k]) <= 24), {tile, want});
        check(`monitor ${m.index}: its tile is that display`, tile?.connector === m.connector, tile);
        check(`monitor ${m.index}: its tile reads the display's resolution`,
            tile?.detail === `${m.mode[0]} × ${m.mode[1]}`, {detail: tile?.detail, mode: m.mode});
    }
    state.screens = {layout: marks.map(({mark, ...m}) => m), tiles};
    picker.close(global.get_current_time());
}

// A PNG read back for sampling: at(x, y) -> [r, g, b].
async function readPng(path) {
    const GdkPixbuf = (await import('gi://GdkPixbuf')).default;
    const pixbuf = GdkPixbuf.Pixbuf.new_from_file(path);
    const bytes = pixbuf.get_pixels();
    const stride = pixbuf.get_rowstride();
    const n = pixbuf.get_n_channels();
    return {
        scale: 1,
        at: (x, y) => {
            const i = Math.min(pixbuf.height - 1, Math.max(0, y)) * stride + Math.min(pixbuf.width - 1, Math.max(0, x)) * n;
            return [bytes[i], bytes[i + 1], bytes[i + 2]];
        },
    };
}

// ── MODE=pill ─────────────────────────────────────────────────────────────

// A shared screen: the pill is on the screen and in none of the frames the
// application receives; Dismiss puts it away and the sharing mark brings it
// back.
async function pillScenario() {
    await openWindow('org.luma.test.Notes', 'Behind the pill');
    spawn(['python3', `${HERE}/client.py`, 'S', '--types', '1', '--snapshot', '40'], 'client-S');
    await until('a picker', () => share()._picker);
    const picker = share()._picker;
    await wait(1500);
    picker._setMode('screens');
    await wait(800);
    picker._choose(picker._tiles[0]);
    picker._share();
    await until('S streaming', () => frames('S').length > 45, 30000);
    const pill = share()._pill;
    check('the pill is up while a screen is shared', pill.visible && pill.mapped);
    // The pill and the screen edge are mapped for the whole share: the
    // stream keeps painting the stage (capture copies inhibited).
    const before = frames('S').length;
    await wait(3000);
    const streamed = frames('S').length - before;
    check('the stream kept delivering frames', streamed > 10, streamed);
    // The application's frame where the pill is: no pill violet in it.
    await until('S snapshot', () => GLib.file_test(`${OUT}/stream-S.png`, GLib.FileTest.EXISTS), 10000);
    const [px, py] = pill.get_transformed_position();
    const [pw, ph] = pill.get_transformed_size();
    const monitor = Main.layoutManager.monitors[0];
    const ms = global.display.get_monitor_scale(0);
    const frame = await readPng(`${OUT}/stream-S.png`);
    let violet = 0;
    for (let y = py; y < py + ph; y += 2) {
        for (let x = px; x < px + pw; x += 2) {
            const [r, g, b] = frame.at(Math.round((x - monitor.x) * ms), Math.round((y - monitor.y) * ms));
            if (Math.abs(r - 125) < 30 && Math.abs(g - 92) < 30 && Math.abs(b - 240) < 30)
                violet++;
        }
    }
    await shot('pill-on-screen');
    check('the frame the application received has no pill in it', violet === 0, violet);

    pill.dismissButton.emit('clicked', 0);
    await wait(500);
    const mark = Main.panel?.statusArea?.screenSharing;
    check('Dismiss puts the pill away', !pill.visible);
    check('sharing stays visible: the sharing mark is shown', Boolean(mark?.visible), mark?.visible);
    mark?._activate?.();
    await wait(500);
    check('the sharing mark brings the pill back', pill.visible);
}

// ── MODE=ring ─────────────────────────────────────────────────────────────

async function setScale(target) {
    const call = (method, params) => new Promise((resolve, reject) => Gio.DBus.session.call(
        'org.gnome.Mutter.DisplayConfig', '/org/gnome/Mutter/DisplayConfig',
        'org.gnome.Mutter.DisplayConfig', method, params, null, Gio.DBusCallFlags.NONE, -1, null,
        (c, res) => {
            try {
                resolve(c.call_finish(res));
            } catch (e) {
                reject(e);
            }
        }));
    const [serial, monitors] = (await call('GetCurrentState', null)).recursiveUnpack();
    const [spec, modes] = monitors[0];
    const mode = modes.find(m => m[6]['is-current']) ?? modes[0];
    const scale = mode[5].reduce((a, b) => Math.abs(b - target) < Math.abs(a - target) ? b : a);
    await call('ApplyMonitorsConfig', new GLib.Variant('(uua(iiduba(ssa{sv}))a{sv})',
        [serial, 1, [[0, 0, scale, 0, true, [[spec[0], mode[0], {}]]]], {}]));
    await wait(2500);
    return scale;
}

function ringOf(window) {
    return share()._edges.find(e => e._window === window);
}

// What the ring is made of, in stage coordinates: its actor and its pieces.
function ringGeometry(window) {
    const ring = ringOf(window);
    const frame = window.get_frame_rect();
    const [x, y] = ring.get_transformed_position();
    const [width, height] = ring.get_transformed_size();
    return {
        frame: {x: frame.x, y: frame.y, width: frame.width, height: frame.height},
        actor: {x, y, width, height},
        radius: ring._shape,
        visible: ring.visible,
    };
}

async function ringScenario() {
    const treatment = new Gio.Settings({schema_id: 'org.project_luma.shell-state'});
    // A flat background, so the halo's falloff can be measured against it.
    const backdrop = new St.Widget({style: 'background-color: #808080;'});
    backdrop.add_constraint(new Clutter.BindConstraint({source: global.stage,
        coordinate: Clutter.BindCoordinate.SIZE}));
    const wallpaper = global.window_group.get_children()
        .find(child => child instanceof Meta.BackgroundGroup) ?? null;
    global.window_group.insert_child_above(backdrop, wallpaper);

    const window = await openWindow('org.luma.test.Reel', 'Ring');
    window.move_resize_frame(true, 300, 220, 900, 560);
    await wait(800);
    // As the D-Bus wrapper hands it over: a dict of variants.
    share().SessionStarted('/ring/1', {
        'app-id': new GLib.Variant('s', ''),
        'windows': new GLib.Variant('at', [window.get_id()]),
        'monitors': new GLib.Variant('as', []),
    });
    await wait(500);
    const shots = [];
    const take = async (name, extra = {}) => {
        await wait(700);
        const scale = await shot(name);
        shots.push({name, scale, ...ringGeometry(window), ...extra});
    };
    for (const target of [1, 1.5, 2]) {
        const scale = await setScale(target);
        window.move_resize_frame(true, 300, 220, 900, 560);
        for (const surface of ['light', 'dark', 'frost', 'glass']) {
            treatment.set_string('surface-treatment', surface);
            await take(`ring-${target}-${surface}`, {target, scale, surface, state: 'floating'});
        }
        treatment.set_string('surface-treatment', 'light');
        window.move_frame(true, 520, 300);
        await take(`ring-${target}-moved`, {target, state: 'moved'});
        window.move_resize_frame(true, 520, 300, 700, 420);
        await take(`ring-${target}-resized`, {target, state: 'resized'});
        window.maximize();
        await take(`ring-${target}-maximized`, {target, state: 'maximized'});
        window.unmaximize();
        await wait(800);
        window.activate(global.get_current_time());
        await superKey(Clutter.KEY_Left);
        await take(`ring-${target}-tiled`, {target, state: 'tiled'});
        window.unmaximize();
        await wait(800);
        window.make_fullscreen();
        await take(`ring-${target}-fullscreen`, {target, state: 'fullscreen'});
        window.unmake_fullscreen();
        await wait(800);
    }
    // While a resize is under way: the ring follows every step.
    await setScale(1);
    window.move_resize_frame(true, 300, 220, 900, 560);
    await wait(800);
    const during = [];
    for (let i = 1; i <= 6; i++) {
        window.move_resize_frame(true, 300, 220, 900 - 40 * i, 560 - 25 * i);
        await wait(60);
        during.push(ringGeometry(window));
    }
    await take('ring-1-resizing', {target: 1, state: 'resizing'});
    state.during = during;
    state.shots = shots;
    share().SessionClosed('/ring/1');
    check('ring scenario ran', shots.length > 0);
}

async function work() {
    keyboard.notify_keyval(now(), Clutter.KEY_Shift_L, Clutter.KeyState.PRESSED);
    keyboard.notify_keyval(now(), Clutter.KEY_Shift_L, Clutter.KeyState.RELEASED);
    if (MODE === 'ring')
        await ringScenario();
    else if (MODE === 'screens')
        await screensScenario();
    else if (MODE === 'pill')
        await pillScenario();
    else if (MODE === 'lifecycle')
        await lifecycleScenario();
    else
        await lockScenario();
}

// The Shell imports this module once it is up and calls init(); run() is the
// perf runner's entry, which needs the perf helper and is not used here.
export function init() {
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, 6000, () => {
        finish();
        return GLib.SOURCE_REMOVE;
    });
}

async function finish() {
    try {
        await work();
    } catch (error) {
        check('scenario completed', false, `${error}\n${error.stack}`);
    }
    GLib.file_set_contents(`${OUT}/stop`, '');
    writeJson('checks', checks);
    writeJson('state', state);
    await wait(500);
    global.context.terminate();
}

export async function run() {
    await new Promise(() => {});
}
