// SPDX-License-Identifier: GPL-2.0-or-later
// Quick Options' Sound page (0360): which outputs are chips, which apps are
// playing rows and what each says, how tall the list is, and that the page,
// the St media row and the TV panel are wired to the shared mixer model.
// Run: gjs -m sound-page-policy.js js/ui/lumaPresentation/soundPageModel.js \
//        js/ui/lumaPresentation/mediaRowTokens.js js/ui/lumaPresentation/soundPage.js \
//        js/ui/lumaPresentation/mediaRow.js js/ui/lumaPresentation/tvQuickPanel.js \
//        js/ui/status/volume.js js/ui/quickSettings.js
// (node sound-page-policy.js ... works too).
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
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

const M = await import(`file://${absolute(args[0])}`);
const {mediaRow: tokens} = await import(`file://${absolute(args[1])}`);
const [page, row, tv, volume, quickSettings] = args.slice(2).map(read);

// Outputs: four or fewer are all chips; more are the three most recently
// used (the one in use always) and "More…".
const out = (name, extra = {}) => ({name, label: name, kind: 'speakers', ...extra});
const four = [out('Speakers', {default: true}), out('WH-1000XM5', {kind: 'headphones'}),
    out('Studio monitors', {kind: 'usb'}), out('Living room', {kind: 'airplay'})];
assert(same(M.outputChips(four), {chips: four, more: false}), 'four outputs are four chips');
const six = [out('A', {last_used: 5}), out('B', {last_used: 50}), out('C', {default: true, last_used: 1}),
    out('D', {last_used: 40}), out('E'), out('F', {last_used: 9})];
const packed = M.outputChips(six);
assert(packed.more && same(packed.chips.map(o => o.name), ['B', 'C', 'D']),
    'more than four: the current output and the two used last, in list order, then More');
assert(M.MAX_OUTPUT_CHIPS === tokens.output.max_count, 'the chip limit is the token');
// An AirPlay speaker found nearby is an output like any other.
const nearby = [{id: 'r1', name: 'Kitchen', state: 'idle', supported: true},
    {id: 'r2', name: 'Living room', state: 'connected', supported: true, node_name: 'Living room'},
    {id: 'r3', name: 'Far', state: 'unavailable', supported: true},
    {id: 'r4', name: 'Old', state: 'idle', supported: false}];
const three = four.slice(0, 3);
const withAirPlay = M.outputChips(three, nearby.filter(r => r.id !== 'r2'));
assert(!withAirPlay.more && same(withAirPlay.chips.map(o => o.label ?? o.name),
    ['Speakers', 'WH-1000XM5', 'Studio monitors', 'Kitchen']) &&
    withAirPlay.chips[3].receiver === 'r1' && withAirPlay.chips[3].kind === 'airplay',
    'up to four: a nearby AirPlay speaker is a chip; one out of reach or unsupported is not');
assert(same(M.outputChips(four, nearby).chips.map(o => o.name), ['Speakers', 'WH-1000XM5', 'Studio monitors']) &&
    M.outputChips(four, nearby).more, 'a connected speaker is listed once, and a fifth entry brings More');
assert(M.outputGlyph({kind: 'headphones'}) === 'lumaui-headphones-symbolic' &&
    M.outputGlyph({kind: 'usb'}) === 'lumaui-speaker-symbolic' &&
    M.outputGlyph({kind: 'airplay'}) === 'lumaui-airplay-symbolic' &&
    M.outputGlyph({kind: 'display'}) === 'lumaui-monitor-symbolic' &&
    M.outputGlyph({kind: 'speakers'}) === 'lumaui-volume-2-symbolic' &&
    M.outputGlyph({}) === 'lumaui-volume-2-symbolic', 'each kind of output has its Lucide glyph');
assert(M.outputLabel({label: 'Speakers', description: 'Analog Output'}) === 'Speakers',
    'a chip reads the label, never the card or profile');

// Playing: one row per app with a stream; never the silent other sounds.
const apps = [
    {id: '@system', kind: 'system', listed: false, other: true},
    {id: 'org.projectluma.Phone', kind: 'app', listed: false, other: true, name: 'Phone'},
    {id: 'tide', kind: 'app', listed: true, name: 'Tide', player: 'p', title: 'God Only Knows', playing: true},
    {id: 'web', kind: 'app', listed: true, name: 'YouTube', player: 'p', title: 'Synth', playing: false,
        output: 'WH-1000XM5'},
    {id: 'game', kind: 'app', listed: true, name: 'Game', player: '', playing: true},
    {id: 'gone', kind: 'app', listed: true, name: 'Gone', player: '', playing: false, streams: []},
];
const rows = M.playingRows(apps);
assert(same(rows.map(a => a.id), ['tide', 'web', 'game', 'gone']),
    'apps with a stream (or one in the last ten seconds) are rows; silent other sounds are not');
assert(M.playingCount(rows) === 2, 'the header counts apps that are not paused');
assert(M.playingCount([]) === 0, 'nothing playing counts nothing');

// What a row says.
const words = {muted: 'Muted', paused: 'Paused', on: name => `on ${name}`};
const outputs = [out('Speakers', {default: true}), out('WH-1000XM5')];
assert(M.rowTitle(rows[0]) === 'God Only Knows', 'the title is the player\'s');
assert(M.rowTitle({name: 'Viola', player: 'p', title: ''}, 'Luma launch film') === 'Luma launch film',
    'a player without a title is named by the window');
assert(M.rowTitle(rows[2], 'A window') === 'Game', 'an app without a player is its own name');
assert(M.rowLine(rows[0], outputs, words) === 'Tide', 'under a playing title: the app');
assert(M.rowLine(rows[1], outputs, words) === 'YouTube · on WH-1000XM5',
    'routed away from the main output, it says where (and not that it is paused)');
assert(M.rowLine({...rows[1], output: ''}, outputs, words) === 'YouTube · Paused', 'paused on the main output');
assert(M.rowLine({...rows[1], output: 'Speakers'}, outputs, words) === 'YouTube · Paused',
    'sent to what is now the main output is not "on"');
assert(M.rowLine({...rows[1], output: 'Unplugged'}, outputs, words) === 'YouTube · Paused',
    'an output that is not there is not named');
assert(M.rowLine({...rows[0], muted: true}, outputs, words) === 'Muted', 'muted says only that');
assert(M.rowLine(rows[2], outputs, words) === '', 'an app without a player repeats nothing under its name');
const tabs = {name: 'Viola', player: 'p', title: '2 tabs playing', artist: 'simplyluma.com, video.example',
    tabs: 2, playing: true};
assert(M.rowTitle(tabs) === '2 tabs playing' &&
    M.rowLine(tabs, outputs, words) === 'Viola · simplyluma.com, video.example' &&
    M.rowLine({...tabs, tabs: 1, title: 'Film', artist: 'simplyluma.com'}, outputs, words) === 'Viola',
    'a browser playing in several tabs names their sites under the count');
assert(M.rowLine(rows[3], outputs, words) === 'Paused', 'and says Paused when it is');

// Left and Right: 5 % at a time, on the grid, inside the range.
assert(Math.abs(M.stepVolume(0.62, 1) - 0.65) < 1e-9 && Math.abs(M.stepVolume(0.62, -1) - 0.55) < 1e-9,
    'a step lands on the 5 % grid');
assert(M.stepVolume(0.98, 1) === 1 && M.stepVolume(0.02, -1) === 0 && M.stepVolume(1, 1, 1.5) > 1,
    'a step stays inside the range');

// Three rows, then the list scrolls; a short screen shows fewer, never none.
const {row: tall, list_gap: gap} = tokens.compact;
assert(M.VISIBLE_ROWS === tokens.compact.visible_count, 'three rows is the token');
assert(M.listHeight(2, tall, gap) === 2 * tall + gap, 'two rows are as tall as two rows');
assert(M.listHeight(7, tall, gap) === 3 * tall + 2 * gap, 'seven rows show three');
assert(M.listHeight(7, tall, gap, 2 * tall + gap + 1) === 2 * tall + gap, 'a short screen shows two');
assert(M.listHeight(7, tall, gap, 10) === tall, 'and never less than one');
assert(M.listHeight(0, tall, gap) === 0, 'no rows, no list');

// The page: held only while its sheet is mapped; no meters, nothing polls.
assert(/this\._presence\.connectObject\('notify::mapped', \(\) => this\._setShown\(this\._presence\.mapped\), this\)/.test(page),
    'the page follows whether its sheet is on screen');
assert(/if \(shown\) \{\s*this\._mixer\.hold\(\);\s*this\._render\(\);\s*\} else \{\s*this\._mixer\.release\(\);/.test(page),
    'on screen the model is held, off screen it is released');
assert(!/watchLevels|timeout_add|setInterval/.test(page) && !/timeout_add|setInterval/.test(row),
    'the page and its rows watch no levels and run no timers');
assert(/master\.bind_property\('value', slider, 'value',\s*GObject\.BindingFlags\.SYNC_CREATE \| GObject\.BindingFlags\.BIDIRECTIONAL\)/.test(page),
    'the master pill is the main page\'s volume, both ways (volume keys move it)');
assert(/new FillSlider\(0\)/.test(page) && /'lumaui-filled-slider'/.test(page), 'the master is the shared filled level');
assert(/Model\.playingRows\(this\._mixer\.applications\)/.test(page), 'rows are the playing apps');
assert(/this\._playing\.visible = any;\s*this\._nothing\.visible = !any;/.test(page),
    'nothing playing: no label or list, one quiet line');
assert(/_\('Nothing playing'\)/.test(page) && /_\('Volume mixer'\)/.test(page) && /_\('Playing'\)/.test(page),
    'the page\'s words');
assert(/this\.emit\('more-outputs'\)/.test(page) && /_\('More…'\)/.test(page), 'More… opens the full list');
assert(/this\._mixer\.setDefaultOutput\(output\.name\)/.test(page), 'one tap on a chip switches');
assert(/row\.connect\('play-pause', \(\) => this\._mixer\.playPause\(app\.id\)\)/.test(page) &&
    /row\.connect\('raise', \(\) => this\._mixer\.raise\(app\.id\)\)/.test(page) &&
    /row\.connect\('mute-toggled', \(r, muted\) => this\._mixer\.setMuted\(app\.id, muted\)\)/.test(page),
    'a row\'s play key, artwork and badge reach the mixer');
assert(/canPlayPause: !!\(app\.can_play \|\| app\.can_pause\)/.test(page), 'no player, no play key');

// The St row reads the LumaUI media row's tokens and answers the keys.
assert(/import \{mediaRow as tokens\} from '\.\/mediaRowTokens\.js';/.test(row), 'the St row is drawn from the tokens');
assert(tokens.compact.art === 40 && tokens.compact.badge === 17 && tokens.compact.play === 32 &&
    tokens.compact.row === 60 && tokens.touch.row === 64 && tokens.touch.art === 44 &&
    tokens.touch.play === 44, 'compact and touch metrics');
assert(/case Clutter\.KEY_space:[\s\S]*?this\.emit\('play-pause'\)/.test(row), 'Space plays or pauses the focused row');
assert(/case Clutter\.KEY_m:\s*case Clutter\.KEY_M:\s*this\.emit\('mute-toggled', !this\._state\.muted\)/.test(row),
    'M mutes the focused row');
assert(/stepVolume\(this\.slider\.value, up \? 1 : -1, this\._maximum\)/.test(row), 'Left and Right step the volume');
assert(/if \(this\._raisedOnce\) \{\s*this\.emit\('mute-toggled', !this\._state\.muted\);\s*\} else \{\s*this\._raisedOnce = true;\s*this\.emit\('raise'\);/.test(row),
    'the artwork: first press shows the app, the second mutes');
assert(/press\(this\._badge, \(\) => this\.emit\('mute-toggled', !this\._state\.muted\)\)/.test(row), 'the badge mutes');

// The model and the sheet in status/volume.js.
assert(/const MIXER_VERSION = 3;/.test(volume), 'the page needs interface version 3');
assert(!/AppMixerRow|AppMixerSection|luma-app-mixer/.test(volume), 'the old rows are gone');
assert(/this\.menu\._lumaPlainPage = true;/.test(volume) && /new SoundPage\(\{/.test(volume), 'the sheet is the Sound page');
assert(/this\.menu\._lumaBack = \(\) => \{/.test(volume), 'Back leaves the full output list first');
assert(/this\._deviceSection\.actor\.visible = !service;/.test(volume),
    'without the service the sheet is the output list');
assert(/Util\.spawn\(\['gnome-control-center', 'sound-mixer'\]\)/.test(volume), 'the link opens Settings\' Volume mixer');
assert(/_lumaBack\?\.\(\)/.test(quickSettings) && /this\._pageMeta/.test(quickSettings) &&
    /menu\._lumaPageOpened\?\.\(\{/.test(quickSettings), 'the page host asks the page');
assert(/handleEscape\(\) \{[\s\S]*?if \(!menu\._lumaBack\?\.\(\)\)\s*menu\.close\(\);\s*return true;/.test(quickSettings),
    'Esc steps out of the output list, then back to the main page');
assert(/_renderAll\(outputs\)/.test(page) && /Model\.outputLabel\(output\)/.test(page) && /output\.detail/.test(page),
    'the full output list reads the same labels, with how each connects');

// TV: the same rows; OK plays or pauses.
assert(/playingRows\(mixer\.applications\)/.test(tv), 'the TV panel lists the playing rows');
assert(/mixer\.outputs\.map\(output => \(\{id: `node:\$\{output\.name\}`, node: output\.name, name: outputLabel\(output\)/.test(tv) &&
    /if \(row\.output\.node\) \{\s*item\?\.appMixer\?\.setDefaultOutput\(row\.output\.node\);/.test(tv),
    'the TV panel names and chooses outputs as the Sound page does');
assert(/if \(row\.app\.canPlayPause\) \{\s*mixer\?\.playPause\(row\.app\.id\);/.test(tv), 'OK plays or pauses');

print('sound-page-policy: PASS');
