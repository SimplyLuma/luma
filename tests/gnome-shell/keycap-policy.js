// SPDX-License-Identifier: GPL-2.0-or-later
// Keycap policy and its wiring into the Shell keyboard.
// Run: gjs -m keycap-policy.js js/ui/lumaPresentation/keycap.js js/ui/keyboard.js
// (node keycap-policy.js ... works too). The cases mirror Stevia's
// test-keycap-history.c so both keyboards keep one behaviour.
const gjs = typeof globalThis.ARGV !== 'undefined';
const args = gjs ? globalThis.ARGV : process.argv.slice(2);
const GLib = gjs ? (await import('gi://GLib')).default : null;
const fs = gjs ? null : await import('node:fs');
const cwd = gjs ? GLib.get_current_dir() : process.cwd();
const absolute = path => (path.startsWith('/') ? path : `${cwd}/${path}`);
const read = path => (gjs ? new TextDecoder().decode(GLib.file_get_contents(path)[1])
    : fs.readFileSync(path, 'utf8'));

function assert(value, message) {
    if (!value)
        throw new Error(`FAIL: ${message}`);
}

const Keycap = await import(`file://${absolute(args[0])}`);
const keyboard = read(args[1]);

// Clutter's wire values (InputContentPurpose, InputContentHintFlags).
const purpose = {NORMAL: 0, ALPHA: 1, DIGITS: 2, NUMBER: 3, PHONE: 4, URL: 5, EMAIL: 6,
    NAME: 7, PASSWORD: 8, DATE: 9, TIME: 10, DATETIME: 11, TERMINAL: 12};
const hint = {COMPLETION: 1, SPELLCHECK: 2, AUTO_CAPITALIZATION: 4, LOWERCASE: 8,
    UPPERCASE: 16, TITLECASE: 32, HIDDEN_TEXT: 64, SENSITIVE_DATA: 128, LATIN: 256, MULTILINE: 512};
const policy = new Keycap.KeycapPolicy({purpose, hint});
const pinPolicy = new Keycap.KeycapPolicy({purpose: {...purpose, PIN: 13}, hint});
const sensitiveHints = hint.HIDDEN_TEXT | hint.SENSITIVE_DATA;

assert(policy.isSensitive(purpose.NORMAL, sensitiveHints), 'hidden sensitive mask (192) is sensitive');
assert(pinPolicy.isSensitive(13, 0), 'PIN purpose is sensitive where the compositor reports it');
assert(policy.isSensitive(purpose.PASSWORD, 0), 'password purpose is sensitive');
assert(policy.isSensitive(purpose.NORMAL, hint.HIDDEN_TEXT), 'hidden text is sensitive');
assert(!policy.isSensitive(purpose.NORMAL, hint.AUTO_CAPITALIZATION), 'auto-capitalisation is not sensitive');
assert(policy.shouldCapitalise(purpose.NORMAL, 0, 'Hello! ', 7), 'capital after a sentence end');
assert(policy.shouldCapitalise(purpose.NORMAL, 0, '', 0), 'capital at the start');
assert(!policy.shouldCapitalise(purpose.URL, 0, '', 0), 'no automatic case in URLs');
assert(!policy.shouldCapitalise(purpose.NORMAL, sensitiveHints, '', 0), 'no automatic case in sensitive text');
assert(!policy.shouldCapitalise(purpose.NORMAL, hint.LOWERCASE, '', 0), 'lowercase hint wins');
assert(!policy.shouldCapitalise(purpose.NORMAL, 0, 'hello ', 6), 'no capital mid sentence');
assert(!policy.shouldCapitalise(purpose.NORMAL, 0, 'é', 1), 'cursor counts characters');
assert(policy.shouldCapitalise(purpose.NORMAL, 0, 'one\n', 4), 'capital on a new line');
assert(policy.shouldCapitalise(purpose.NAME, hint.TITLECASE, 'ada ', 4), 'title case after a space');
assert(!policy.shouldCapitalise(purpose.TERMINAL, 0, '', 0), 'terminals keep their case');
assert(!policy.shouldCapitalise(purpose.NORMAL, 0, 'ab', 7), 'cursor beyond the text is rejected');
assert(Keycap.canEndSentence('café '), 'letters end sentences');
assert(!Keycap.canEndSentence('hello. '), 'no second period');
assert(!Keycap.canEndSentence('  '), 'two spaces alone stay spaces');
assert(policy.isNumeric(purpose.PHONE) && !policy.isNumeric(purpose.NORMAL), 'numeric purposes');

const taps = new Keycap.TapTracker(350);
assert(!taps.tap(1000) && taps.tap(1300), 'second shift tap inside 350 ms locks capitals');
assert(!taps.tap(1400), 'a lock consumes the pair');
assert(!taps.tap(2000) && !taps.tap(2400), 'slow taps stay single');

assert(Keycap.deleteMode(1999, false) === 'char' && Keycap.deleteMode(2000, false) === 'word',
    'word deletion after two seconds');
assert(Keycap.deleteMode(5000, true) === 'char', 'sensitive fields only delete characters');
assert(Keycap.deleteSelectWords(-19) === 0 && Keycap.deleteSelectWords(-30) === 1 &&
    Keycap.deleteSelectWords(-95) === 3 && Keycap.deleteSelectWords(-5000) === 32, 'delete drag words');
assert(Keycap.spaceCursorSteps(8, 0, 0) === 0 && Keycap.spaceCursorSteps(9, 13, 0) === 0,
    'space needs a clear sideways drag');
assert(Keycap.spaceCursorSteps(25, 3, 0) === 2 && Keycap.spaceCursorSteps(25, 3, 2) === 0 &&
    Keycap.spaceCursorSteps(-31, 20, 1) === -4, 'space moves one character per step');

let slots = Keycap.suggestionSlots('teh', ['the', 'teh', 'ten']);
assert(slots.map(s => s.kind).join() === 'literal,suggested,alternative', 'literal, choice, alternative');
assert(slots[0].label === '“teh”' && slots[1].text === 'the' && slots[2].text === 'ten',
    'literal quoted, backend choice centred');
slots = Keycap.suggestionSlots('the', ['the', 'they']);
assert(slots.length === 2 && slots[1].text === 'they', 'a choice equal to the literal is not repeated');
slots = Keycap.suggestionSlots(null, ['a', 'b', 'c', 'd']);
assert(slots.length === 3 && slots[0].kind === 'suggested', 'without a literal the first candidate leads');

const g = Keycap.keycap.geometry;
const gutters = g.gutter_top + g.gutter_bottom;
// The us layout's widest row spans 11.5 key widths.
const capFor = width => Math.min(g.portrait_cap, Math.round((width / 11.5 - g.gutter_x) * g.portrait_cap / g.cap_width));
assert(Keycap.keyboardHeight({width: 360, height: 808}, 4, 11.5) ===
    4 * Math.max(g.min_row, capFor(360) + gutters) + g.strip_height, 'phone caps keep the design proportion at their real width');
assert(Keycap.keyboardHeight({width: 360, height: 808}, 4, 11.5) <= 4 * (g.portrait_cap + gutters) + g.strip_height,
    'phone caps never exceed the design height');
assert(Keycap.keyboardHeight({width: 360, height: 808}, 4, 11.5, 28) ===
    Keycap.keyboardHeight({width: 360, height: 808}, 4, 11.5) + 28, 'the home gesture inset is added below the keys');
assert(Keycap.keyboardHeight({width: 404, height: 908}, 4, 11.5) < 908 / 3, 'tall phones are not sized by screen height');
assert(Keycap.keyboardHeight({width: 908, height: 404}, 4, 11.5) === 4 * g.landscape_row + g.strip_height,
    'phone landscape uses compact rows');
assert(Keycap.keyboardHeight({width: 360, height: 400}, 4, 11.5) === 200, 'never more than half the screen');
assert(Keycap.keyboardHeight({width: 1920, height: 1080}, 4, 11.5) === 360 + g.strip_height,
    'large screens keep proportional height');
assert(Keycap.keyboardHeight({width: 1038, height: 1076}, 4, 11.5) === 269 + g.strip_height,
    'tablet portrait keeps proportional height');
for (const monitor of [{width: 360, height: 800}, {width: 800, height: 360}, {width: 1280, height: 800}])
    assert((Keycap.keyboardHeight(monitor, 4, 11.5) - g.strip_height) / 4 >= g.min_row, 'touch-size rows');

// The keyboard is re-sized for each screen; a height set for a larger screen
// (the unfolded panel) must not floor the next one.
{
    const at = keyboard.indexOf('    _relayout() {');
    const body = keyboard.slice(keyboard.indexOf('{', at) + 1, keyboard.indexOf('\n    }', at));
    const relayout = new Function('Main', 'St', 'Keycap', 'global', body);
    const actor = {height: 0, width: 0, _currentPage: {getRatio: () => [11.5, 4]},
        set_style() {}, get_preferred_height: () => [actor.height, actor.height]};
    const run = (width, height) => relayout.call(actor, {layoutManager: {keyboardMonitor: {width, height}},
        presentation: {keyboardInset: () => 28}}, {ThemeContext: {get_for_stage: () => ({scale_factor: 1})}}, Keycap, {stage: {}});
    run(1038, 1076);
    const tablet = actor.height;
    run(360, 808);
    assert(actor.height < tablet && actor.height === Keycap.keyboardHeight({width: 360, height: 808}, 4, 11.5, 28),
        `keyboard shrinks back on the folded screen (${tablet} -> ${actor.height})`);
}

const history = new Keycap.ClipboardHistory({max_clips: 10, max_bytes: 65536, ttl_seconds: 3600});
for (let i = 0; i < 12; i++)
    history.add(`clip ${i}`, i);
assert(history.list(20).length === 10 && history.list(20)[0] === 'clip 11', 'ten newest clips');
history.pin('clip 2', true);
history.add('clip 12', 30);
assert(history.has('clip 2', 31) && !history.has('clip 3', 31), 'pins survive eviction');
assert(!history.add('x'.repeat(65537), 40), 'over 64 KiB is refused');
assert(history.list(3600 * 1000 + 31).join() === 'clip 2', 'unpinned clips expire after an hour');
const full = new Keycap.ClipboardHistory({max_clips: 2, max_bytes: 10, ttl_seconds: 1});
full.add('a', 0); full.add('b', 0); full.pin('a', true); full.pin('b', true);
assert(!full.add('c', 0) && full.list(0).join() === 'b,a', 'all slots pinned refuses new captures');

for (const [needle, message] of [
    ["import * as Keycap from './lumaPresentation/keycap.js';", 'keyboard uses the shared policy'],
    ['setOskCompletion(this._completionAllowed())', 'completion follows privacy and preference'],
    ['Keycap.SECRET_CLIPBOARD_TARGET', 'password-manager copies are skipped'],
    ['generation !== this._clipboardGeneration || this._isSensitive()', 'stale clipboard reads are dropped'],
    ['St.Settings.get().high_contrast ? null', 'High Contrast keeps the platform keyboard'],
    ['Keycap.keyboardHeight({width: monitor.width / scale', 'Keycap sizing in logical pixels'],
    ['this._queue = Promise.resolve();', 'keys reach the input method in order'],
    ['this._shiftTaps.tap(monotonicMs())', 'double-tap caps lock'],
    ['Keycap.deleteMode(monotonicMs() - this._deleteStarted', 'time-based word deletion'],
    ['!physicalKeyboard && this._seat.get_touch_mode()', 'automatic OSK policy (0291) is kept'],
])
    assert(keyboard.includes(needle), message);
assert(!keyboard.includes('BACKSPACE_WORD_DELETE_THRESHOLD'), 'count-based word deletion is gone');

// Captured events that reach no actor (a cancelled touch, say) must neither
// throw nor be consumed by the keyboard's popups (the alternatives popup and
// the preferences menu each watch captured events while open).
const Clutter = {EventType: {BUTTON_PRESS: 0, TOUCH_BEGIN: 1, BUTTON_RELEASE: 2, TOUCH_END: 3, TOUCH_CANCEL: 4},
    EVENT_PROPAGATE: false, EVENT_STOP: true};
const strict = {contains(actor) { if (!actor) throw new Error('Argument descendant may not be null'); return false; }};
const actorless = {stage: {get_event_actor: () => null}};
const handlers = [];
for (let at = keyboard.indexOf('    _onCapturedEvent('); at >= 0; at = keyboard.indexOf('    _onCapturedEvent(', at + 1)) {
    const body = keyboard.indexOf('{', at);
    handlers.push(keyboard.slice(body + 1, keyboard.indexOf('\n    }', body)));
}
assert(handlers.length === 2, 'both captured-event handlers found');
for (const body of handlers) {
    const handle = new Function('Clutter', 'global', 'actor', 'event', body);
    let hidden = false;
    const owner = {actor: strict, _boxPointer: {bin: strict}, _hideSubkeys() { hidden = true; }, close() {}};
    for (const type of Object.values(Clutter.EventType)) {
        const result = handle.call(owner, Clutter, actorless, null, {type: () => type});
        assert(result === Clutter.EVENT_PROPAGATE, `actorless event ${type} propagates`);
    }
    if (body.includes('_hideSubkeys'))
        assert(hidden, 'a cancelled touch closes the alternatives');
}
console.log('PASS Keycap privacy, actorless captured events, re-sizing, case, double space, shift lock, delete/space gestures, suggestions, sizing, clipboard history');
