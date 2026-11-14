// SPDX-License-Identifier: GPL-2.0-or-later
// Keycap policy and its wiring into the Shell keyboard.
// Run: gjs -m keycap-policy.js js/ui/lumaPresentation/keycap.js js/ui/keyboard.js [js/gdm/loginDialog.js]
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
// safeAreas.js sits next to keycap.js (js/ui/lumaPresentation/).
const SafeAreas = await import(`file://${absolute(args[0]).replace(/[^/]*$/, 'safeAreas.js')}`);
const keyboard = read(args[1]);
const loginDialog = args[2] ? read(args[2]) : null;

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
assert(Keycap.bottomInset({width: 360, height: 808}, true, 28) === g.thumb_gap && g.thumb_gap > 28,
    'an upright phone keeps the thumb gap, which holds the home gesture');
assert(Keycap.bottomInset({width: 360, height: 808}, true, 0) === g.thumb_gap, 'the lock screen keeps the thumb gap');
assert(Keycap.bottomInset({width: 808, height: 360}, true, 28) === 28, 'a phone on its side keeps only the home gesture');
assert(Keycap.bottomInset({width: 1038, height: 1076}, false, 28) === 28, 'a tablet keeps only the home gesture');
assert(Keycap.standaloneInset({width: 372, height: 828}, true) === g.thumb_gap,
    'a handheld greeter keeps the thumb gap on a phone-width screen');
assert(Keycap.standaloneInset({width: 828, height: 372}, true) === 0 &&
    Keycap.standaloneInset({width: 1038, height: 1076}, true) === 0 &&
    Keycap.standaloneInset({width: 372, height: 828}, false) === 0,
    'no standalone inset on a phone on its side, a wide handheld screen or a desktop');
// A panel's rounded bottom corners (its display profile) keep the outer keys
// clear wherever the thumb gap does not already.
{
    // config/mobile/display-geometry/google-comet.json, outer panel (bottom corners only).
    const profile = SafeAreas.selectDisplayProfile(SafeAreas.readDisplayProfiles(JSON.stringify({
        schema: 1, connector: 'DSI-1', nativeWidth: 1080, nativeHeight: 2424,
        cutouts: [{x: 0, y: 2376, width: 48, height: 48}, {x: 1032, y: 2376, width: 48, height: 48}],
    })), 'DSI-1', true);
    const phone = {width: 360, height: 808};
    const corner = SafeAreas.bottomCornerClearance(profile, phone);
    assert(corner === 16, `comet's 48 px corner squares are 16 logical px at 300% (${corner})`);
    assert(SafeAreas.bottomCornerClearance(null, phone) === 0, 'no profile, no corner clearance');
    assert(Keycap.bottomInset(phone, true, 0, corner) === g.thumb_gap &&
        Keycap.bottomInset(phone, false, 0, corner) === 16 &&
        Keycap.bottomInset(phone, false, 28, corner) === 28 &&
        Keycap.bottomInset({width: 808, height: 360}, true, 0, corner) === 16,
        'corners, gesture zone and thumb gap: the largest wins');
    assert(Keycap.controlRow(Keycap.bottomInset(phone, true, 0, corner)) === g.min_row,
        'the corners leave the thumb gap and its control row as they are');
    assert(Keycap.standaloneInset(phone, true, corner) === g.thumb_gap &&
        Keycap.standaloneInset(phone, false, corner) === 16 &&
        Keycap.standaloneInset(phone, false) === 0,
        'the greeter keeps a profiled panel\'s corners where it keeps no thumb gap');
}
assert(Keycap.keyboardHeight({width: 432, height: 970}, 4, 11.5, Keycap.bottomInset({width: 432, height: 970}, true, 28)) <= 970 / 2,
    'the thumb gap fits within half the screen');
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
    const relayout = new Function('Main', 'St', 'Keycap', 'global', 'GLib',
        'panelProfile', 'bottomCornerClearance', body);
    let style = null, styleWrites = 0, sizeWrites = 0, toolSyncs = 0;
    const sized = {};
    const actor = {_currentPage: {getRatio: () => [11.5, 4]},
        _controls: {visible: false}, _syncTools() { toolSyncs++; },
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
    assert(!actor._controls.visible && style === 'padding-bottom: 28px;',
        'a keyboard without the thumb gap keeps its controls in the strip');
    // A phone held upright: settings and hide sit in the thumb gap's top
    // row, the home gesture zone below it stays clear.
    const phone = {width: 372, height: 828};
    const phoneInset = Keycap.bottomInset(phone, true, 28);
    const upright = () => relayout.call(actor, {layoutManager: {keyboardMonitor: phone},
        presentation: {keyboardInset: () => phoneInset}}, {ThemeContext: {get_for_stage: () => ({scale_factor: 1})}},
    Keycap, {stage: {}});
    const syncs = toolSyncs;
    upright();
    assert(actor._controls.visible && toolSyncs === syncs + 1 &&
        style === `padding-bottom: ${phoneInset - g.min_row}px;` &&
        actor.height === Keycap.keyboardHeight(phone, 4, 11.5, phoneInset),
    `the thumb gap holds the control row above the home gesture (${style}, ${actor.height})`);
    const writes = [styleWrites, sizeWrites, toolSyncs];
    upright();
    assert(styleWrites === writes[0] && sizeWrites === writes[1] && toolSyncs === writes[2],
        'an unchanged phone relayout writes nothing');
    run(1038, 1076);
    assert(!actor._controls.visible && toolSyncs === writes[2] + 1, 'a tablet returns the controls to the strip');
    // The greeter has no presentation: a handheld keeps the thumb gap below
    // the keys there, a desktop greeter keeps nothing.
    const greeter = (deviceClass, profile = null) => relayout.call(actor,
        {layoutManager: {keyboardMonitor: {width: 372, height: 828}}, presentation: null},
        {ThemeContext: {get_for_stage: () => ({scale_factor: 1})}}, Keycap, {stage: {}},
        {getenv: name => (name === 'LUMA_DEVICE_CLASS' ? deviceClass : null)},
        () => profile, SafeAreas.bottomCornerClearance);
    greeter('handheld');
    assert(style === `padding-bottom: ${g.thumb_gap - g.min_row}px;` && actor._controls.visible &&
        actor.height === Keycap.keyboardHeight({width: 372, height: 828}, 4, 11.5, g.thumb_gap),
        `the handheld greeter keyboard keeps the thumb gap (${style}, ${actor.height})`);
    greeter(null);
    assert(style === null && !actor._controls.visible && actor.height === Keycap.keyboardHeight({width: 372, height: 828}, 4, 11.5),
        'a greeter that is not a handheld keeps no inset');
    // A profiled panel: a handheld keeps its thumb gap and control row, which
    // already clear the corners; a greeter without one sits just above them.
    const panel = {nativeWidth: 372, nativeHeight: 828,
        cutouts: [{x: 0, y: 808, width: 20, height: 20}, {x: 352, y: 808, width: 20, height: 20}]};
    greeter('handheld', panel);
    assert(style === `padding-bottom: ${g.thumb_gap - g.min_row}px;` && actor._controls.visible,
        `a profiled handheld greeter keeps the thumb gap and its controls (${style})`);
    greeter(null, panel);
    assert(style === 'padding-bottom: 20px;' && !actor._controls.visible &&
        actor.height === Keycap.keyboardHeight({width: 372, height: 828}, 4, 11.5, 20),
        `the greeter keyboard clears the panel's corners (${style}, ${actor.height})`);
}

// The thumb gap's control row: one touch row, only where the thumb gap is,
// leaving at least the home gesture zone (28 px) below it.
assert(Keycap.controlRow(g.thumb_gap) === g.min_row && Keycap.controlRow(g.thumb_gap + 8) === g.min_row,
    'a phone thumb gap holds one touch row of controls');
assert(Keycap.controlRow(28) === 0 && Keycap.controlRow(0) === 0, 'other insets hold no controls');
assert(g.thumb_gap - g.min_row >= 28, 'the control row leaves the home gesture zone clear');

// Settings and hide leave the strip while the control row shows them, so
// hide stays reachable while suggestions fill the strip; number pads keep
// hide (in the strip or the row) and gain Enter.
{
    const at = keyboard.indexOf('    _syncTools() {');
    const body = keyboard.slice(keyboard.indexOf('{', at) + 1, keyboard.indexOf('\n    }', at));
    const syncTools = new Function(body);
    const tools = (controls, numeric, suggesting) => {
        const actor = {_tools: {visible: true}, _suggestions: {visible: false, count: suggesting ? 3 : 0},
            _suggestionsShown: true, _controls: {visible: controls}, _stripEnter: {visible: false},
            _toolButtons: [0, 1, 2, 3, 4].map(() => ({visible: true})),
            _keycapPolicy: {isNumeric: () => numeric}, _keyboardController: {purpose: 0}};
        syncTools.call(actor);
        return {strip: actor._toolButtons.map(b => (b.visible ? 1 : 0)).join(''),
            enter: actor._stripEnter.visible, tools: actor._tools.visible, suggestions: actor._suggestions.visible};
    };
    assert(tools(false, false, false).strip === '11111', 'without the row the strip has every tool');
    assert(tools(true, false, false).strip === '11100', 'with the row settings and hide leave the strip');
    assert(tools(true, true, false).strip === '00000' && tools(true, true, false).enter, 'a number pad strip keeps Enter');
    assert(tools(false, true, false).strip === '00001', 'a number pad without the row keeps hide in the strip');
    const typing = tools(true, false, true);
    assert(typing.suggestions && !typing.tools, 'suggestions take the strip while a word has them');
    assert(keyboard.includes("add('chevron-down', _('Hide keyboard'), () => this.close(true))") &&
        keyboard.includes('this.add_child(this._controls);'), 'the control row carries hide');
}

// A key never sets the row height: its label's font (a fallback font for one
// glyph) must not raise the rows above the height the Keycap rule gives.
{
    const at = keyboard.indexOf('class Key extends St.BoxLayout {');
    const key = keyboard.slice(at, keyboard.indexOf('\n});', at));
    const from = key.indexOf('    vfunc_get_preferred_height(forWidth) {');
    assert(at > 0 && from > 0, 'Key overrides its preferred height');
    const body = key.slice(key.indexOf('{', from) + 1, key.indexOf('\n    }', from));
    const preferred = new Function('forWidth', body.replace('super.vfunc_get_preferred_height(forWidth)', '[52, 52]'));
    const [min, nat] = preferred(-1);
    assert(min === 0 && nat === 52, `a key reports no minimum height (${min}, ${nat})`);
}

// The login dialog lays its content out above an open keyboard, as the
// unlock dialog does, and lays out again when the keyboard shows or hides.
if (loginDialog) {
    const at = loginDialog.indexOf('    vfunc_allocate(dialogBox) {');
    const allocate = loginDialog.slice(at, loginDialog.indexOf('\n    }', at));
    assert(at > 0 && allocate.includes('Main.layoutManager.keyboardBox.height') &&
        allocate.includes('const height = Math.max(fullHeight / 2, fullHeight - keyboard);'),
        'the login dialog allocates in the height the keyboard leaves');
    assert(/Main\.keyboard\.connectObject\('visibility-changed', \(\) => this\.queue_relayout\(\), this\)/.test(loginDialog),
        'the login dialog follows the keyboard');
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
    ['levelLayout.opacity = 0;', 'every layer stays laid out and cached; a switch swaps opacity'],
    ['this._gridRatio = [columns, rows];', 'a switch sets the grid ratio only when it changes'],
    ['            else\n                this._coolLayers();', 'every show draws the layers into their caches again'],
    ['warm.drawn = !this._inputSincePaint', 'no layer is warmed in a frame that handles a touch'],
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
    ['            levelLayout.hide();', 'a layer switch shows or hides no layer'],
    ['this._currentPage.hide();', 'a layer switch shows or hides no layer'],
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
// Phone key arrangement (Gboard / AOSP LatinIME) from the shipped us layout.
{
    const layouts = absolute(args[1]).replace(/js\/ui\/keyboard\.js$/, 'data/osk-layouts/');
    const us = JSON.parse(read(`${layouts}us.json`)).levels;
    const name = key => key.label ?? (key.strings?.[0] === ' ' ? 'space' : key.strings?.[0]) ??
        (key.keyval === '0xff0d' ? 'enter' : key.iconName === 'osk-shift-symbolic' ? 'shift' : key.action);
    const plan = levels => levels.map(level => level.rows.map(row => row.map(key =>
        `${key.leftOffset ? `+${key.leftOffset} ` : ''}${name(key)}${(key.width ?? 1) !== 1 ? `:${key.width}` : ''}`).join(' ')));
    const [letters, shifted, symbols, more] = plan(Keycap.phoneLevels(us));
    assert(JSON.stringify(letters) === JSON.stringify([
        'q w e r t y u i o p',
        '+0.5 a s d f g h j k l',
        'shift:1.5 z x c v b n m delete:1.5',
        '?123:1.5 , emoji space:4 . enter:1.5',
    ]), `letters: delete after m, Enter bottom right, ?123 , emoji space . Enter (${JSON.stringify(letters)})`);
    assert(shifted[2] === 'shift:1.5 Z X C V B N M delete:1.5', 'capitals keep the arrangement');
    assert(JSON.stringify(symbols) === JSON.stringify([
        '1 2 3 4 5 6 7 8 9 0',
        '+0.5 @ # $ % & - + ( )',
        '=/<:1.5 * " \' : ; ! ? delete:1.5',
        'ABC:1.5 , _ space:3 / . enter:1.5',
    ]), `symbols keep the skeleton, _ and / beside space (${JSON.stringify(symbols)})`);
    assert(more[2].endsWith('delete:1.5') && more[3] === 'ABC:1.5 , < space:3 > . enter:1.5', 'second symbol page too');
    const twoLanguages = plan(Keycap.phoneLevels(us, {languages: 2}));
    assert(twoLanguages[0][3] === '?123:1.5 , languageMenu space:4 . enter:1.5',
        'several input sources: the language key takes the emoji slot (emoji stays in the strip)');
    const noEmoji = plan(Keycap.phoneLevels(us, {emoji: false}));
    assert(noEmoji[0][3] === '?123:1.5 , space:5 . enter:1.5', 'fields without emoji give its width to space');
    const period = Keycap.phoneLevels(us)[0].rows[3].find(key => key.strings?.[0] === '.');
    assert(period.strings.includes('!') && period.strings.includes('?'), 'period keeps its long-press punctuation');
    for (const file of ['email', 'url', 'de', 'fr', 'ru', 'th', 'il', 'ara']) {
        for (const level of Keycap.phoneLevels(JSON.parse(read(`${layouts}${file}.json`)).levels)) {
            const widths = level.rows.map(row => row.reduce((sum, key) => sum + (key.leftOffset ?? 0) + (key.width ?? 1), 0));
            const all = level.rows.flat().flatMap(key => [key.width ?? 1, key.leftOffset ?? 0]);
            assert(all.every(value => value >= 0 && Number.isInteger(value * 2)), `${file}: half-key grid`);
            assert(widths.every(width => width <= Math.max(...widths)) && widths.at(-1) === Math.max(...widths),
                `${file}/${level.level}: the bottom row spans the grid`);
            const last = level.rows.at(-2), bottom = level.rows.at(-1);
            assert(last.at(-1).action === 'delete', `${file}/${level.level}: delete ends the last letter row`);
            assert(bottom.at(-1).keyval === '0xff0d', `${file}/${level.level}: Enter ends the bottom row`);
            assert(!level.rows.flat().some(key => key.action === 'hide'), `${file}: hide lives in the strip`);
        }
    }
    const url = Keycap.phoneLevels(JSON.parse(read(`${layouts}url.json`)).levels)[0];
    assert(url.rows[3].find(key => key.strings?.[0] === ' ').width >= 2.5 &&
        url.rows[3].reduce((sum, key) => sum + (key.width ?? 1), 0) === 10, 'URL keys fit the letters\' ten columns');
    for (const file of ['digits', 'number', 'phone', 'us-extended']) {
        const levels = JSON.parse(read(`${layouts}${file}.json`)).levels;
        assert(Keycap.phoneLevels(levels) === levels, `${file} keeps its own arrangement`);
    }
    const build = keyboard.slice(keyboard.indexOf('    _updateLayout('), keyboard.indexOf('    _keycapRow('));
    assert(/pad \|\| this\._tv \|\| !phone\s*\?\s*keyboardModel\.getLevels\(\)\s*:\s*Keycap\.phoneLevels\(/.test(build),
        'the keyboard uses the phone arrangement except on number pads, TV, Tablet and Desktop');
    assert(/const phone = Main\.presentation\s*\?\s*Main\.presentation\.mode === 'mobile'\s*:\s*GLib\.getenv\('LUMA_DEVICE_CLASS'\) === 'handheld';/.test(build),
        'the phone arrangement follows Mobile, and a handheld greeter');
}
// A keyboard destroyed while shown (a mouse or keyboard plugged in) stops
// following the keyboard box's height; a leaked handler wrote to the disposed
// actor on every later monitor change.
{
    const at = keyboard.indexOf('        this._destroyed = true;');
    const body = keyboard.slice(at, keyboard.indexOf('\n    }', at));
    assert(at > 0 && body.includes('keyboardBox.disconnect(this._keyboardHeightNotifyId)'),
        'a destroyed keyboard drops its keyboard-box height handler');
}
// A text field focused by a tap on its own window opens the keyboard: the
// client's own request to show it came before the focus and went nowhere.
// Only touch events arm it, so pointer and keyboard use stay as they were.
{
    const touch = keyboard.slice(keyboard.indexOf('    _onStageTouch(stage, event) {'),
        keyboard.indexOf('\n    }\n', keyboard.indexOf('    _onStageTouch(stage, event) {')));
    const focus = keyboard.slice(keyboard.indexOf('    _tapFieldFocus() {'),
        keyboard.indexOf('\n    }\n', keyboard.indexOf('    _tapFieldFocus() {')));
    assert(/'captured-event', this\._onStageTouch\.bind\(this\)/.test(keyboard),
        'the keyboard watches the stage for touches');
    assert(touch.includes('Clutter.EventType.TOUCH_BEGIN') && !/BUTTON_PRESS|KEY_PRESS/.test(touch) &&
        touch.includes('window === global.display.focus_window'),
        'only a touch on the focused window arms the tap');
    assert(touch.includes('return Clutter.EVENT_PROPAGATE;') && !touch.includes('EVENT_STOP'),
        'watching touches never takes them from the window');
    assert(/this\._tvFieldFocus\(\);\s*this\._tapFieldFocus\(\);/.test(keyboard),
        'a text input focus-in checks for the tap');
    assert(focus.includes('monotonicMs() - tap.upMs > FIELD_TAP_TIME') && focus.includes('this._fieldTap = null;') &&
        focus.includes('this.open();') && focus.includes('this._tv'),
        'a recent tap opens the keyboard once, outside TV posture');
    assert(focus.includes('getSurroundingText()') && focus.includes('cursor !== anchor'),
        'a field focused with its text selected (by the application) stays without the keyboard');
    assert(/if \(this\._tapFocusId\)\s*GLib\.source_remove\(this\._tapFocusId\);/.test(keyboard),
        'a destroyed keyboard drops a pending tap focus');
}
console.log('PASS Keycap privacy, tap-focused fields, phone key arrangement, actorless captured events, re-sizing, case, double space, shift lock, delete/space gestures, suggestions, sizing, clipboard history, key timing, hit testing, touch correction, text mirror');
