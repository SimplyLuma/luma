// SPDX-License-Identifier: Apache-2.0
// The Keycap key path on the installed Shell, driven by a virtual touchscreen
// at phone scale: run through run.sh with PRESENTATION_AUTOMATION=<this file>
// PRESENTATION_INITIAL_MODE=mobile PRESENTATION_MONITOR=1080x2424
// PRESENTATION_SCALE=3 (the Fold's outer panel, 360x808 at 3).
//
// Each check is a regression test for one key-path rule
// (docs/mobile/keycap-keyboard.md, "Key path"): paint cost per key event,
// commits at once (also into the Shell's unlock entry and with the main loop
// busy), applications resized above the keyboard, repeat bound to a live
// touch, press-order rollover, whole-cell and gap hit testing, sentence case
// under fast typing, popups that never swallow taps, haptics through
// feedbackd, and no per-key IBus traffic with a layout source.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Shell from 'gi://Shell';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as ModalDialog from 'resource:///org/gnome/shell/ui/modalDialog.js';

const results = [];
const out = GLib.getenv('PRESENTATION_OUT');
const source = GLib.getenv('PRESENTATION_SOURCE');
const sleep = ms => new Promise(resolve => GLib.timeout_add(GLib.PRIORITY_DEFAULT,
    ms, () => { resolve(); return GLib.SOURCE_REMOVE; }));
const now = () => GLib.get_monotonic_time();
function check(name, condition, detail = '') {
    results.push({name, ok: Boolean(condition), detail});
    console.log(`[keypath] ${condition ? 'PASS' : 'FAIL'} ${name} ${detail}`);
}
async function until(test, ms = 10000) {
    for (let waited = 0; waited < ms && !test(); waited += 50)
        await sleep(50);
    return test();
}
const read = path => {
    try {
        return new TextDecoder().decode(GLib.file_get_contents(path)[1]);
    } catch {
        return '';
    }
};
const frameOf = window => {
    const rect = window.get_frame_rect();
    return `${rect.width}x${rect.height}+${rect.x}+${rect.y}${window.is_fullscreen() ? ' fullscreen' : ''}`;
};
function descendants(actor, test, found = []) {
    for (const child of actor.get_children()) {
        if (test(child))
            found.push(child);
        descendants(child, test, found);
    }
    return found;
}

// Measures how long the keyboard itself takes to paint (St renders its
// backgrounds there) and how long each whole stage frame paints.
const PaintProbe = GObject.registerClass(
class PaintProbe extends Clutter.Effect {
    _init() {
        super._init();
        this.samples = [];
    }

    vfunc_pre_paint() {
        this._start = now();
        return true;
    }

    vfunc_post_paint() {
        this.samples.push({at: this._start, ms: (now() - this._start) / 1000});
    }
});

let keyboard, finger, monitor;
const seat = Clutter.get_default_backend().get_default_seat();

// The key on the current page that types `text` in either case (letters
// sit in the same place on both letter layers), or the delete key.
function keyFor(text) {
    const wanted = text.toLowerCase();
    for (const key of keyboard._currentPage.get_children()) {
        if (!key.keyButton || !key.mapped)
            continue;
        const labels = [key.keyButton.label, ...descendants(key, a => a instanceof St.Label &&
            !a.has_style_class_name('luma-keycap-hint')).map(a => a.text)]
            .filter(Boolean).map(label => label.toLowerCase());
        if (text === 'delete' ? key._iconName === 'osk-delete-symbolic'
            : text === 'shift' ? key._iconName === 'osk-shift-symbolic'
                : labels.includes(wanted))
            return key;
    }
    return null;
}
function centre(actor) {
    const box = actor.get_transformed_extents();
    return [box.origin.x + box.size.width / 2, box.origin.y + box.size.height / 2];
}
const down = (slot, x, y) => finger.notify_touch_down(now(), slot, x, y);
const up = slot => finger.notify_touch_up(now(), slot);
async function tap(text, hold = 50, gap = 40) {
    const key = keyFor(text);
    if (!key)
        return false;
    const [x, y] = centre(key);
    down(0, x, y);
    await sleep(hold);
    up(0);
    await sleep(gap);
    return true;
}

async function work() {
    const presentation = Main.presentation;
    const settings = new Gio.Settings({schema_id: 'org.project_luma.shell-state'});
    settings.set_string('presentation-mode', 'mobile');
    await until(() => presentation?.mode === 'mobile');
    monitor = Main.layoutManager.primaryMonitor;
    const scale = monitor.geometry_scale;
    check('Mobile presentation at phone scale', presentation?.mode === 'mobile' && scale === 3,
        `${monitor.width}x${monitor.height}@${scale}`);
    GLib.unlink(GLib.build_filenamev([GLib.get_user_data_dir(), 'luma', 'keycap-words.json']));
    new Gio.Settings({schema_id: 'org.gnome.shell.keyboard'}).set_boolean('auto-correct', false);

    // A stand-in feedbackd that D-Bus activates on the first key press.
    const feedbackLog = `${out}/feedback.log`;
    GLib.unlink(feedbackLog);
    // Session buses read activatable services from the user's and the
    // system's data directories; the fixture container may use either.
    for (const dir of [GLib.get_user_data_dir(), '/usr/local/share']) {
        const services = GLib.build_filenamev([dir, 'dbus-1', 'services']);
        GLib.mkdir_with_parents(services, 0o755);
        try {
            GLib.file_set_contents(`${services}/org.sigxcpu.Feedback.service`,
                `[D-BUS Service]\nName=org.sigxcpu.Feedback\nExec=/usr/bin/python3 ${source}/tests/gnome-shell/presentation/feedback-service.py ${feedbackLog}\n`);
        } catch {}
    }
    await new Promise(resolve => Gio.DBus.session.call('org.freedesktop.DBus', '/org/freedesktop/DBus',
        'org.freedesktop.DBus', 'ReloadConfig', null, null, Gio.DBusCallFlags.NONE, -1, null, () => resolve()));

    const typed = `${out}/typed.txt`;
    const context = global.create_app_launch_context(0, -1);
    Gio.AppInfo.create_from_commandline(
        `python3 ${source}/tests/gnome-shell/presentation/typing-window.py ${typed}`,
        'Typing fixture', Gio.AppInfoCreateFlags.NONE).launch([], context);
    const full = `${monitor.width}x${monitor.height}+${monitor.x}+${monitor.y} fullscreen`;
    const app = () => global.display.focus_window?.get_wm_class()?.includes('TypingFixture')
        ? global.display.focus_window : null;
    await until(() => app() && frameOf(app()) === full);
    const window = app();
    check('application opens full screen', window && frameOf(window) === full, window ? frameOf(window) : 'no window');
    if (!window)
        return;

    const a11y = new Gio.Settings({schema_id: 'org.gnome.desktop.a11y.applications'});
    a11y.set_boolean('screen-keyboard-enabled', true);
    await until(() => Main.keyboard.keyboardActor);
    Main.keyboard.open(Main.layoutManager.primaryIndex);
    await until(() => Main.keyboard.visible);
    await sleep(1500);
    keyboard = Main.keyboard.keyboardActor;
    finger = seat.create_virtual_device(Clutter.InputDeviceType.TOUCHSCREEN_DEVICE);

    // adjustResize: Mobile shrinks the application above the keyboard and
    // keeps it full screen; it grows back when the keyboard hides.
    const above = `${monitor.width}x${monitor.height - keyboard.height}+${monitor.x}+${monitor.y} fullscreen`;
    await until(() => frameOf(window) === above, 3000);
    check('application resizes above the keyboard (adjustResize)', frameOf(window) === above,
        `${frameOf(window)}, expected ${above}`);

    // Every commit and deletion the keyboard sends, with its time.
    const commits = [];
    const commit = Main.inputMethod.commit;
    Main.inputMethod.commit = text => {
        commits.push({text, at: now()});
        return commit.call(Main.inputMethod, text);
    };
    const deletes = [];
    const deleteSurrounding = Main.inputMethod.delete_surrounding;
    Main.inputMethod.delete_surrounding = (offset, count) => {
        deletes.push({count, at: now()});
        return deleteSurrounding.call(Main.inputMethod, offset, count);
    };
    const backspaces = [];
    const keyvalPress = keyboard._keyboardController.keyvalPress;
    keyboard._keyboardController.keyvalPress = keyval => {
        if (keyval === Clutter.KEY_BackSpace)
            backspaces.push(now());
        return keyvalPress.call(keyboard._keyboardController, keyval);
    };
    const appText = () => read(typed);
    // Empty the field the way a person would (select all, delete).
    const reset = async () => {
        keyboard._latched = false;
        await keyboard._keyboardController.sendKeyval(Clutter.KEY_a, [Clutter.KEY_Control_L]);
        await keyboard._keyboardController.sendKeyval(Clutter.KEY_BackSpace);
        await until(() => appText() === '', 2000);
        await sleep(300);
    };

    // 1. Paint cost of key events: no frame repaints the keyboard's
    // background on a press or release, so painting the keyboard costs
    // under a frame and so does each stage frame around taps.
    const probe = new PaintProbe();
    keyboard.add_effect(probe);
    const frames = [];
    let paintStart = 0;
    const stageIds = [
        global.stage.connect('before-paint', () => (paintStart = now())),
        global.stage.connect('after-paint', () => frames.push({at: paintStart, ms: (now() - paintStart) / 1000})),
    ];
    await sleep(300);
    probe.samples = [];
    frames.length = 0;
    const tapTimes = [];
    keyboard._setActiveLevel('default');
    keyboard._latched = true;
    await sleep(300);
    // The press path itself: how long handling a TOUCH_BEGIN takes, how
    // often it reads actor geometry, and whether it restyles anything.
    const pressHandling = [];
    const handle = keyboard.handleKeyEvent;
    keyboard.handleKeyEvent = function (event) {
        const start = now();
        const type = event.type();
        try {
            return handle.call(this, event);
        } finally {
            if (type === Clutter.EventType.TOUCH_BEGIN)
                pressHandling.push((now() - start) / 1000);
        }
    };
    let geometryReads = 0, counting = false;
    const geometry = ['get_transformed_extents', 'get_transformed_position', 'get_transformed_size']
        .map(name => [name, Clutter.Actor.prototype[name]]);
    for (const [name, original] of geometry) {
        Clutter.Actor.prototype[name] = function (...args) {
            if (counting)
                geometryReads++;
            return original.apply(this, args);
        };
    }
    const nodes = actor => [actor, ...descendants(actor, () => true)].filter(a => a instanceof St.Widget)
        .map(a => a.get_theme_node());
    let restyled = 0;
    let presses = 0;
    for (const letter of 'thequickbrownfoxjumpsoverthelazydog') {
        const key = keyFor(letter);
        const [x, y] = centre(key);
        const before = nodes(key);
        tapTimes.push(now());
        counting = true;
        down(0, x, y);
        await sleep(70);
        counting = false;
        presses++;
        const during = nodes(key);
        if (during.some((node, i) => node !== before[i]))
            restyled++;
        tapTimes.push(now());
        up(0);
        await sleep(90);
    }
    keyboard.handleKeyEvent = handle;
    for (const [name, original] of geometry)
        Clutter.Actor.prototype[name] = original;
    await sleep(300);
    const sortedPress = [...pressHandling].sort((a, b) => a - b);
    const pressMedian = sortedPress[Math.floor(sortedPress.length / 2)] ?? Infinity;
    const pressP90 = sortedPress[Math.floor(sortedPress.length * 0.9)] ?? Infinity;
    check('handling a press takes well under its 2 ms device budget', pressMedian < 0.75 && pressP90 < 1.5,
        `TOUCH_BEGIN median ${pressMedian.toFixed(3)} ms, p90 ${pressP90.toFixed(3)} ms, max ${(sortedPress.at(-1) ?? 0).toFixed(3)} ms over ${sortedPress.length}`);
    check('a press reads actor geometry at most once', geometryReads <= presses,
        `${geometryReads} geometry reads over ${presses} presses`);
    check('a press restyles nothing (the cap and its labels keep their theme nodes)', restyled === 0,
        `${restyled} of ${presses} presses restyled the pressed key`);
    stageIds.forEach(id => global.stage.disconnect(id));
    keyboard.remove_effect(probe);
    // The frame that shows a press (the cap going down, the preview) and
    // the one that shows its release (the cap coming up, the strip) are the
    // keyboard's own; later frames composite the application's new text.
    const firstFrameAfter = time => frames.find(frame => frame.at >= time);
    const pressFrames = tapTimes.filter((_t, i) => i % 2 === 0).map(firstFrameAfter).filter(Boolean);
    const releaseFrames = tapTimes.filter((_t, i) => i % 2 === 1).map(firstFrameAfter).filter(Boolean);
    // The session's very first press also rasterises its preview glyph and
    // the first pressed cap for the first time; it is reported, not judged.
    const keyFrames = [...pressFrames.slice(1), ...releaseFrames].map(frame => frame.ms);
    const afterTap = sample => tapTimes.some(t => sample.at >= t && sample.at - t < 150000);
    const allFrames = frames.filter(afterTap).map(frame => frame.ms);
    const worstKey = Math.max(0, ...keyFrames);
    const median = list => [...list].sort((a, b) => a - b)[Math.floor(list.length / 2)] ?? 0;
    const stat = list => `median ${median(list).toFixed(2)} ms, worst ${Math.max(0, ...list).toFixed(2)} ms`;
    check('no press or release frame takes over 16.7 ms to paint (after the first press)', worstKey < 16.7,
        `press ${stat(pressFrames.map(f => f.ms))} [${pressFrames.map(f => f.ms.toFixed(1)).join(' ')}]; ` +
        `release ${stat(releaseFrames.map(f => f.ms))}; ` +
        `all frames around taps ${stat(allFrames)} over ${allFrames.length}; keyboard subtree worst ${Math.max(0, ...probe.samples.map(x => x.ms)).toFixed(2)} ms`);
    let direct = 'not tried';
    try {
        Gio.DBus.session.call_sync('org.sigxcpu.Feedback', '/org/sigxcpu/Feedback', 'org.sigxcpu.Feedback',
            'TriggerFeedback', new GLib.Variant('(ssa{sv}i)', ['test', 'probe', {}, -1]), null,
            Gio.DBusCallFlags.NONE, 2000, null);
        direct = 'ok';
    } catch (error) {
        direct = error.message;
    }
    check('a stand-in feedbackd was activated and asked for a quiet tick per press',
        await until(() => read(feedbackLog).split('\n').filter(l => l.includes('button-pressed quiet')).length >= 30, 3000),
        `${read(feedbackLog).split('\n').filter(l => l.includes('button-pressed')).length} feedback calls; direct call ${direct}`);

    // 2. Commit at once: a release commits within the event that ended it,
    // also with the main loop kept busy at the frame clock's priority (as
    // in the measured frame-clock spin), where idle callbacks never run.
    await reset();
    commits.length = 0;
    let spinning = true;
    const spin = GLib.idle_add(GLib.PRIORITY_HIGH_IDLE + 50, () => spinning);
    const latencies = [];
    for (const letter of 'busyloop') {
        const key = keyFor(letter);
        const [x, y] = centre(key);
        down(0, x, y);
        await sleep(40);
        const released = now();
        const seen = commits.length;
        up(0);
        // eslint-disable-next-line no-await-in-loop
        await until(() => commits.length > seen, 1000);
        latencies.push(commits.length > seen ? (commits[seen].at - released) / 1000 : Infinity);
        await sleep(30);
    }
    spinning = false;
    GLib.source_remove(spin);
    check('every release commits at once even with the main loop busy', Math.max(...latencies) < 16.7,
        `release-to-commit ${latencies.map(l => l.toFixed(1)).join(', ')} ms`);
    await sleep(500);
    check('the busy-loop text reached the application in order', appText().toLowerCase().endsWith('busyloop'), JSON.stringify(appText()));

    // 3. Two commits in one main-loop turn both reach the application
    // (Mutter keeps them until its done; the keyboard sends the second
    // right after the first done otherwise).
    await reset();
    await sleep(100);
    Main.inputMethod.commit('x');
    Main.inputMethod.commit('y');
    keyboard._keyboardController.replaceBefore(0, 'u');
    keyboard._keyboardController.replaceBefore(0, 'v');
    await sleep(500);
    check('several commits in one turn all arrive, in order', appText().endsWith('xyuv'), JSON.stringify(appText()));

    // 4. Rollover: an older held key types when a newer touch begins, so
    // overlapping taps type in press order whatever order they lift in.
    await reset();
    const [ax, ay] = centre(keyFor('a'));
    const [sx, sy] = centre(keyFor('s'));
    const [dx, dy] = centre(keyFor('d'));
    down(0, ax, ay);
    await sleep(40);
    down(1, sx, sy);
    await sleep(40);
    up(1);
    await sleep(20);
    up(0);
    await sleep(60);
    down(0, dx, dy);
    await sleep(30);
    up(0);
    await sleep(500);
    check('overlapping taps type in press order', appText().toLowerCase().endsWith('asd'), JSON.stringify(appText()));

    // 5. Hit testing: the gutter between two caps and the keyboard's own
    // margin type the nearest key.
    await reset();
    const g = keyFor('g').keyButton.get_transformed_extents();
    const h = keyFor('h').keyButton.get_transformed_extents();
    const gutterX = (g.origin.x + g.size.width + h.origin.x) / 2;
    const gutterY = g.origin.y + g.size.height / 2;
    down(0, gutterX, gutterY);
    await sleep(40);
    up(0);
    await sleep(60);
    const z = keyFor('z').keyButton.get_transformed_extents();
    const kb = keyboard.get_transformed_extents();
    // Below the bottom letter row, between it and the space row's gap.
    const p = keyFor('p').keyButton.get_transformed_extents();
    down(0, p.origin.x + p.size.width / 2, p.origin.y - 2);
    await sleep(40);
    up(0);
    await sleep(500);
    const gapText = appText().toLowerCase();
    check('a tap in the gutter between caps types a neighbouring key', /[gh]p$/.test(gapText) || /[gh]$/.test(gapText.slice(0, -1)),
        JSON.stringify(gapText));
    check('a tap on the keyboard margin above a cap types that key', gapText.endsWith('p'), JSON.stringify(gapText));
    void z;
    void kb;

    // 6. A held delete repeats while the finger is down and stops on the
    // release, and also when a grab takes the touch away from the keyboard.
    await reset();
    keyboard._latched = true;
    for (const letter of 'deletemeplease')
        await tap(letter, 30, 30);
    await sleep(400);
    const [ex, ey] = centre(keyFor('delete'));
    const deletedBefore = () => deletes.length + backspaces.length;
    let start = deletedBefore();
    down(0, ex, ey);
    await sleep(800);
    up(0);
    const atRelease = deletedBefore();
    await sleep(1200);
    check('a held delete repeats while held', atRelease - start >= 3, `${atRelease - start} deletions in 800 ms`);
    check('a held delete stops when the finger lifts', deletedBefore() === atRelease,
        `${deletedBefore() - atRelease} deletions after release`);
    start = deletedBefore();
    down(0, ex, ey);
    await sleep(700);
    const blocker = new St.Widget({reactive: true, width: 10, height: 10});
    Main.uiGroup.add_child(blocker);
    const grab = global.stage.grab(blocker);
    await sleep(50);
    up(0);
    const atLostRelease = deletedBefore();
    await sleep(1200);
    grab.dismiss();
    blocker.destroy();
    check('a delete whose release a grab took still stops', deletedBefore() === atLostRelease,
        `${deletedBefore() - atLostRelease} deletions after a grabbed release`);
    // The grab moved the input focus; give it back to the application.
    window.activate(global.get_current_time());
    await sleep(500);
    Main.keyboard.open(Main.layoutManager.primaryIndex);
    await until(() => Main.keyboard.visible && keyFor('q'), 3000);
    await sleep(400);

    // 7. Long press: the alternatives open on a hold, the release types the
    // chosen one, and the very next tap is typed (no popup swallows it).
    await reset();
    const [qx, qy] = centre(keyFor('q'));
    down(0, qx, qy);
    await sleep(700);
    up(0);
    await sleep(60);
    await tap('w', 40, 60);
    await tap('e', 40, 60);
    await sleep(500);
    check('a long press types its alternative and the next taps arrive', appText().endsWith('1we'), JSON.stringify(appText()));

    // 8. Sentence case decided locally under fast typing.
    await reset();
    keyboard._setLatched(false);
    keyboard._updateLevelFromHints(true);
    await sleep(300);
    const sentence = 'Hello. How are you. I am fine.';
    for (const character of sentence) {
        // eslint-disable-next-line no-await-in-loop
        await tap(character === ' ' ? ' ' : character, 35, 25);
    }
    await sleep(800);
    check('sentence case is right under fast typing', appText() === sentence, JSON.stringify(appText()));

    // 9. Typing into the Shell's own entries, with the main loop kept busy:
    // a password entry under the lock screen's session mode and modal grab
    // (the unlock prompt), and an entry in a modal dialog, whose grab hands
    // keyboard events over through KeyboardManager.maybeHandleEvent.
    a11y.set_boolean('screen-keyboard-enabled', true);
    Main.keyboard.close();
    await sleep(600);
    check('application is full screen again after the keyboard hides', await until(() => frameOf(window) === full, 3000),
        frameOf(window));
    const typeInto = async (entry, word) => {
        entry.clutter_text.grab_key_focus();
        await sleep(300);
        Main.keyboard.open(Main.layoutManager.primaryIndex);
        await until(() => Main.keyboard.visible, 3000);
        await sleep(800);
        keyboard = Main.keyboard.keyboardActor;
        keyboard._setActiveLevel('default');
        keyboard._latched = true;
        await sleep(300);
        let busy = true;
        const loop = GLib.idle_add(GLib.PRIORITY_HIGH_IDLE + 50, () => busy);
        for (const letter of word)
            await tap(letter, 40, 40);
        busy = false;
        GLib.source_remove(loop);
        await sleep(300);
        return entry.get_text();
    };
    if (Main.screenShield) {
        Main.screenShield.lock(false);
        await until(() => Main.sessionMode.isLocked, 5000);
        await sleep(1500);
        Main.screenShield._dialog?._showPrompt?.();
        await sleep(1500);
        const entry = Main.screenShield._dialog?._authPrompt?._entry;
        check('the on-screen keyboard types into the real unlock entry', entry && await typeInto(entry, 'secret') === 'secret',
            JSON.stringify(entry?.get_text()));
    }
    Main.sessionMode.pushMode('unlock-dialog');
    const lockEntry = new St.PasswordEntry({can_focus: true, width: 300, x: 30, y: 200});
    Main.uiGroup.add_child(lockEntry);
    const lockGrab = Main.pushModal(Main.uiGroup, {actionMode: Shell.ActionMode.UNLOCK_SCREEN});
    const lockText = await typeInto(lockEntry, 'secret');
    check('the on-screen keyboard types into a lock-screen password entry at once', lockText === 'secret',
        JSON.stringify(lockText));
    Main.keyboard.close();
    Main.popModal(lockGrab);
    lockEntry.destroy();
    Main.sessionMode.popMode('unlock-dialog');
    await sleep(600);
    const dialog = new ModalDialog.ModalDialog();
    const dialogEntry = new St.Entry({can_focus: true, width: 240});
    dialog.contentLayout.add_child(dialogEntry);
    dialog.open();
    await sleep(600);
    const dialogText = await typeInto(dialogEntry, 'search');
    check('the on-screen keyboard types into an entry under a modal grab', dialogText === 'search',
        JSON.stringify(dialogText));
    Main.keyboard.close();
    dialog.close();
    await sleep(400);
    dialog.destroy();

    // 10. With a keyboard layout input source, IBus is not told the caret
    // or the surrounding text on every keystroke.
    const sent = [];
    const fake = {
        set_surrounding_text: () => sent.push('surrounding'),
        set_cursor_location: () => sent.push('cursor'),
        needs_surrounding_text: () => false,
        set_capabilities() {},
        set_content_type() {},
    };
    const realContext = Main.inputMethod._context;
    Main.inputMethod._context = fake;
    const rect = new (await import('gi://Graphene')).default.Rect();
    rect.init(1, 2, 3, 4);
    Main.inputMethod.vfunc_set_surrounding('abc', 3, 3);
    Main.inputMethod.vfunc_set_cursor_location(rect);
    Main.inputMethod._context = realContext;
    check('a layout source sends IBus no caret or surrounding text', sent.length === 0, JSON.stringify(sent));

    // A long document: a surrounding-text report costs the same whatever its
    // length (only the tail before the caret is read), and the keyboard's
    // view of that tail is right, emoji included.
    const documentText = `${'lorem ipsum 😀 '.repeat(20000)}Hello. `;
    const caret = [...documentText].length;
    let started = now();
    Main.inputMethod.vfunc_set_surrounding(documentText, caret, caret);
    const reportMs = (now() - started) / 1000;
    const mirrored = keyboard._mirror.before;
    check('a surrounding-text report of a 300k-unit document is handled in under 3 ms', reportMs < 3,
        `${documentText.length} code units in ${reportMs.toFixed(3)} ms`);
    check('the keyboard keeps the right text before the caret of a long document',
        mirrored.endsWith('lorem ipsum 😀 Hello. ') && mirrored.length <= 400, JSON.stringify(mirrored.slice(-40)));
    Main.inputMethod.vfunc_set_surrounding('', 0, 0);
    // Word analysis runs when the Shell is idle, between frames: it must be
    // short enough never to hold the next touch.
    const analyses = [];
    for (const word of ['internationalizatoin', 'recieve', 'quikcly', 'teh', 'somethign']) {
        started = now();
        await keyboard._suggester.analyse(word);
        analyses.push((now() - started) / 1000);
    }
    const worstAnalysis = Math.max(...analyses.slice(1));
    check('analysing a word takes under 1 ms', worstAnalysis < 1,
        `${analyses.map(ms => ms.toFixed(2)).join(', ')} ms`);

    // Desktop keeps the platform behaviour: the keyboard keeps no inset and
    // applications are not resized.
    settings.set_string('presentation-mode', 'desktop');
    await until(() => presentation.mode === 'desktop');
    await sleep(1000);
    window.activate(global.get_current_time());
    await sleep(500);
    const desktopFrame = frameOf(window);
    Main.keyboard.open(Main.layoutManager.primaryIndex);
    await until(() => Main.keyboard.visible, 3000);
    await sleep(1200);
    const inset = global.display.get_input_panel_inset?.(Main.layoutManager.primaryIndex) ?? 0;
    check('Desktop keeps no keyboard inset', Main.keyboard.visible && inset === 0, `inset ${inset}`);
    check('Desktop does not resize the application for the keyboard', frameOf(window) === desktopFrame,
        `${frameOf(window)} (was ${desktopFrame})`);
    Main.keyboard.close();

    finger.run_dispose();
    Main.inputMethod.commit = commit;
    Main.inputMethod.delete_surrounding = deleteSurrounding;
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
