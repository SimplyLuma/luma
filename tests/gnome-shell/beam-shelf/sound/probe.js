// Sound oracle: drives the volume slider, the volume keys and other event
// sounds, and logs when each happened so the recording can be read.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const log = (m, o = {}) => console.log(`[snd] ${m} ${JSON.stringify({t: GLib.get_real_time() / 1e6, ...o})}`);
const sleep = ms => new Promise(r => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => { r(); return GLib.SOURCE_REMOVE; }));

export function init() {
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, 14000, () => {
        work().catch(e => log('error', {e: `${e}`, stack: `${e.stack}`})).finally(() => global.context.terminate());
        return GLib.SOURCE_REMOVE;
    });
}
export async function run() {
    await new Promise(() => {});
}

async function work() {
    const quick = Main.panel.statusArea.quickSettings;
    const output = quick._volumeOutput?._output;
    const osd = () => {
        const w = Main.osdWindowManager._osdWindows[0];
        return {visible: w.visible, value: w._value?.text ?? w._label?.text ?? null,
            accessible: w._capsule?.accessible_name ?? null, glyph: w._glyph?._glyph ?? null};
    };
    const lm = Main.layoutManager;
    log('startup', {startingUp: lm._startingUp, background: !!lm._systemBackground, cover: !!lm._coverPane,
        primary: !!lm.primaryMonitor, showOverview: Main.sessionMode.showOverviewOnStartup, hasOverview: Main.sessionMode.hasOverview,
        overviewAnim: Main.overview._shown ?? null});
    // With an automation script the Shell connects its startup-complete
    // handler after awaiting the script's import; when startup finishes first
    // the action mode stays NONE and no keybinding fires. A real session has
    // no such await. Ending an empty modal sets the mode the handler would.
    if (Main.actionMode === 0 && !lm._startingUp) {
        const dummy = new (await import('gi://St')).default.Widget();
        Main.uiGroup.add_child(dummy);
        Main.popModal(Main.pushModal(dummy));
        dummy.destroy();
        log('action-mode-normalised', {actionMode: Main.actionMode});
    }
    log('shell', {actionMode: Main.actionMode, modalCount: Main.modalCount, focus: `${global.stage.key_focus}`,
        mode: Main.sessionMode.currentMode, overview: Main.overview.visible});
    log('start', {stream: output?.stream?.get_name?.() ?? null, state: output?.stream?.state ?? null,
        volume: output?.stream?.volume ?? null, theme: new Gio.Settings({schema_id: 'org.gnome.desktop.sound'}).get_string('theme-name')});
    const player = global.display.get_sound_player();
    const quiet = async ms => {
        await sleep(ms);
    };

    await quiet(1500);
    log('action', {name: 'event-bell'});
    player.play_from_theme('bell-window-system', 'oracle bell', null);
    await quiet(2500);

    for (const value of [0.3, 0.55, 0.8]) {
        log('action', {name: 'slider', value});
        output.slider.value = value;
        await quiet(2000);
        log('after', {name: 'slider', value, volume: output.stream.volume, state: output.stream.state, osd: osd()});
    }

    const seat = Clutter.get_default_backend().get_default_seat();
    const keyboard = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
    const now = () => GLib.get_monotonic_time();
    const press = async (keyval, modifiers = []) => {
        for (const m of modifiers)
            keyboard.notify_keyval(now(), m, Clutter.KeyState.PRESSED);
        keyboard.notify_keyval(now(), keyval, Clutter.KeyState.PRESSED);
        await sleep(40);
        keyboard.notify_keyval(now(), keyval, Clutter.KeyState.RELEASED);
        for (const m of modifiers.reverse())
            keyboard.notify_keyval(now(), m, Clutter.KeyState.RELEASED);
    };
    const keys = [
        ['key-volume-down', Clutter.KEY_AudioLowerVolume, []],
        ['key-volume-up', Clutter.KEY_AudioRaiseVolume, []],
        ['key-volume-up-precise', Clutter.KEY_AudioRaiseVolume, [Clutter.KEY_Shift_L]],
        ['key-volume-up-quiet', Clutter.KEY_AudioRaiseVolume, [Clutter.KEY_Alt_L]],
        ['key-mute', Clutter.KEY_AudioMute, []],
        ['key-unmute', Clutter.KEY_AudioMute, []],
    ];
    for (const [name, keyval, modifiers] of keys) {
        log('action', {name});
        await press(keyval, modifiers);
        await sleep(500);
        log('osd', {name, osd: osd(), volume: output.stream.volume, muted: output.stream.is_muted,
            actionMode: Main.actionMode, modalCount: Main.modalCount});
        await quiet(1700);
    }

    log('action', {name: 'event-dialog-information'});
    player.play_from_theme('dialog-information', 'oracle information', null);
    await quiet(2500);
    log('done');
}
