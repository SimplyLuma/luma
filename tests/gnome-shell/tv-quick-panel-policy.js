// SPDX-License-Identifier: GPL-2.0-or-later
// The TV quick panel: its rows, the remote in it, how it reads the shell's
// own Quick Options and notifications, and the wiring that opens it from the
// bar's cluster and a long press of Home.
// Run: gjs -m tv-quick-panel-policy.js js/ui/lumaPresentation/tvQuickPanelModel.js \
//        js/ui/lumaPresentation/tvTokens.js js/ui/lumaPresentation/tvQuickPanel.js \
//        js/ui/lumaPresentation/tv.js js/ui/lumaPresentation/tvInput.js
// (node tv-quick-panel-policy.js ... works too).
const gjs = typeof globalThis.ARGV !== 'undefined';
const args = gjs ? globalThis.ARGV : process.argv.slice(2);
const GLib = gjs ? (await import('gi://GLib')).default : null;
const fs = gjs ? null : await import('node:fs');
const cwd = gjs ? GLib.get_current_dir() : process.cwd();
const absolute = path => (path.startsWith('/') ? path : `${cwd}/${path}`);
const read = path => (gjs ? new TextDecoder().decode(GLib.file_get_contents(path)[1])
    : fs.readFileSync(path, 'utf8'));
const print = gjs ? globalThis.print : console.log;

function assert(value, message) {
    if (!value)
        throw new Error(`FAIL: ${message}`);
}

const M = await import(`file://${absolute(args[0])}`);
const {tv: tokens} = await import(`file://${absolute(args[1])}`);
const panel = read(args[2]);
const home = read(args[3]);
const input = read(args[4]);

// Settings' island, mirrored: the same material numbers.
const q = tokens.quick_panel;
assert(q.radius === 36 && q.row_height === 68 && q.row_size === 24 && q.row_weight === 600 && q.icon === 30,
    "the island's metrics");
assert(q.home_hold_ms > 0 && q.level_step > 0 && q.level_step <= 0.1, 'a hold and a volume step');

// Rows: only what has a backend.
const kinds = rows => rows.map(row => row.kind).join(' ');
const full = {
    me: {name: 'Nick', focusable: true},
    toggles: [{key: 'wifi', visible: true}, {key: 'bluetooth', visible: false}, {key: 'dnd', visible: true}],
    level: {visible: true, outputs: [{id: 1, name: 'TV'}, {id: 2, name: 'Buds'}], current: 1},
    notes: [{id: 7, clearable: true}, {id: 6, clearable: false}],
    settings: true, sleep: true, power: {restart: true, powerOff: true}, open: {},
};
assert(kinds(M.panelRows(full)) ===
    'me sep chips sep level sep label note note clear sep settings sleep power', 'the whole panel, top to bottom');
assert(M.panelRows(full).find(row => row.kind === 'chips').chips.map(chip => chip.key).join() === 'wifi,dnd',
    'a toggle without its backend is not drawn');
const bare = {me: {name: 'Nick'}, toggles: [], level: {visible: false}, notes: [], settings: false, sleep: true,
    power: {restart: false, powerOff: false}, open: {}};
assert(kinds(M.panelRows(bare)) === 'me sep sleep', 'nothing to show, nothing drawn; no stray separators');
assert(!M.panelRows({...full, notes: [{id: 1, clearable: false}]}).some(row => row.kind === 'clear'),
    'Clear all only when something can be cleared');
assert(kinds(M.panelRows({...full, power: {restart: true, powerOff: false}, open: {power: true}})).endsWith('power restart'),
    'Power drops only what logind allows');
assert(kinds(M.panelRows({...full, open: {power: true}})).endsWith('power restart power-off'), 'Power drops its two');
const outputs = M.panelRows({...full, open: {outputs: true}});
assert(kinds(outputs).includes('level output output sep'), 'the outputs drop under the volume row');
assert(outputs.find(row => row.kind === 'output' && row.current).output.name === 'TV', 'the one in use is marked');
assert(!M.panelRows({...full, level: {...full.level, outputs: [{id: 1}]}, open: {outputs: true}})
    .some(row => row.kind === 'output'), 'one output has no list');

// The remote: Up/Down over the rows it can stop on; never onto a separator.
const rows = M.panelRows(full);
assert(M.firstStop(rows) === 0, 'it opens on you');
assert(M.firstStop(M.panelRows({...full, me: {name: 'Nick', focusable: false}})) === 2,
    'or on the toggles when there is nothing to open for you');
assert(rows[M.stepRow(rows, 0, 'down')].kind === 'chips', 'down skips the separator');
assert(M.stepRow(rows, 0, 'up') === 0, 'the top stays');
assert(M.stepRow(rows, rows.length - 1, 'down') === rows.length - 1, 'the bottom stays');
const label = rows.findIndex(row => row.kind === 'label');
assert(rows[M.stepRow(rows, label - 2, 'down')].kind === 'note', 'the label is not a stop');
assert(M.keepRow(rows, 'settings', 0) === rows.findIndex(row => row.key === 'settings'), 'a rebuild keeps the row');
const fewer = M.panelRows({...full, notes: []});
assert(M.focusable(fewer[M.keepRow(fewer, 'note:7', label + 1)]), 'a cleared row hands the focus on');

// Chips: Left/Right, skipping one the hardware switched off; Left past the
// first closes the panel.
const chips = [{reactive: true}, {reactive: false}, {reactive: true}];
assert(M.stepChip(chips, 0, 'right') === 2, 'a dead chip is skipped');
assert(M.stepChip(chips, 2, 'right') === 2, 'the last stays');
assert(M.stepChip(chips, 0, 'left') === -1, 'left past the first closes');
assert(M.firstChip([{reactive: false}, {reactive: true}]) === 1, 'the first live chip');
assert(!M.focusable({kind: 'chips', chips: [{reactive: false}]}), 'a row of dead chips is not a stop');
assert(M.toggleMethod({toggleMode: true}) === 'flip' && M.toggleMethod({toggleMode: false}) === 'click',
    'toggle-mode flips its binding; the rest act on clicked');
assert(M.chipState({checked: true, subtitle: 'Studio North', title: 'Wi-Fi'}) === 'Studio North', 'what it is on');
assert(M.chipState({checked: true, subtitle: 'Wi-Fi', title: 'Wi-Fi'}) === 'On', 'no echo of the title');
assert(M.chipState({checked: true, subtitle: 'Top center', stateOnly: true}) === 'On',
    "Do Not Disturb's subtitle (the corner) is not its state");
assert(M.chipState({checked: false, subtitle: 'x'}) === 'Off', 'off');

// Volume.
assert(M.stepLevel(0.4, 'right', 0.05) === 0.45 && M.stepLevel(0.4, 'left', 0.05) === 0.35, 'a step');
assert(M.stepLevel(0.02, 'left', 0.05) === 0 && M.stepLevel(0.98, 'right', 0.05) === 1, 'clamped');
assert(M.stepLevel(1.48, 'right', 0.05, 1.5) === 1.5, 'amplified range when the setting allows it');
assert(M.levelPercent(0.555) === 56, 'shown as a percentage');

// Notifications: the tray's own reading, newest first.
const closed = new Set(['acted', 'dismissed']);
const sources = [
    {policy: {enable: true}, notifications: [{n: 'a', time: 10, lifecycleState: 'shown'},
        {n: 'b', time: 30, lifecycleState: 'dismissed'}]},
    {policy: {enable: false}, notifications: [{n: 'c', time: 50}]},
    {policy: {enable: true}, notifications: [{n: 'd', time: 20, sortSequence: 1}, {n: 'e', time: 20, sortSequence: 2}]},
];
assert(M.noteRecords(sources, closed).map(note => note.n).join('') === 'eda', 'newest first; closed and muted out');
assert(M.noteRecords(sources, closed, 2).length === 2, 'capped');
assert(M.noteAge(10).unit === 'now' && M.noteAge(240).value === 4 && M.noteAge(7300).unit === 'h' &&
    M.noteAge(200000).unit === 'd', 'how long ago');

// One action per OK press (Pi, 1 Nov: one press of a held OK switched Wi-Fi off, then 24 more
// presses without the repeat flag switched it on 24 times).
{
    const quiet = 300;
    const gate = M.okGate();
    assert(M.okPress(gate, 0, quiet), 'the first press acts');
    let acts = 0;
    for (let t = 40; t < 1000; t += 40) if (M.okPress(gate, t, quiet)) acts++;
    assert(acts === 0, 'presses of a held OK, flagged as repeats or not, act no more');
    M.okRelease(gate);
    assert(M.okPress(gate, 1010, quiet), 'after the release the next press acts');
    assert(M.okPress(gate, 2000, quiet), 'a release that never came: a later press, after a quiet spell, acts');
    const opened = M.okGate(5000);
    assert(!M.okPress(opened, 5040, quiet), 'the press that opened the panel from the bar does not act in it');
    M.okRelease(opened);
    assert(M.okPress(opened, 5100, quiet), 'the next press does');
    assert(M.okPress(M.okGate(), 0, quiet), 'opened by a long press of Home: OK acts at once');
}

// Sleep, Home, scrolling, light.
assert(M.sleepAction(true) === 'suspend' && M.sleepAction(false) === 'screen-off', 'sleep');
assert(M.homePress(120, 500) === 'home' && M.homePress(500, 500) === 'quick-panel', 'a long press of Home');
assert(M.scrollTo(0, 68, 400, 1000, 80) === 0, 'in view: no scroll');
assert(M.scrollTo(600, 668, 400, 1000, 80) === 348, 'the focused row and room under it');
assert(M.scrollTo(950, 1000, 400, 1000, 80) === 600, 'never past the end');
assert(M.scrollTo(10, 78, 400, 1000, 80, 300) === 0, 'back to the top');
assert(M.mixRgba('rgba(100, 100, 100, 0.9)', 'rgba(0, 200, 255, 1)', 0.5) === 'rgba(50, 150, 178, 0.9)',
    'soft light keeps the chip alpha');

// One definition per method: a later one silently replaces an earlier one in a class.
const methods = [...panel.matchAll(/^    (_?[a-zA-Z]+)\([^)]*\) \{$/gm)].map(m => m[1]);
const twice = methods.filter((m, i) => methods.indexOf(m) !== i);
assert(!twice.length, `methods defined twice: ${twice.join(', ')}`);

// Shared TV rule: a held key's repeat only moves the focus or steps a slider (tvModel.repeatAllowed).
const model = read(args[2].replace(/tvQuickPanel\.js$/, 'tvModel.js'));
assert(/export function repeatAllowed\(action\) \{\s*return action === 'up' \|\| action === 'down' \|\| action === 'left' \|\| action === 'right';/.test(model),
    'only directions repeat');
for (const [name, source] of [['the TV home', home], ['the quick panel', panel], ['system dialogs', read(args[2].replace(/tvQuickPanel\.js$/, 'tvDialogs.js'))]])
    assert(/!repeatAllowed\(action\) && \(event\.get_flags\(\) & Clutter\.EventFlags\.FLAG_REPEATED\)\) return Clutter\.EVENT_STOP/.test(source),
        `${name}: a repeat never activates`);

// St takes background-gradient-start as the background colour: a later background-color breaks the key.
for (const [name, source] of [['the quick panel', panel], ['system dialogs', read(args[2].replace(/tvQuickPanel\.js$/, 'tvDialogs.js'))]])
    assert(!/background-gradient-start:[^`]*background-color:/.test(source), `${name}: nothing overrides the key's gradient start`);

// A property must not share a method's name (this._ok = ... once shadowed _ok(row)).
const props = new Set([...panel.matchAll(/this\.(_?[a-zA-Z]+) = /g)].map(m => m[1]));
const clash = methods.filter(m => props.has(m));
assert(!clash.length, `properties shadowing methods: ${clash.join(', ')}`);
// The Wi-Fi chip shows and switches the radio (NetworkManager's wireless-enabled), not "connected".
assert(/key: 'wifi'[\s\S]*?state: toggle => toggle\._client \? \{checked: toggle\._client\.wireless_enabled/.test(panel) &&
    /signal: 'notify::wireless-enabled'/.test(panel), 'the Wi-Fi chip follows the radio');
assert(/this\._scrim = new St\.Widget/.test(panel) && /this\._m\('sheet'\)/.test(panel), 'the page steps back under an opaque-enough island');
assert(/const owner = Boolean\(this\._quick\?\.isOpen\)/.test(home) && /opened: \(\) => this\._draw\(\)/.test(home),
    'the cluster is a chip whenever the panel is open');

// Clutter's flag is EventFlags.FLAG_REPEATED: EventFlags.REPEATED is undefined in GJS, so a test
// of it is always false (Pi, 2 Nov: every repeat of a held OK reached the panel as a press).
for (const [name, source] of [['the TV home', home], ['the quick panel', panel], ['system dialogs', read(args[2].replace(/tvQuickPanel\.js$/, 'tvDialogs.js'))]])
    assert(!/EventFlags\.REPEATED\b/.test(source) && /EventFlags\.FLAG_REPEATED/.test(source), `${name}: the real repeat flag`);

// The panel drives the desktop's objects; it creates none of its own.
assert(/_network\?\._wirelessToggle/.test(panel) && /_bluetooth\?\.quickSettingsItems/.test(panel) &&
    /_doNotDisturb\?\.quickSettingsItems/.test(panel) && /_volumeOutput\?\.quickSettingsItems/.test(panel),
    "Quick Options' own toggles and slider");
assert(!/new NM\.|GnomeBluetooth|Gvc\.MixerControl|new Gio\.Settings/.test(panel), 'no second backend');
assert(/Main\.messageTray\.getSources\(\)/.test(panel) && /\.activate\(\)\);/.test(panel), "the tray's notifications, opened by OK");
assert(/NotificationDestroyedReason\.DISMISSED/.test(panel) && /Urgency\.CRITICAL/.test(panel),
    'Clear all dismisses as the tray does, never a critical one');
assert(/SystemActions\.getDefault\(\)/.test(panel) && /activateRestart\(\)/.test(panel) &&
    /activatePowerOff\(\)/.test(panel), 'power through SystemActions (its dialogs confirm)');
assert(/this\.close\(\(\) => this\._actions\.activateRestart\(\)\)/.test(panel), 'the panel lets go before a dialog');
assert(/Main\.pushModal\(this\._actor/.test(panel) && /Main\.popModal\(this\._grab\)/.test(panel), 'it holds the remote while open');
assert(/if \(Main\.modalCount > 0\) return;/.test(panel), 'never over a dialog that holds the grab');
assert(/this\._okGate = okGate\(origin === 'bar' \?/.test(panel) && /'key-release-event'[\s\S]*?okRelease\(this\._okGate\)/.test(panel) &&
    /!okPress\(this\._okGate, /.test(panel), 'one action per OK press, from open to close');
assert(/slider\.value = value;/.test(panel), "volume through the slider's own handler");
assert(/fade_edges: false/.test(panel), 'fades only where the rows continue');

// The bar: Right past the last tab is the cluster; OK opens; Back and the
// panel's close return to it.
assert(/direction === 'right' && this\._focus\.index === PAGES\.length - 1[\s\S]*?\{kind: 'cluster'\}/.test(home),
    'right past Search reaches the cluster');
assert(/kind === 'cluster'\) this\._quick\?\.open\('bar'\)/.test(home), 'OK on the cluster opens the panel');
assert(/_quickClosed\(origin\) \{[\s\S]*?origin === 'bar'\) this\._focus = \{kind: 'cluster'\}/.test(home),
    'closing hands the focus back to the cluster');
assert(/owner[\s\S]*?m\('chip'\)/.test(home), 'the cluster is a chip while its panel is up');
assert(/_leave\(\) \{[\s\S]*?this\._quick\?\.destroy\(\)/.test(home), 'leaving TV posture ends it');
assert(/showHome\(page = 'home'\) \{[\s\S]*?this\._quick\?\.close\(\)/.test(home), 'Home closes it');
assert(/quickPanel: \(\) => this\._quick\?\.toggle\('remote'\)/.test(home), 'the remote shortcut');

// Home is held: short goes home on release, long opens the panel at once.
assert(/_homeDown\(source\) \{[\s\S]*?timeout_add\([\s\S]*?home_hold_ms[\s\S]*?this\._quickPanel\(\)/.test(input),
    'a long press opens the panel without waiting for the release');
assert(/_homeUp\(source\) \{[\s\S]*?homePress\(held, TOKENS\.quick_panel\.home_hold_ms\) === 'home'\) this\._act\('home'\)/.test(input),
    'a short press goes home');
assert(/REMOTE_KEYS\[name\] === 'home'\) this\._homeDown\(/.test(input) && /'accelerator-deactivated'[\s\S]*?this\._homeUp\(/.test(input),
    "a remote's Home");
assert(/action === 'home'\) \{[\s\S]*?this\._homeSources\.has\(source\)\) this\._homeUp\(source\)[\s\S]*?this\._homeDown\(source\)/.test(input),
    "a pad's Guide, and a release without its press is ignored");
assert(/pressed && pad\.buttons\.has\(BUTTON\.SELECT\) && pad\.buttons\.has\(BUTTON\.START\)\) \{\s*this\._press\('home'\)/.test(input),
    'Select+Start still goes home at once');

print('tv-quick-panel-policy: ok');
