// SPDX-License-Identifier: GPL-2.0-or-later
// The TV app switcher and closing apps: its rules, the remote in it, and the
// wiring that opens it from Home pressed twice and closes apps the desktop's way.
// Run: gjs -m tv-app-switcher-policy.js js/ui/lumaPresentation/tvAppSwitcherModel.js \
//        js/ui/lumaPresentation/tvTokens.js js/ui/lumaPresentation/tvAppSwitcher.js \
//        js/ui/lumaPresentation/tv.js js/ui/lumaPresentation/tvInput.js
// (node tv-app-switcher-policy.js ... works too).
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
const switcher = read(args[2]);
const home = read(args[3]);
const input = read(args[4]);
const s = tokens.app_switcher;

// Sizes: the TV norm for a double press; three cards fit inside the safe area.
assert(s.double_ms >= 250 && s.double_ms <= 400, 'a double press is a TV remote double press');
assert(s.force_after_ms >= 2000 && s.force_after_ms <= 5000, 'Force Close only after a few seconds');
assert(3 * s.card_width + 2 * s.card_gap <= tokens.layout.base_width - 2 * tokens.layout.safe_x, 'three cards in the safe area');
assert(s.row_top > tokens.bar.top + tokens.bar.height, 'the row starts under the bar');

// Home twice.
assert(M.homeSecondPress(1000, 1300, 350) && M.homeSecondPress(1000, 1350, 350), 'a second press in time');
assert(!M.homeSecondPress(1000, 1351, 350) && !M.homeSecondPress(null, 1100, 350), 'too late, or no first press');

// Apps: newest first by window, each once, then windowless running apps.
const a = {id: 'a'}, b = {id: 'b'}, c = {id: 'c'};
const order = M.switcherApps([{app: b}, {app: null}, {app: a}, {app: b}], [a, c], app => app.id);
assert(order.map(app => app.id).join() === 'b,a,c', 'Alt+Tab order, each app once, windowless apps after');
assert(!M.switcherApps([], []).length, 'nothing running, nothing listed');
const info = show => ({should_show: () => show});
assert(M.shownApp(info(true)) && M.shownApp(null) && !M.shownApp(info(false)),
    "a NoDisplay app (a system prompt) is never a card; a window without a desktop file is");

// The row scrolls only as far as the focused card and a peek need.
assert(M.rowScroll(0, tokens, 1920) === 0 && M.rowScroll(1, tokens, 1920) === 0, 'the first cards need no scroll');
const third = M.rowScroll(3, tokens, 1920);
assert(third > 0 && tokens.layout.safe_x + 4 * s.card_width + 3 * s.card_gap + s.peek - third === 1920,
    'a later card ends a peek from the screen edge');
assert(M.rowScroll(3, tokens, 4000) === 0, 'a wide screen needs no scroll');

// Closing: asked, then after the grace time, Force Close and Wait.
assert(M.closeState(null, 5000, 3000) === 'open' && M.closeState(1000, 3999, 3000) === 'closing' &&
    M.closeState(1000, 4000, 3000) === 'stuck', 'open, closing, stuck');
assert(M.cardMenu('open').join() === 'close', 'a card offers Close');
assert(!M.cardMenu('closing').length, 'nothing while it closes');
assert(M.cardMenu('stuck').join() === 'force,wait' && M.cardMenu('stuck', false).join() === 'wait',
    'Force Close only after the grace time, and only with a window to end');
assert(M.tileMenu(true).join() === 'open,close' && !M.tileMenu(false).length, 'a running tile offers Open and Close');
assert(M.okHold(0, 499, 500) === 'ok' && M.okHold(0, 500, 500) === 'menu', 'held OK is the menu');
assert(M.stepCard(0, 3, 'left') === 0 && M.stepCard(2, 3, 'right') === 2 && M.stepCard(1, 3, 'right') === 2, 'Left/Right in the row');
assert(M.afterRemoval(2, 0, 3) === 1 && M.afterRemoval(1, 1, 2) === 1 && M.afterRemoval(2, 2, 2) === 1,
    'the focus stays on the card that took the place');

// The switcher drives the shell's own app model and close path.
assert(/Shell\.AppSystem\.get_default\(\)/.test(switcher) && /get_tab_list\(Meta\.TabList\.NORMAL_ALL/.test(switcher) &&
    /Shell\.WindowTracker\.get_default\(\)/.test(switcher), "the shell's running apps, in Alt+Tab order");
assert(/sessionApplications\.requestQuit\(app\) : app\.request_quit\(\)/.test(switcher), "Close is the desktop's Quit");
assert((switcher.match(/\.kill\(\)/g) ?? []).length === 1 && /key === 'force'\) \{[\s\S]*?window\.kill\(\)/.test(switcher),
    'only Force Close ends windows');
assert(/new Clutter\.Clone\(\{source: actor\}\)/.test(switcher), "a card is the window's own picture");
assert(/Main\.pushModal\(this\._actor/.test(switcher) && /Main\.popModal\(this\._grab\)/.test(switcher), 'it holds the remote while open');
assert(/Main\.modalCount > 0\) return false/.test(switcher), 'never over a dialog that holds the grab');
assert(/insert_child_below\(this\._actor, below\)/.test(switcher) && /below: this\._track/.test(home), 'the bar stays above the scrim');
assert(/'app-state-changed'/.test(switcher) && /this\.close\('empty'\)/.test(switcher), 'a closed app leaves; the last one closes it');

// The remote: a repeat never activates; a held OK opens the menu once; a menu acts once per press.
for (const [name, source] of [['the switcher', switcher], ['the TV home', home]]) {
    assert(!/EventFlags\.REPEATED\b/.test(source) && /EventFlags\.FLAG_REPEATED/.test(source), `${name}: the real repeat flag`);
    assert(/TOKENS\.focus\.hold_ms/.test(source), `${name}: OK held for the menu`);
    assert(/okPress\(this\._(itemM|m)enu\.gate/.test(source) && /okRelease\(this\._(itemM|m)enu\.gate\)/.test(source),
        `${name}: one menu action per OK press`);
}
assert(/\.filter\(normalWindow\)[\s\S]*?\.filter\(entry => switcherApp\(entry\.app\)\)/.test(switcher) &&
    /appRunning\(app\) && switcherApp\(app\)/.test(switcher), 'system prompts are never cards');
assert(/is_skip_taskbar\(\)/.test(switcher) && /WindowType\.NORMAL/.test(switcher) && /get_transient_for\(\)/.test(switcher),
    'cards are taskbar windows: no dialogs, skip-taskbar or transient windows');
assert(/return switcherApp\(shellApp\) && appRunning\(shellApp\)/.test(home), 'no running dot for a system prompt');
assert(/action === 'up'\) \{\s*this\._openMenu\(null\)/.test(switcher), 'Up on a card drops its menu');
assert(/action === 'back'\) \{\s*this\.close\('back'\)/.test(switcher), 'Back closes it');
assert(/_keyUp\(event\) \{[\s\S]*?this\._switch\(\)/.test(switcher), 'OK switches when let go');

// Home twice, single Home, held Home.
assert(/homeSecondPress\(this\._homeTapAt, now\(\), TOKENS\.app_switcher\.double_ms\)[\s\S]*?this\._switcher\(\)/.test(input),
    'the second press opens the switcher at once');
assert(/_homeUp\(source\) \{[\s\S]*?timeout_add\(GLib\.PRIORITY_DEFAULT, TOKENS\.app_switcher\.double_ms[\s\S]*?this\._act\('home'\)/.test(input),
    'a single press goes home once the window has passed');
assert(/switcher: \(\) => this\.toggleSwitcher\(\)/.test(home), 'the TV home wires it');
assert(/showHome\(page = 'home'\) \{[\s\S]*?this\._switcher\?\.close\('home'\)/.test(home), 'Home closes it');
assert(/_leave\(\) \{[\s\S]*?this\._switcher\?\.destroy\(\)/.test(home), 'leaving TV posture ends it');
assert(/returnTo\.activate\(\)/.test(home), 'Back returns to the app it opened over');
assert(/reason === 'back' && returnTo && appRunning\(returnTo\)/.test(home), 'Back never restarts an app closed in the switcher');

// The TV home's tiles: the running dot and the held-OK menu.
assert(/watch\(this\._appSystem, 'app-state-changed', \(\) => this\._syncRunning\(\)\)/.test(home), 'the dot follows the app');
assert(/made\.dot\.visible = this\._running\(made\.app\)/.test(home), 'only running apps carry the dot');
assert(/tileMenu\(this\._running\(made\.app\)\)/.test(home) && /key === 'close' && shellApp\) requestQuit\(shellApp\)/.test(home),
    "a tile's Close is the same Quit");

print('tv-app-switcher-policy: ok');
