// SPDX-License-Identifier: GPL-2.0-or-later
// Screen sharing evidence, driven inside a headless Luma Shell.
//
// Every request goes through the real stack: client.py asks
// org.freedesktop.portal.ScreenCast, xdg-desktop-portal asks luma-portal,
// luma-portal asks this Shell's picker and then Mutter. Input is a virtual
// keyboard and pointer, so the keyboard and the double-click are exercised the
// way a person exercises them, not by calling the picker's methods.
//
// MODE=full   light, every scenario below
// MODE=look   the picker only (windows, screens, resolved requester), for the
//             dark and 200% passes
// Writes screenshots, state-*.json and checks.json to $ORACLE_OUT.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GObject from 'gi://GObject';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as Capture from 'resource:///org/gnome/shell/ui/lumaCapture.js';

const OUT = GLib.getenv('ORACLE_OUT') ?? '/oracle/out';
const HERE = GLib.getenv('SHARE_HARNESS') ?? '/oracle/t';
const MODE = GLib.getenv('MODE') ?? 'full';
const checks = [];

Gio._promisify(Shell.Screenshot.prototype, 'screenshot_stage_to_content');
Gio._promisify(Shell.Screenshot, 'composite_to_stream');

const wait = ms => new Promise(r => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
    r();
    return GLib.SOURCE_REMOVE;
}));
const now = () => GLib.get_monotonic_time();
const log = text => console.log(`[share-probe] ${text}`);

function check(name, ok, detail = null) {
    checks.push({name, ok: Boolean(ok), detail});
    log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ` ${JSON.stringify(detail)}` : ''}`);
}

function write(name, data) {
    GLib.file_set_contents(`${OUT}/${name}.json`, JSON.stringify(data, null, 1));
}

// A screenshot is the same offscreen paint a recording or a cast uses, so the
// pill and the edge -- which stay out of captures -- would be missing from it.
// Capture's switch paints them for the length of one screenshot, so the image
// shows what a person sees. No stream frame is ever grabbed while it is on:
// the frames the application receives are checked with it off.
async function shot(name) {
    Capture.setPaintInCaptures(true);
    try {
        await shotRaw(name);
    } finally {
        Capture.setPaintInCaptures(false);
    }
}

async function shotRaw(name) {
    const shooter = new Shell.Screenshot();
    const [content, scale] = await shooter.screenshot_stage_to_content();
    const texture = content.get_texture();
    const file = Gio.File.new_for_path(`${OUT}/${name}.png`);
    const stream = file.replace(null, false, Gio.FileCreateFlags.NONE, null);
    await Shell.Screenshot.composite_to_stream(texture, 0, 0, -1, -1, scale,
        null, 0, 0, 1, stream);
    stream.close(null);
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

function box(actor) {
    const [x, y] = actor.get_transformed_position();
    const [width, height] = actor.get_transformed_size();
    return {x: Math.round(x), y: Math.round(y), width: Math.round(width), height: Math.round(height)};
}

function clientSteps(tag) {
    try {
        const [, bytes] = GLib.file_get_contents(`${OUT}/client-${tag}.json`);
        return JSON.parse(new TextDecoder().decode(bytes)).steps;
    } catch {
        return [];
    }
}
const clientDid = (tag, step, pred = () => true) =>
    clientSteps(tag).some(s => s.step === step && pred(s));

// ── Input ─────────────────────────────────────────────────────────────────

const seat = Clutter.get_default_backend().get_default_seat();
const keyboard = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
const pointer = seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);

async function key(keyval, modifiers = []) {
    for (const m of modifiers)
        keyboard.notify_keyval(now(), m, Clutter.KeyState.PRESSED);
    keyboard.notify_keyval(now(), keyval, Clutter.KeyState.PRESSED);
    keyboard.notify_keyval(now(), keyval, Clutter.KeyState.RELEASED);
    for (const m of modifiers.reverse())
        keyboard.notify_keyval(now(), m, Clutter.KeyState.RELEASED);
    await wait(250);
}

async function click(actor, settle = 250) {
    const b = box(actor);
    pointer.notify_absolute_motion(now(), b.x + b.width / 2, b.y + b.height / 2);
    await wait(80);
    pointer.notify_button(now(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.PRESSED);
    pointer.notify_button(now(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.RELEASED);
    await wait(settle);
}

// ── The picker's state, as a person and a screen reader see it ───────────

const share = () => Main.lumaShare;
const picker = () => share()?._picker ?? null;

function pickerState() {
    const p = picker();
    if (!p)
        return null;
    const acc = a => ({name: a.accessible_name, role: a.accessible_role,
        checked: a.get_accessible?.()?.get_state_set?.()?.contains?.(4) ?? null});
    return {
        title: p._title.text,
        body: p._body.text,
        card: box(p._card),
        cardAccessible: acc(p._card),
        tabsVisible: p._tabs.visible,
        tabs: Object.entries(p._tabButtons ?? {}).map(([mode, b]) => ({mode, ...acc(b),
            selected: b.has_style_pseudo_class('checked')})),
        mode: p._mode,
        tiles: p._tiles.map(t => ({kind: t.source.kind, title: t.source.title,
            detail: t.source.detail, ...acc(t), chosen: t.chosen, focused: t.has_key_focus(),
            box: box(t), frame: box(t._frame)})),
        options: [p._pointer, p._hideNotifications, p._control].map(o => ({
            visible: o.visible, active: o.active, ...acc(o)})),
        shareSensitive: p._shareButton.reactive,
        requesterResolved: Boolean(p._badge),
    };
}

function sharingState() {
    const s = share();
    const pill = s._pill;
    return {
        sharing: s.sharing,
        silencing: s.silencing,
        pill: pill.visible ? {box: box(pill), text: pill._label.text,
            stop: box(pill.stopButton), accessibleName: pill.surface.accessible_name} : null,
        edges: s._edges.map(e => ({type: e.constructor.name, box: box(e), visible: e.visible})),
        wellMarkVisible: Main.panel.statusArea.screenSharing?.visible ?? null,
    };
}

function windowByTitle(title) {
    return global.display.list_all_windows().find(w => w.get_title() === title);
}

// ── Scenarios ─────────────────────────────────────────────────────────────

// Each scenario starts from nothing on screen and nothing shared, whatever
// the one before it left behind; otherwise one failure fails every later one.
async function settle() {
    for (let i = 0; i < 3 && picker(); i++) {
        await key(Clutter.KEY_Escape);
        await until('picker closed', () => !picker(), 3000);
    }
    if (share().sharing) {
        share().stopSharing();
        await until('sharing ended', () => !share().sharing, 8000);
    }
    await wait(800);
}

async function ask(tag, types, extra = []) {
    spawn(['python3', `${HERE}/client.py`, tag, String(types), ...extra], `client-${tag}`);
    return until(`picker for ${tag}`, () => picker()?._tiles !== undefined && picker().mapped);
}

// A browser-style request for windows or screens, answered by keyboard only.
// The live window's preview is shot twice, a moment apart.
async function keyboardShare() {
    check('S1 picker opened from a portal request', await ask('s1', 3, ['--grab', '4']));
    await wait(1200);
    await shot('02-picker-windows-a');
    write('state-02-picker-windows', pickerState());
    await wait(700);
    await shot('02-picker-windows-b');
    const state = pickerState();
    check('S1 opens on Windows with both tabs', state.tabsVisible && state.mode === 'windows', state.tabs);
    check('S1 focus starts on the first preview', state.tiles[0]?.focused);
    check('S1 Share is disabled until something is chosen', !state.shareSensitive);
    check('S1 hide-notifications on, pointer on, no control switch',
        state.options[0].active && state.options[1].active && !state.options[2].visible);

    await key(Clutter.KEY_Tab, [Clutter.KEY_Control_L]);
    await wait(900);
    await shot('02-picker-screens');
    write('state-02-picker-screens', pickerState());
    check('S1 Control+Tab shows Screens', picker()._mode === 'screens');
    await key(Clutter.KEY_Tab, [Clutter.KEY_Control_L]);
    await wait(400);
    picker()._tiles[0].grab_key_focus();

    const live = picker()._tiles.findIndex(t => t.source.title === 'Prairie 0.1, launch film');
    check('S1 the playing window has a preview', live >= 0);
    for (let i = 0; i < live; i++)
        await key(Clutter.KEY_Right);
    await key(Clutter.KEY_space);
    const chosen = pickerState();
    write('state-04-keyboard-chosen', chosen);
    await shot('04-keyboard-chosen');
    check('S1 arrows and Space choose the playing window, and Space does not share',
        Boolean(picker()) && chosen.tiles[live]?.chosen && chosen.shareSensitive);

    await key(Clutter.KEY_Return);
    check('S1 Enter shares', await until('picker closed', () => !picker()));
    check('S1 the Shell is told what is shared', await until('session', () => share().sharing));
    await wait(1500);
    await shot('06-sharing-window');
    const sharing = sharingState();
    write('state-06-sharing-window', sharing);
    check('S1 the pill names what is shared and with whom', Boolean(sharing.pill?.text), sharing.pill?.text);
    check('S1 one window edge', sharing.edges.length === 1 && sharing.edges[0].type.includes('WindowEdge'));
    check('S1 the Well mark stands down while the pill is up', sharing.wellMarkVisible !== true);
    check('S1 banners wait while sharing', sharing.silencing);

    const win = windowByTitle('Prairie 0.1, launch film');
    const edgeAround = () => {
        const r = win.get_frame_rect();
        const e = share()._edges[0];
        const halo = 7 * St_scale();
        const b = box(e);
        return {frame: {x: r.x, y: r.y, width: r.width, height: r.height}, edge: b,
            follows: Math.abs(b.x - (r.x - halo)) <= 1 && Math.abs(b.y - (r.y - halo)) <= 1 &&
                Math.abs(b.width - (r.width + 2 * halo)) <= 1 && Math.abs(b.height - (r.height + 2 * halo)) <= 1};
    };
    win.move_frame(true, 260, 170);
    await wait(900);
    await shot('06-edge-moved');
    const moved = edgeAround();
    check('S1 the edge follows a moved window', moved.follows, moved);
    win.move_resize_frame(true, 320, 210, 640, 400);
    await wait(900);
    await shot('06-edge-resized');
    const resized = edgeAround();
    check('S1 the edge follows a resized window', resized.follows, resized);
    write('state-06-edge', {moved, resized});

    check('S1 the application received frames',
        await until('s1 frame', () => clientDid('s1', 'grabbed', s => s.file), 25000));

    await click(share()._pill.stopButton, 600);
    check('S1 Stop ends the share in the Shell', await until('sharing ended', () => !share().sharing));
    check('S1 Stop ends the session in the application',
        await until('s1 closed', () => clientDid('s1', 'session-closed'), 15000));
    await wait(600);
    await shot('06-stopped');
    write('state-06-stopped', sharingState());
    check('S1 the pill and the edge are gone', !share()._pill.visible && share()._edges.length === 0);
}

// A pointer double-click on a preview shares it. The first click alone
// only chooses.
async function doubleClickShare() {
    await settle();
    check('S2 picker opened', await ask('s2', 3));
    await wait(1000);
    const tile = picker()._tiles[0];
    await click(tile, 400);
    await shot('04-pointer-one-click');
    check('S2 one click chooses and does not share', Boolean(picker()) && tile.chosen);
    // A real double-click on a different preview: two presses well inside
    // the double-click time. The first chooses it, the second shares it.
    const t2 = picker()?._tiles[1];
    await click(t2, 120);
    await click(t2, 0);
    check('S2 a double-click on another preview shares it',
        await until('picker closed', () => !picker()) && await until('session', () => share().sharing));
    write('state-04-double-click', sharingState());
    await wait(800);
    await click(share()._pill.stopButton, 600);
    check('S2 stopped', await until('sharing ended', () => !share().sharing));
}

// A request that allows only screens: no tabs, and the application's frames
// must not contain the violet edge or the pill that the person sees.
async function screenShare() {
    await settle();
    check('S3 picker opened for screens only', await ask('s3', 1, ['--grab', '3.5']));
    await wait(1000);
    const state = pickerState();
    write('state-03-screens-only', state);
    await shot('03-screens-only');
    check('S3 one type hides the Windows/Screens control', !state.tabsVisible && state.mode === 'screens');
    await key(Clutter.KEY_Return);
    check('S3 Enter on the focused preview chooses and shares',
        await until('session', () => share().sharing));
    // The application's frames first, with nothing painted for the camera.
    check('S3 the application received frames',
        await until('s3 frame', () => clientDid('s3', 'grabbed', s => s.file), 25000));
    await shot('06-sharing-screen');
    const sharing = sharingState();
    write('state-06-sharing-screen', sharing);
    check('S3 one screen edge', sharing.edges.length === 1 && sharing.edges[0].type.includes('ScreenEdge'));
    check('S3 the pill is up for a shared screen', Boolean(sharing.pill), sharing.pill?.text);
    // The edge covers the whole monitor; it must not take the pointer.
    // Put the target where nothing else covers it, so the only thing between
    // the pointer and the window is the edge itself.
    const behind = windowByTitle('Library');
    const others = global.display.list_all_windows().filter(w => w !== behind && w.window_type === 0);
    others.forEach((w, i) => w.move_frame(true, 20 + i * 30, 20 + i * 30));
    behind.move_frame(true, 980, 480);
    await wait(700);
    await click({get_transformed_position: () => {
        const r = behind.get_frame_rect();
        return [r.x + r.width / 2, r.y + r.height / 2];
    }, get_transformed_size: () => [1, 1]}, 600);
    check('S3 a click through the screen edge reaches the window beneath',
        global.display.focus_window === behind, global.display.focus_window?.get_title());
    await click(share()._pill.stopButton, 600);
    check('S3 stopped', await until('sharing ended', () => !share().sharing) &&
        await until('s3 closed', () => clientDid('s3', 'session-closed'), 15000));
}

async function escapeCancels() {
    await settle();
    check('S4 picker opened', await ask('s4', 3));
    await wait(800);
    await key(Clutter.KEY_Escape);
    check('S4 Escape closes the picker', await until('picker closed', () => !picker()));
    check('S4 the application is told it was cancelled',
        await until('s4 answer', () => clientDid('s4', 'start', s => s.response === 1), 15000));
    check('S4 nothing is shared', !share().sharing);
}

// The backend called with an app id, to show a resolved requester. A host
// caller has none, which is what the other scenarios show.
async function resolvedRequester(suffix = '') {
    await settle();
    spawn(['python3', `${HERE}/client.py`, `s5${suffix}`, '3', '--direct', 'org.luma.test.Viola'], 'client-s5');
    check(`S5${suffix} picker opened`, await until('picker', () => picker()?.mapped));
    await wait(1200);
    await shot(`02-picker-resolved${suffix}`);
    const state = pickerState();
    write(`state-02-picker-resolved${suffix}`, state);
    check(`S5${suffix} the requester is named from the system`, state.requesterResolved &&
        state.title.startsWith('Viola') && state.body.includes('Viola will see it'), state.title);
    await key(Clutter.KEY_Escape);
    await until('picker closed', () => !picker());
}

// Counts its own paints by where they go: into a stage view's framebuffer
// (the screen) or anywhere else (a capture repainting the stage).
const paints = {view: 0, other: 0};
const PaintCounter = GObject.registerClass(
class PaintCounter extends St.Widget {
    vfunc_paint(paintContext) {
        const fb = paintContext.get_framebuffer();
        const onView = global.stage.peek_stage_views().some(v =>
            v.get_framebuffer() === fb || v.get_onscreen() === fb);
        paints[onView ? 'view' : 'other']++;
        super.vfunc_paint(paintContext);
    }
});

// Which kinds of actor reach a monitor stream. Three squares in top chrome
// while a screen is shared: a filled CaptureHidden, a border-only
// CaptureHidden (the shape of the screen edge) and a plain widget as the
// control. measure.py reads the application's frame at each.
async function streamExperiment() {
    await settle();
    spawn(['python3', `${HERE}/client.py`, 'x1', '1', '--grab', '5', '--grab', '9'], 'client-x1');
    await until('picker', () => picker()?.mapped);
    await wait(800);
    await key(Clutter.KEY_Return);
    await until('session', () => share().sharing);
    const made = [];
    const add = (actor, x, y) => {
        actor.set_position(x, y);
        actor.set_size(160, 160);
        Main.layoutManager.addTopChrome(actor);
        made.push(actor);
    };
    add(new Capture.CaptureHidden({style: 'background-color: #ff00ff;'}), 80, 80);
    add(new Capture.CaptureHidden({style: 'border: 6px solid #ff00ff;'}), 300, 80);
    add(new St.Widget({style: 'background-color: #00ff00;'}), 520, 80);
    const surface = new Capture.ShelfSurface('pill', 20, {prefix: 'luma-share'});
    surface.setContent(new St.Widget({style: 'background-color: #ff00ff;', x_expand: true, y_expand: true}));
    add(surface, 740, 80);
    const held = new Capture.CaptureHidden({layout_manager: new Clutter.BinLayout()});
    held.add_child(new St.Widget({style: 'background-color: #ff00ff;', x_expand: true, y_expand: true}));
    add(held, 1180, 80);
    const counter = new PaintCounter({style: 'background-color: #0000ff;'});
    add(counter, 960, 80);
    await wait(1500);
    paints.view = paints.other = 0;
    await wait(2000);
    const idle = {...paints};
    const edge = share()._edges[0];
    write('state-x1', {edge: edge ? {type: edge.constructor.name,
        isCaptureHidden: edge instanceof Capture.CaptureHidden, parent: edge.get_parent()?.constructor.name,
        parentName: edge.get_parent()?.name, style: edge.style_class,
        paintVfuncOwn: Object.getOwnPropertyNames(Object.getPrototypeOf(edge)),
        protoChain: (() => { const c = []; let p = Object.getPrototypeOf(edge); while (p && c.length < 6) { c.push(p.constructor.name); p = Object.getPrototypeOf(p); } return c; })()} : null,
        squares: made.map(a => ({type: a.constructor.name, box: box(a)}))});
    await until('x1 frame', () => clientDid('x1', 'grabbed', s => s.file), 25000);
    write('state-x1-paints', {idleTwoSeconds: idle, afterGrab: {...paints}});
    // The same share, with capture-hidden actors forced to paint: if the
    // pill appears only now, it was not being painted on the screen either.
    const pill = share()._pill;
    write('state-x1-pill', {visible: pill.visible, mapped: pill.mapped, opacity: pill.opacity,
        paintOpacity: pill.get_paint_opacity(), redirect: pill.get_offscreen_redirect(),
        effects: pill.get_effects().map(e => e.constructor.name),
        surfaceEffects: pill.surface.get_effects().map(e => e.constructor.name),
        box: box(pill)});
    Capture.setPaintInCaptures(true);
    await until('x1 second frame', () => clientDid('x1', 'grabbed', s => s.index === 1 && s.file), 25000);
    Capture.setPaintInCaptures(false);
    made.forEach(a => a.destroy());
    await settle();
}

async function lookOnly(suffix) {
    check(`L${suffix} picker opened`, await ask(`l${suffix}`, 3));
    await wait(1400);
    await shot(`02-picker-windows${suffix}`);
    write(`state-02-picker-windows${suffix}`, pickerState());
    await key(Clutter.KEY_Tab, [Clutter.KEY_Control_L]);
    await wait(900);
    await shot(`02-picker-screens${suffix}`);
    await key(Clutter.KEY_Escape);
    await until('picker closed', () => !picker());
    await resolvedRequester(suffix);
}

function St_scale() {
    return St.ThemeContext.get_for_stage(global.stage).scale_factor;
}

async function work() {
    log(`mode ${MODE}, scale ${St_scale()}`);
    check('the picker is part of this Shell', Boolean(share()));
    for (const [id, title, live] of [
        ['org.luma.test.Photos', 'Library', false],
        ['org.luma.test.Notes', 'Prairie', false],
        ['org.luma.test.Reel', 'Prairie 0.1, launch film', true],
    ]) {
        spawn(['python3', `${HERE}/window.py`, id, title, ...(live ? ['--live'] : [])], 'windows');
        await until(`window ${title}`, () => windowByTitle(title), 30000);
        await wait(400);
    }
    await wait(1500);
    const suffix = GLib.getenv('SUFFIX') ?? '';
    if (MODE === 'exp') {
        await streamExperiment();
    } else if (MODE === 'look') {
        await lookOnly(suffix);
    } else {
        await keyboardShare();
        await doubleClickShare();
        await screenShare();
        await escapeCancels();
        await resolvedRequester();
    }
    write(`checks${suffix}`, checks);
    log(`${checks.filter(c => !c.ok).length} failed of ${checks.length}`);
}

let started = false;
export function init() {
    GLib.mkdir_with_parents(OUT, 0o755);
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, Number(GLib.getenv('ORACLE_SETTLE') ?? 9000), () => {
        if (!started) {
            started = true;
            work().catch(e => {
                check('the probe ran to the end', false, `${e}\n${e.stack}`);
                write(`checks${GLib.getenv('SUFFIX') ?? ''}`, checks);
            }).finally(() => global.context.terminate());
        }
        return GLib.SOURCE_REMOVE;
    });
}

export async function run() {
    await new Promise(() => {});
}
