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

// Key timing from event times: a hold is judged by the press and release
// timestamps, repeats are owed by elapsed time and never bunched.
const t = Keycap.keycap.timing;
assert(!Keycap.isLongPress(t.long_press - 1) && Keycap.isLongPress(t.long_press), 'long press from event times');
assert(Keycap.repeatsDue(t.delete_repeat_delay - 1) === 0 && Keycap.repeatsDue(t.delete_repeat_delay) === 1 &&
    Keycap.repeatsDue(t.delete_repeat_delay + 3 * t.delete_repeat_interval) === 4, 'repeats owed by hold time');
assert(Keycap.nextRepeatAt(0) === t.delete_repeat_delay &&
    Keycap.nextRepeatAt(2) === t.delete_repeat_delay + 2 * t.delete_repeat_interval, 'next repeat time');

// Hit testing: whole cells, nearest key for gaps and margins.
const cell = (x1, y1, letter) => ({x1, y1, x2: x1 + 36, y2: y1 + 50, letter});
const row = [cell(0, 0, 'q'), cell(36, 0, 'w'), cell(72, 0, 'e')];
assert(Keycap.nearestCell(row, 50, 25) === row[1], 'a touch inside a cell is that key');
assert(Keycap.nearestCell(row, 36, 25) === row[1] || Keycap.nearestCell(row, 36, 25) === row[0],
    'a touch on a cell boundary is one of its keys');
assert(Keycap.nearestCell(row, -12, 25) === row[0] && Keycap.nearestCell(row, 80, 70) === row[2],
    'a touch in the margins is the nearest key');
assert(Keycap.cellDistance(row[0], 10, 10) === 0 && Keycap.cellDistance(row[0], 40, 0) === 4, 'cell distance');
// Dynamic targets: near the q|w boundary a much likelier w wins, but a
// clear tap on q never does, and nothing moves without a prior.
const likelyW = c => (c.letter === 'w' ? 100000 : 10);
assert(Keycap.chooseCell(row, 34, 25, likelyW) === row[1], 'the word moves a near-boundary touch');
assert(Keycap.chooseCell(row, 18, 25, likelyW) === row[0], 'a clear tap keeps its key');
assert(Keycap.chooseCell(row, 34, 25, null) === row[0], 'geometry alone without a prior');
assert(Keycap.chooseCell(row, 34, 25, () => 5) === row[0], 'an even prior keeps geometry');

// Touch-position correction: a per-row bias learned from committed taps,
// clamped, and undone when the tap is deleted.
const correction = new Keycap.TouchCorrection({rate: 0.5, limit: 0.2});
let token = null;
for (let i = 0; i < 10; i++)
    token = correction.learn(1, -0.4, 0.3);
const [bx, by] = correction.bias(1);
assert(Math.abs(bx + 0.2) < 1e-9 && Math.abs(by - 0.2) < 1e-9, `bias clamped (${bx}, ${by})`);
const [cx, cy] = correction.correct(1, 100, 100, 36, 50);
assert(cx === 100 + 0.2 * 36 && cy === 100 - 0.2 * 50, 'correction moves the touch toward the cap centre');
assert(correction.bias(0)[0] === 0, 'other rows are untouched');
assert(correction.learn(1, 0.9, 0) === null, 'taps outside the cell teach nothing');
correction.retract(token);
assert(correction.bias(1)[0] !== bx || correction.bias(1)[0] === -0.2, 'a deleted tap is retracted');

// The keyboard's own view of the text before the caret: its sends apply at
// once, echoes of them change nothing, outside changes replace it.
const mirror = new Keycap.TextMirror();
mirror.reset('Hello. ');
mirror.apply(0, 'H');
mirror.apply(0, 'o');
assert(mirror.before === 'Hello. Ho' && mirror.pending, 'sends apply at once');
assert(!mirror.report('Hello. H') && mirror.before === 'Hello. Ho', 'a late echo keeps the newer text');
assert(!mirror.report('Hello. Ho') && !mirror.pending, 'the final echo settles');
assert(mirror.report('Elsewhere') && mirror.before === 'Elsewhere', 'an outside change replaces it');
mirror.apply(2, 'X');
assert(mirror.before === 'ElsewheX', 'deletions count characters');
assert(!mirror.report('Elsewhe') && !mirror.report('ElsewheX') && !mirror.pending,
    'a replacement reported as deletion then insertion is its own echo');
assert(policy.shouldCapitalise(purpose.NORMAL, 0, 'Hi. ', [...'Hi. '].length), 'case decided from the mirror text');

// Only the tail before the caret is read from a surrounding text, whatever
// the document's length, and the caret counts characters.
assert(Keycap.textBeforeCursor('Hello world', 5) === 'Hello', 'text before the caret');
assert(Keycap.textBeforeCursor('a😀b😀c', 4) === 'a😀b😀', 'the caret counts characters, not code units');
assert(Keycap.textBeforeCursor('', 3) === '' && Keycap.textBeforeCursor('abc', 0) === '', 'empty cases');
{
    const long = `${'x'.repeat(500000)}End. `;
    const tail = Keycap.textBeforeCursor(long, long.length);
    assert(tail.endsWith('End. ') && tail.length <= 4 * 48, 'a long document yields a bounded tail');
    const emoji = `${'😀'.repeat(300)}Ok`;
    const tailEmoji = Keycap.textBeforeCursor(emoji, [...emoji].length);
    assert(tailEmoji.endsWith('😀Ok') && !/^[\uDC00-\uDFFF]/.test(tailEmoji), 'a tail never splits a surrogate pair');
    const big = new Keycap.TextMirror();
    big.reset(long);
    assert(big.before.length <= 4 * 48 && !big.report(long), 'the mirror keeps a bounded tail of long reports');
}
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
    let style = null, styleWrites = 0, sizeWrites = 0;
    const sized = {};
    const actor = {_currentPage: {getRatio: () => [11.5, 4]},
        get_style: () => style, set_style(value) { style = value; styleWrites++; },
        get height() { return sized.height ?? 0; }, set height(v) { sized.height = v; sizeWrites++; },
        get width() { return sized.width ?? 0; }, set width(v) { sized.width = v; sizeWrites++; },
        get_preferred_height: () => [actor.height, actor.height]};
    const run = (width, height) => relayout.call(actor, {layoutManager: {keyboardMonitor: {width, height}},
        presentation: {keyboardInset: () => 28}}, {ThemeContext: {get_for_stage: () => ({scale_factor: 1})}}, Keycap, {stage: {}});
    run(1038, 1076);
    const tablet = actor.height;
    run(360, 808);
    assert(actor.height < tablet && actor.height === Keycap.keyboardHeight({width: 360, height: 808}, 4, 11.5, 28),
        `keyboard shrinks back on the folded screen (${tablet} -> ${actor.height})`);
    // Laying out again with nothing changed writes nothing (a write would
    // re-render the open keyboard's paint state).
    const [styles, sizes] = [styleWrites, sizeWrites];
    run(360, 808);
    run(360, 808);
    assert(styleWrites === styles && sizeWrites === sizes, 'an unchanged relayout writes no style or size');
}

const words2 = new Keycap.WordSuggester(['the', 'then', 'they', 'there', 'tax'],
    new Map([['the', 1000], ['then', 500], ['there', 400], ['tax', 2]]));
assert(words2.prefixMass('th') > words2.prefixMass('ta') && words2.prefixMass('zz') === 0 &&
    words2.prefixMass('') === 0, 'prefix mass ranks likely continuations');

const words = new Keycap.WordSuggester(Keycap.hunspellWords('7\nhello/S\nthe\ntheir\nquick/M\nworld\nword\nwork\n'));
assert(words.size === 7 && words.has('Hello'), 'Hunspell words without affix flags');
assert(words.suggest('helo')[0] === 'hello' && words.suggest('teh')[0] === 'the', 'single-edit corrections lead');
assert(words.suggest('Thier')[0] === 'Their' && words.suggest('WOR').every(w => w === w.toUpperCase()), 'case follows the word');
assert(words.suggest('wor').join() === 'word,work,world', 'completions shortest first');
const typos = new Keycap.WordSuggester(['hello', 'halo', 'hero', 'help', 'work', 'word', 'the', 'thy']);
assert(typos.suggest('helo')[0] === 'hello' && typos.suggest('wprk')[0] === 'work' && typos.suggest('thw')[0] === 'the',
    'doubled letters and neighbouring keys rank first');
assert(words.suggest('hello').length === 0 && words.suggest('').length === 0 && words.suggest('12').length === 0,
    'known words and non-words get nothing');

// Frequency ranking and decisive autocorrection (python3-pyspellchecker
// style counts): a correction needs autocorrect_min weight and to lead the
// runner-up by autocorrect_lead, as Stevia's LatinIME completer requires.
const counts = new Map([['the', 76138318], ['thy', 2000], ['their', 3000000], ['there', 5000000],
    ['work', 900000], ['word', 300000], ['cat', 400000], ['car', 400000], ['receive', 51996]]);
const model = new Keycap.WordSuggester(['the', 'cat', 'car'], counts);
assert(model.analyse('teh').correction === 'the' && model.analyse('Teh').correction === 'The', 'teh becomes the');
assert(model.analyse('recieve').correction === 'receive', 'transposed letters are corrected');
assert(model.analyse('the').correction === null && model.analyse('work').correction === null, 'known words are never corrected');
assert(model.analyse('cax').correction === null, 'an ambiguous correction (cat or car) is left as typed');
assert(model.analyse('TEH').correction === null && model.analyse('t').correction === null,
    'capitals and single letters are left as typed');
assert(model.analyse('wor').suggestions[0] === 'work', 'completions rank by frequency');
model.keep('teh');
assert(model.analyse('teh').correction === null, 'a word kept by undoing a correction stays');
model.accept('thy');
assert(model.learned.kept.includes('teh') && model.learned.accepted.thy === 1, 'learned words round-trip');
const restored = new Keycap.WordSuggester([], counts);
restored.restore(model.learned);
assert(restored.analyse('teh').correction === null, 'kept words survive a restart');
let parsed = 0;
for (const batch of Keycap.frequencyEntries('{"a": 3, "b": 4, "c": 5}', 2))
    parsed += batch.length;
assert(parsed === 3, 'frequency lists parse in slices');
for (const [character, boundary] of [[' ', true], ['.', true], [',', true], ['a', false], ["'", false]])
    assert(Keycap.isWordBoundary(character) === boundary, `boundary ${JSON.stringify(character)}`);

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
    ['this._keyboardController.setOskCompletion(false);', 'taps never switch to a completion engine'],
    ['GLib.idle_add(GLib.PRIORITY_LOW, () => {', 'suggestions are computed off the key path'],
    ['analysis?.word !== this._word || !analysis.correction', 'autocorrect never waits for analysis'],
    ['this._prefs.auto_correct', 'autocorrect has an owner setting'],
    ['Keycap.SECRET_CLIPBOARD_TARGET', 'password-manager copies are skipped'],
    ['generation !== this._clipboardGeneration || this._isSensitive()', 'stale clipboard reads are dropped'],
    ['St.Settings.get().high_contrast ? null', 'High Contrast keeps the platform keyboard'],
    ['Keycap.keyboardHeight({width: monitor.width / scale', 'Keycap sizing in logical pixels'],
    ['this._queue = Promise.resolve();', 'engine keys reach the input method in order'],
    ['this._shiftTaps.tap(this._keyEvent.downMs', 'double-tap caps lock from press times'],
    ['Keycap.deleteMode(held, this._isSensitive())', 'time-based word deletion'],
    ['GLib.idle_add(GLib.PRIORITY_DEFAULT + 2', 'a second key in one turn follows the done, never at idle priority'],
    ['Clutter.Event.add_filter(global.stage', 'every tracked touch end is seen, grab or not'],
    ['Meta.LaterType.BEFORE_REDRAW, () => {\n                    this._tickLaterId = 0;', 'holds are decided at frame time'],
    ['this._finishPointer(pointer, timeUs, pointer.alternatives);', 'older keys type first on a new press'],
    ['global.display.set_input_panel_inset(monitor', 'applications resize above the keyboard'],
    ['reactive: false,\n            track_hover: false,', 'caps are drawn only; the tracker owns input'],
    ["this._preview.opacity = 0;", 'the preview hides without a relayout'],
    ['_cellsFor(page) {', 'key geometry is cached per layer allocation'],
    ['page.transform_stage_point(x, y)', 'a press maps the touch once instead of measuring every key'],
    ['Keycap.textBeforeCursor(text, cursor)', 'surrounding reports read only the tail'],
    ['this._letterPriors?.word === this._word', 'letter priors are computed off the key path'],
    ['_lockedWithoutEntry()', 'the locked screen opens the keyboard only for a visible entry'],
    ['levelLayout.offscreen_redirect = Clutter.OffscreenRedirect.ALWAYS', 'key layers are cached as one texture'],
    ['_showPressedCap(cell, cap)', 'the pressed cap is drawn over the cached grid'],
    ['offscreen_redirect: 0', 'previews render no offscreen text cache'],
    ['this._warmTimeoutId = GLib.timeout_add', 'the warm-up runs even when the show animation is stopped'],
    ['!physicalKeyboard && this._seat.get_touch_mode()', 'automatic OSK policy (0291) is kept'],
])
    assert(keyboard.includes(needle), message);
assert(!keyboard.includes('BACKSPACE_WORD_DELETE_THRESHOLD'), 'count-based word deletion is gone');
for (const [needle, message] of [
    ['GLib.PRIORITY_LOW, () => {\n                this._flushId', 'no idle-priority text flush'],
    ['toggleDelete(', 'no surrounding-text driven delete repeat'],
    ["'surrounding-text-set', () => {\n                    const [text, cursor, anchor]", 'no repeat bound to text reports'],
    ['KEY_LONG_PRESS_TIME,', 'no timer-owned long press'],
    ["global.stage.connectObject(\n            'captured-event', this._onCapturedEvent.bind(this), this);\n        this.keyButton", 'alternatives take no stage grab'],
    ['setKeycapVariantClass(this._preview);\n        this._preview.text', 'no per-press restyle of the preview'],
    ["this.keyButton.add_style_pseudo_class('active');", 'a press restyles no cap'],
    ['    _capDown(button) {', 'a press moves nothing inside the cached grid'],
    ["[...text].slice(0, cursor).join('')", 'no whole-document copy per surrounding report'],
    ['this._suggester.prefixMass(word + cell.letter)', 'no word-model lookups on a press'],
])
    assert(!keyboard.includes(needle), message);

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
assert(handlers.length === 1, 'only the menu popup watches captured events');
for (const body of handlers) {
    const handle = new Function('Clutter', 'global', 'actor', 'event', body);
    let hidden = false;
    const owner = {actor: strict, sourceActor: strict, _boxPointer: {bin: strict}, _hideSubkeys() { hidden = true; }, close() {}};
    for (const type of Object.values(Clutter.EventType)) {
        const result = handle.call(owner, Clutter, actorless, null, {type: () => type});
        assert(result === Clutter.EVENT_PROPAGATE, `actorless event ${type} propagates`);
    }
    if (body.includes('_hideSubkeys'))
        assert(hidden, 'a cancelled touch closes the alternatives');
}
// A press outside an open menu closes it and still reaches its target (a
// key), so no tap is lost to a menu.
{
    const body = handlers[0];
    const handle = new Function('Clutter', 'global', 'actor', 'event', body);
    const elsewhere = {contains: () => false};
    let closed = false;
    const owner = {actor: elsewhere, sourceActor: elsewhere, close() { closed = true; }};
    const target = {};
    const stage = {stage: {get_event_actor: () => target}};
    const result = handle.call(owner, Clutter, stage, null, {type: () => Clutter.EventType.TOUCH_BEGIN});
    assert(closed && result === Clutter.EVENT_PROPAGATE, 'a press outside the menu closes it and propagates');
}
console.log('PASS Keycap privacy, actorless captured events, re-sizing, case, double space, shift lock, delete/space gestures, suggestions, sizing, clipboard history, key timing, hit testing, touch correction, text mirror');
