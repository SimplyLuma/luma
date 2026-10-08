// SPDX-License-Identifier: GPL-2.0-or-later
// Shared helpers for the movable-islands oracle (ADR-044): logging, checks,
// screenshots, geometry dumps, virtual input and arrangement writes.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as MessageTray from 'resource:///org/gnome/shell/ui/messageTray.js';

export const OUT = GLib.getenv('SA_OUT') ?? '/tmp/shelf-arrange';
export const results = [];
export const sleep = ms => new Promise(r => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => { r(); return GLib.SOURCE_REMOVE; }));
// Wait up to `ms` for `cond()` to hold; true when it did.
export async function until(cond, ms = 8000) {
    for (let t = 0; t < ms; t += 100) {
        if (cond())
            return true;
        await sleep(100);
    }
    return !!cond();
}
export const log = m => console.log(`[shelfarrange] ${m}`);
export function check(name, ok, detail = '') {
    results.push({name, ok: !!ok, detail});
    log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ` — ${detail}` : ''}`);
    return !!ok;
}
export const descend = (a, out = []) => { out.push(a); a.get_children?.().forEach(c => descend(c, out)); return out; };
export const settings = () => new Gio.Settings({schema_id: 'org.project_luma.shell-state'});

Gio._promisify(Shell.Screenshot.prototype, 'screenshot_stage_to_content');
Gio._promisify(Shell.Screenshot, 'composite_to_stream');
export async function shot(name, rect = null) {
    const [content, scale] = await new Shell.Screenshot().screenshot_stage_to_content();
    let {x, y, width, height} = rect ?? {x: 0, y: 0, width: global.stage.width, height: global.stage.height};
    x = Math.max(0, Math.round(x)); y = Math.max(0, Math.round(y));
    width = Math.round(Math.min(width, global.stage.width - x));
    height = Math.round(Math.min(height, global.stage.height - y));
    const stream = Gio.File.new_for_path(`${OUT}/${name}.png`).replace(null, false, Gio.FileCreateFlags.NONE, null);
    await Shell.Screenshot.composite_to_stream(content.get_texture(), x, y, width, height, scale, null, 0, 0, 1, stream);
    stream.close(null);
}

export function rectOf(actor) {
    const e = actor.get_transformed_extents();
    return {x: Math.round(e.get_x()), y: Math.round(e.get_y()),
        width: Math.round(e.get_width()), height: Math.round(e.get_height())};
}
export const intersects = (a, b) => a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;
export const inside = (a, b, slack = 0) => a.x >= b.x - slack && a.y >= b.y - slack &&
    a.x + a.width <= b.x + b.width + slack && a.y + a.height <= b.y + b.height + slack;

// The groups as drawn: placement and every visible island's rectangle.
export function groups() {
    return Main.shelf._groups.filter(g => g.placement).map(g => ({
        edge: g.placement.edge, anchor: g.placement.anchor, monitor: g.placement.monitor,
        visible: g.visible, rect: rectOf(g),
        islands: g.row.get_children().filter(a => a.visible).map(a => ({id: a.islandId ?? a.style_class, rect: rectOf(a)})),
    }));
}

export function workArea(index = Main.layoutManager.primaryIndex) {
    const w = Main.layoutManager.getWorkAreaForMonitor(index);
    return {x: w.x, y: w.y, width: w.width, height: w.height};
}

export async function waitForShelf() {
    for (let i = 0; i < 80 && !Main.shelf?._groups?.length; i++)
        await sleep(250);
    await sleep(3000);
    return Main.shelf;
}

// Write an arrangement and wait for the shelf to follow.
export async function arrange(groups, wait = 1500) {
    const variant = new GLib.Variant('aa{sv}', groups.map(g => ({
        display: new GLib.Variant('s', g.display ?? ''),
        edge: new GLib.Variant('s', g.edge),
        anchor: new GLib.Variant('s', g.anchor ?? 'center'),
        position: new GLib.Variant('d', g.position ?? 0.5),
        islands: new GLib.Variant('as', g.islands),
    })));
    settings().set_value('shelf-arrangement', variant);
    await sleep(wait);
}
export async function reset(wait = 1500) {
    settings().reset('shelf-arrangement');
    await sleep(wait);
}
export function stored() {
    return settings().get_value('shelf-arrangement').recursiveUnpack();
}

export async function setMode(mode) {
    new Gio.Settings({schema_id: 'org.gnome.desktop.interface'}).set_string('color-scheme', mode === 'light' ? 'default' : 'prefer-dark');
    settings().set_string('surface-treatment', mode);
    await sleep(2500);
}

let pointer, keyboard;
const now = () => GLib.get_monotonic_time();
export function input() {
    const seat = Clutter.get_default_backend().get_default_seat();
    pointer ??= seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
    keyboard ??= seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
    return {pointer, keyboard};
}
export async function move(x, y, wait = 30) {
    input().pointer.notify_absolute_motion(now(), x, y);
    await sleep(wait);
}
export async function press(button = Clutter.BUTTON_PRIMARY) {
    input().pointer.notify_button(now(), button, Clutter.ButtonState.PRESSED);
    await sleep(30);
}
export async function release(button = Clutter.BUTTON_PRIMARY) {
    input().pointer.notify_button(now(), button, Clutter.ButtonState.RELEASED);
    await sleep(30);
}
export async function key(keyval, mods = [], wait = 250) {
    const k = input().keyboard;
    for (const m of mods) k.notify_keyval(now(), m, Clutter.KeyState.PRESSED);
    k.notify_keyval(now(), keyval, Clutter.KeyState.PRESSED);
    k.notify_keyval(now(), keyval, Clutter.KeyState.RELEASED);
    for (const m of [...mods].reverse()) k.notify_keyval(now(), m, Clutter.KeyState.RELEASED);
    await sleep(wait);
}
export const centre = r => [r.x + r.width / 2, r.y + r.height / 2];

// Drag from one point to another in steps, the way a hand does.
export async function drag(from, to, {hold = 520, steps = 24, stepWait = 16, before = null} = {}) {
    await move(...from, 100);
    await press();
    await sleep(hold);
    for (let i = 1; i <= steps; i++) {
        const t = i / steps;
        await move(from[0] + (to[0] - from[0]) * t, from[1] + (to[1] - from[1]) * t, stepWait);
    }
    await sleep(250);
    const seen = before?.();
    await release();
    await sleep(700);
    return seen;
}

// A notification so the notifications island shows.
let source;
export function notify(title = 'Tea is ready', body = 'Your timer finished') {
    if (!source) {
        source = new MessageTray.Source({title: 'Clock', iconName: 'alarm-symbolic'});
        source.connect('destroy', () => (source = null));
        Main.messageTray.add(source);
    }
    const n = new MessageTray.Notification({source, title, body});
    source.addNotification(n);
    return n;
}

// A window of an app that exists on the image, for struts and tiling.
export async function openWindow() {
    const app = Shell.AppSystem.get_default().lookup_app('org.gnome.Ptyxis.desktop') ??
        Shell.AppSystem.get_default().lookup_app('org.gnome.Console.desktop') ??
        Shell.AppSystem.get_default().get_installed().map(i => Shell.AppSystem.get_default().lookup_app(i.get_id())).find(a => a);
    return app;
}

export function finish(name) {
    GLib.file_set_contents(`${OUT}/${name}.json`, JSON.stringify(results, null, 1));
    log(`DONE ${results.filter(r => r.ok).length}/${results.length} passed`);
    global.context.terminate();
}

export function start(name, work, delay = 6000) {
    GLib.mkdir_with_parents(OUT, 0o755);
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, delay, () => {
        work().catch(e => check('scenario ran without errors', false, `${e}\n${e.stack}`)).finally(() => finish(name));
        return GLib.SOURCE_REMOVE;
    });
}

// Lay the virtual monitors out side by side, left to right, in the order of
// their sizes in `order` ('WxH' strings; a trailing '*' marks the primary),
// top-aligned, through Mutter's DisplayConfig. Returns once the Shell has
// the new layout.
export async function layoutMonitors(order) {
    const bus = Gio.DBus.session;
    // Mutter answers in this process: call asynchronously, never sync.
    const call = (method, params) => new Promise((resolve, reject) => bus.call('org.gnome.Mutter.DisplayConfig',
        '/org/gnome/Mutter/DisplayConfig', 'org.gnome.Mutter.DisplayConfig', method, params,
        null, Gio.DBusCallFlags.NONE, -1, null, (b, res) => {
            try { resolve(b.call_finish(res)); } catch (e) { reject(e); }
        }));
    const state = (await call('GetCurrentState', null)).recursiveUnpack();
    const [serial, physical] = state;
    const outputs = physical.map(([[connector], modes]) => {
        const mode = modes.find(m => m[6]?.['is-current']) ?? modes[0];
        return {connector, mode: mode[0], width: mode[1], height: mode[2]};
    });
    const used = new Set();
    let x = 0;
    const logical = [];
    for (const spec of order) {
        // WxH, WxH* (primary), optionally +X+Y for an explicit position
        // (monitors are otherwise placed left to right, top-aligned).
        const [size, px, py] = spec.split('+');
        const primary = size.endsWith('*');
        const [w, h] = size.replace('*', '').split('x').map(Number);
        const out = outputs.find(o => !used.has(o.connector) && o.width === w && o.height === h);
        if (!out)
            throw new Error(`no monitor ${spec}`);
        used.add(out.connector);
        logical.push([px !== undefined ? Number(px) : x, py !== undefined ? Number(py) : 0, 1.0, 0, primary,
            [[out.connector, out.mode, {}]]]);
        x += w;
    }
    const before = Main.layoutManager.monitors.map(m => m.x).join();
    await call('ApplyMonitorsConfig', new GLib.Variant('(uua(iiduba(ssa{sv}))a{sv})', [serial, 1, logical, {}]));
    for (let i = 0; i < 40 && Main.layoutManager.monitors.map(m => m.x).join() === before; i++)
        await sleep(250);
    await sleep(2000);
}
