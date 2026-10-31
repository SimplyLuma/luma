// SPDX-License-Identifier: Apache-2.0
// The on-screen keyboard on the real lock screen (ScreenShield -> curtain ->
// UnlockDialog -> Prairie poster card -> AuthPrompt), driven by a virtual
// touchscreen at phone scale. Run through run.sh with
// PRESENTATION_AUTOMATION=<this file> PRESENTATION_INITIAL_MODE=mobile
// PRESENTATION_MONITOR=1080x2424 PRESENTATION_SCALE=3.
//
// Headless sessions have no GDM or logind, so the screen shield is created
// here when the Shell did not create one, and the GDM user verifier is a
// stand-in that asks for a password and records the answer. Everything else
// (the shield, curtain, swipe, prompt, entry, keyboard) is the real Shell.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as ScreenShield from 'resource:///org/gnome/shell/ui/screenShield.js';
import * as GdmUtil from 'resource:///org/gnome/shell/gdm/util.js';

const results = [];
const out = GLib.getenv('PRESENTATION_OUT');
const source = GLib.getenv('PRESENTATION_SOURCE');
const sleep = ms => new Promise(resolve => GLib.timeout_add(GLib.PRIORITY_DEFAULT,
    ms, () => { resolve(); return GLib.SOURCE_REMOVE; }));
const now = () => GLib.get_monotonic_time();
function check(name, condition, detail = '') {
    results.push({name, ok: Boolean(condition), detail});
    console.log(`[keycap-lock] ${condition ? 'PASS' : 'FAIL'} ${name} ${detail}`);
}
async function until(test, ms = 10000) {
    for (let waited = 0; waited < ms && !test(); waited += 50)
        await sleep(50);
    return test();
}
const box = actor => {
    const extents = actor.get_transformed_extents();
    return {x: extents.origin.x, y: extents.origin.y, w: extents.size.width, h: extents.size.height};
};

// A stand-in for GDM's user verifier: asks for a password once verification
// begins, and records what the prompt answers.
const answers = [];
const proto = GdmUtil.ShellUserVerifier.prototype;
proto.begin = function () {
    GLib.idle_add(GLib.PRIORITY_DEFAULT, () => {
        this.emit('ask-question', GdmUtil.PASSWORD_SERVICE_NAME ?? 'gdm-password', 'Password:', true);
        return GLib.SOURCE_REMOVE;
    });
};
proto.answerQuery = function (serviceName, answer) {
    answers.push(answer);
};
for (const name of ['cancel', 'clear', 'destroy'])
    proto[name] = function () {};
proto.serviceIsDefault = () => false;
proto.serviceIsFingerprint = () => false;
proto.foregroundServiceDeterminesUsername = () => false;
Object.defineProperty(proto, 'hasPendingMessages', {get: () => false, configurable: true});

async function work() {
    const settings = new Gio.Settings({schema_id: 'org.project_luma.shell-state'});
    settings.set_string('presentation-mode', 'mobile');
    await until(() => Main.presentation?.mode === 'mobile');
    const monitor = Main.layoutManager.primaryMonitor;

    const typed = `${out}/typed.txt`;
    Gio.AppInfo.create_from_commandline(
        `python3 ${source}/tests/gnome-shell/presentation/typing-window.py ${typed}`,
        'Typing fixture', Gio.AppInfoCreateFlags.NONE).launch([], global.create_app_launch_context(0, -1));
    await until(() => global.display.focus_window?.get_wm_class()?.includes('TypingFixture'));
    await sleep(1500);
    const a11y = new Gio.Settings({schema_id: 'org.gnome.desktop.a11y.applications'});
    a11y.set_boolean('screen-keyboard-enabled', true);
    await until(() => Main.keyboard.keyboardActor);
    Main.keyboard.open(Main.layoutManager.primaryIndex);
    await until(() => Main.keyboard.visible);
    await sleep(1200);
    check('keyboard is open for the application', Main.keyboard.visible);

    // Lock, as loginctl lock-session does.
    const shield = Main.screenShield ?? new ScreenShield.ScreenShield();
    shield.lock(false);
    await until(() => Main.sessionMode.isLocked, 5000);
    await sleep(1500);
    check('session is locked', Main.sessionMode.isLocked);
    check('the keyboard opened for the application hides on lock', !Main.keyboard.visible,
        `visible ${Main.keyboard.visible}`);
    // An application still holding a text field asks for the keyboard
    // (its text-input enable), as the GTK probe does on the phone: on the
    // curtain that must not bring the keyboard back over the lock screen.
    Main.inputMethod.set_input_panel_state(Clutter.InputPanelState.ON);
    await sleep(1200);
    check('an application asking for the keyboard while locked does not open it', !Main.keyboard.visible,
        `visible ${Main.keyboard.visible}`);
    const dialog = shield._dialog;
    check('the unlock dialog shows its curtain', dialog?.visible && dialog._activePage === dialog._clock,
        `page ${dialog?._activePage === dialog?._clock ? 'clock' : dialog?._activePage}`);

    // Lift the curtain with a swipe up from the middle of the screen.
    const finger = Clutter.get_default_backend().get_default_seat()
        .create_virtual_device(Clutter.InputDeviceType.TOUCHSCREEN_DEVICE);
    const x = monitor.x + monitor.width / 2;
    let y = monitor.y + monitor.height * 0.7;
    finger.notify_touch_down(now(), 0, x, y);
    for (let step = 0; step < 14; step++) {
        await sleep(16);
        y -= monitor.height * 0.03;
        finger.notify_touch_motion(now(), 0, x, y);
    }
    await sleep(16);
    finger.notify_touch_up(now(), 0);
    await sleep(1500);
    const entry = dialog?._authPrompt?._entry;
    check('a swipe up lifts the curtain to the password prompt', dialog?._activePage === dialog?._promptBox && entry?.mapped,
        `entry ${entry?.mapped ? 'mapped' : 'not mapped'}`);
    if (!entry)
        return;
    await until(() => Main.keyboard.visible, 3000);
    await sleep(800);
    check('the keyboard opens for the password prompt', Main.keyboard.visible);
    const keyboard = Main.keyboard.keyboardActor;
    const keyboardTop = monitor.y + monitor.height - keyboard.height;
    const entryBox = box(entry);
    check('the password entry sits above the keyboard', entryBox.y + entryBox.h <= keyboardTop + 1 && entryBox.y >= monitor.y,
        `entry ${Math.round(entryBox.y)}..${Math.round(entryBox.y + entryBox.h)}, keyboard top ${Math.round(keyboardTop)}`);
    check('the password entry is visible', entry.get_paint_opacity() > 200 && dialog._promptBox.opacity > 200,
        `opacity ${entry.get_paint_opacity()}`);

    // Type the password on the on-screen keyboard and press Enter.
    const keyFor = text => keyboard._currentPage.get_children().find(key => key.keyButton && key.mapped &&
        (text === 'enter' ? key.keyButton.has_style_class_name('luma-keycap-enter')
            : key.character?.toLowerCase() === text));
    keyboard._setActiveLevel('default');
    keyboard._latched = true;
    await sleep(300);
    for (const letter of ['s', 'e', 'c', 'r', 'e', 't', 'enter']) {
        const key = keyFor(letter);
        if (!key) {
            check(`key ${letter} is on the keyboard`, false);
            break;
        }
        const k = box(key);
        finger.notify_touch_down(now(), 0, k.x + k.w / 2, k.y + k.h / 2);
        await sleep(50);
        finger.notify_touch_up(now(), 0);
        await sleep(80);
    }
    await sleep(800);
    check('the typed password reaches the prompt and is submitted', answers.includes('secret'),
        `answers ${JSON.stringify(answers)}, entry ${JSON.stringify(entry.get_text?.() ?? '')}`);
    finger.run_dispose();
    a11y.reset('screen-keyboard-enabled');
}

export function init() {
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, 6000, () => {
        work().catch(error => check('scenario completed', false, `${error}\n${error.stack}`)).finally(() => {
            GLib.file_set_contents(`${out}/results.json`, JSON.stringify(results, null, 2));
            global.context.terminate();
        });
        return GLib.SOURCE_REMOVE;
    });
}
export async function run() { await new Promise(() => {}); }
