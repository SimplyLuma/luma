// SPDX-License-Identifier: GPL-2.0-or-later
// The Well's background apps (Shell patch 0349): apps stay in view while
// they fit, the overflow exists only when it hides two or more apps and
// shows their faces, and a browser's status icon or notification belongs to
// the web app it speaks for, else to the browser -- never a mix of the two.
// Run: gjs -m well-overflow-policy.js js/ui/lumaWellModel.js js/ui/lumaWell.js \
//        js/ui/notificationDaemon.js
// (node well-overflow-policy.js ... works too).
const gjs = typeof globalThis.ARGV !== 'undefined';
const args = gjs ? globalThis.ARGV : process.argv.slice(2);
const GLib = gjs ? (await import('gi://GLib')).default : null;
const fs = gjs ? null : await import('node:fs');
const url = gjs ? null : await import('node:url');
const read = path => (gjs ? new TextDecoder().decode(GLib.file_get_contents(path)[1])
    : fs.readFileSync(path, 'utf8'));
const uri = path => (gjs
    ? GLib.filename_to_uri(GLib.canonicalize_filename(path, GLib.get_current_dir()), null)
    : url.pathToFileURL(path).href);
const print = gjs ? globalThis.print : console.log;

function assert(value, message) {
    if (!value)
        throw new Error(`FAIL: ${message}`);
}

const M = await import(uri(args[0]));
const well = read(args[1]);
const daemon = read(args[2]);

// ── Overflow ──────────────────────────────────────────────────────────
const apps = n => Array.from({length: n}, (_, i) => ({key: `app${i}`, status: 'Active'}));
const keys = list => list.map(item => item.key).join(',');
const none = {pins: [], hiddenWhenIdle: []};

let r = M.arrange(apps(2), {...none, now: 0});
assert(keys(r.shown) === 'app0,app1' && r.rest.length === 0, 'two background apps are both in view');

r = M.arrange(apps(2), {pins: ['app0'], hiddenWhenIdle: [], now: 0});
assert(keys(r.shown) === 'app0,app1' && r.rest.length === 0,
    'one kept and one other app: both in view, no overflow for one app');

r = M.arrange(apps(1), {...none, capacity: 1, now: 0});
assert(r.shown.length === 1 && r.rest.length === 0, 'one app is never folded away');

r = M.arrange(apps(5), {...none, capacity: 4, now: 0});
assert(keys(r.shown) === 'app0,app1,app2' && keys(r.rest) === 'app3,app4',
    'five apps on a desktop: three in view and an overflow holding two');

r = M.arrange(apps(4), {...none, capacity: 4, now: 0});
assert(r.shown.length === 4 && r.rest.length === 0, 'four apps fit a desktop Well');

r = M.arrange(apps(3), {...none, capacity: M.capacityFor('phone'), now: 0});
assert(r.shown.length === 1 && r.rest.length === 2, 'a phone-width Well holds fewer');

r = M.arrange(apps(6), {pins: ['app5', 'app4', 'app3', 'app2'], hiddenWhenIdle: [], capacity: 4, now: 0});
assert(keys(r.shown) === 'app5,app4,app3,app2' && keys(r.rest) === 'app0,app1',
    'kept apps stay in view in their order; the others fold away together');

r = M.arrange([{key: 'a', status: 'Passive'}, ...apps(1)], {pins: [], hiddenWhenIdle: ['a'], now: 0});
assert(keys(r.shown) === 'app0' && r.rest.length === 0, 'an idle app hidden when idle is in neither');

r = M.arrange([...apps(5), {key: 'ask', status: 'NeedsAttention'}], {...none, capacity: 4, now: 0});
assert(r.shown.some(item => item.key === 'ask'), 'an app asking for attention stays in view');

assert(M.capacityFor('tablet') === 4 && M.capacityFor('fold') === 3 && M.capacityFor(undefined) === 4,
    'capacity follows the form of the screen');

for (let n = 0; n <= 9; n++) {
    for (let pinned = 0; pinned <= 4; pinned++) {
        for (const capacity of [1, 2, 3, 4]) {
            const items = apps(n);
            const out = M.arrange(items, {pins: items.slice(0, pinned).map(item => item.key),
                hiddenWhenIdle: [], capacity, now: 0});
            assert(out.rest.length !== 1, `never one app behind the overflow (n=${n} kept=${pinned} cap=${capacity})`);
            assert(out.shown.length + out.rest.length === n, 'every app is somewhere');
            const fixed = Math.min(pinned, n);
            const places = out.shown.length + (out.rest.length ? 1 : 0);
            assert(places <= Math.max(capacity, fixed + 1), `the Well stays within its places (n=${n} cap=${capacity})`);
        }
    }
}

// ── Identity ──────────────────────────────────────────────────────────
const CHATGPT = 'chrome-cadlkienfkclaiaibeoongdcgmdikeeg-Default.desktop';
const SLACK = 'chrome-bnmkfklbkkhblogaoicfbffmkkdakgfn-Default.desktop';
const VIOLA_NOTES = 'viola-aghbiahbpaijignceidepookljebhfak-Default.desktop';
const installed = [
    {id: 'google-chrome.desktop', name: 'Google Chrome', exec: '/usr/bin/google-chrome-stable', categories: 'Network;WebBrowser;'},
    {id: CHATGPT, name: 'ChatGPT', wmClass: 'crx_cadlkienfkclaiaibeoongdcgmdikeeg', exec: '/opt/google/chrome/google-chrome'},
    {id: SLACK, name: 'Slack', wmClass: 'crx_bnmkfklbkkhblogaoicfbffmkkdakgfn', exec: '/opt/google/chrome/google-chrome'},
    {id: 'com.rhyme.viola.desktop', name: 'Viola', exec: '/usr/bin/viola-browser', categories: 'Network;WebBrowser;'},
    {id: VIOLA_NOTES, name: 'Notes', wmClass: 'crx_aghbiahbpaijignceidepookljebhfak', exec: '/usr/bin/viola-browser'},
    {id: 'org.projectluma.Notes.desktop', name: 'Notes', exec: 'notes'},
    {id: 'nm-applet.desktop', name: 'Network', exec: 'nm-applet'},
];
const catalog = {
    lookup: id => installed.find(desc => desc.id === id) ?? null,
    installed: () => installed,
};

assert(M.webAppOf(catalog.lookup(CHATGPT))?.appId === 'cadlkienfkclaiaibeoongdcgmdikeeg', 'a crx desktop entry is a web app');
assert(M.webAppOf({id: 'x.desktop', wmClass: 'crx_cadlkienfkclaiaibeoongdcgmdikeeg'}), 'a crx_ window class is a web app');
assert(!M.webAppOf(catalog.lookup('google-chrome.desktop')) && M.isBrowser(catalog.lookup('google-chrome.desktop')),
    'the browser is a browser, not a web app');
assert(M.browserOf(catalog.lookup(VIOLA_NOTES), catalog)?.id === 'com.rhyme.viola.desktop',
    'a Viola web app belongs to Viola');

const status = facts => M.statusItemApp({id: 'chrome_status_icon_1', title: '', tooltip: '', ...facts}, catalog);
let who = status({tooltip: 'ChatGPT', hint: 'google-chrome.desktop'});
assert(who?.id === CHATGPT && who.via === 'web-app', 'a web app\'s status icon is the web app, not "Chrome"');
who = status({tooltip: 'ChatGPT', hint: CHATGPT});
assert(who?.id === CHATGPT, 'the same when the process was placed under the web app');
who = status({tooltip: 'Google Chrome', hint: CHATGPT});
assert(who?.id === 'google-chrome.desktop' && who.via === 'browser',
    'the browser\'s own status icon is the browser, even when its process was placed under a web app');
who = status({tooltip: ''});
assert(who?.id === 'google-chrome.desktop', 'a browser status icon that names nothing is the browser');
who = status({tooltip: 'Notes', hint: 'com.rhyme.viola.desktop'});
assert(who?.id === VIOLA_NOTES, 'a Viola web app is found among Viola\'s own web apps');
who = status({tooltip: 'Notes', hint: 'google-chrome.desktop'});
assert(who?.id === 'google-chrome.desktop', 'another browser\'s web app is not this browser\'s');
// Chrome and Viola both installed, and the host knows only the program.
who = status({tooltip: '', program: '/opt/google/chrome/chrome'});
assert(who?.id === 'google-chrome.desktop', 'a status icon from Chrome\'s program is Chrome');
who = status({tooltip: '', program: '/usr/lib64/viola/viola'});
assert(who?.id === 'com.rhyme.viola.desktop', 'a status icon from Viola\'s program is Viola, not Chrome');
who = status({tooltip: 'Notes', program: '/usr/lib64/viola/viola'});
assert(who?.id === VIOLA_NOTES, 'a Viola web app is found from Viola\'s program');
who = status({tooltip: 'ChatGPT'});
assert(who?.id === CHATGPT, 'with no process known, the one web app named is still found');
assert(M.familyOfProgram('/usr/lib64/chromium-browser/chromium-browser') === 'chromium' &&
    M.familyOfProgram('/opt/google/chrome/chrome') === 'chrome' && M.familyOfProgram('/usr/bin/python3') === null,
    'a program maps to its browser family');
who = M.statusItemApp({id: 'nm-applet', title: 'Network', tooltip: '', desktopEntry: 'nm-applet'}, catalog);
assert(who?.id === 'nm-applet.desktop' && who.via === 'entry', 'an item that names its desktop entry keeps it');
assert(M.statusItemApp({id: 'nm-applet', title: '', tooltip: ''}, catalog) === null,
    'an item from no browser is left to its own name and icon');

const chrome = catalog.lookup('google-chrome.desktop');
assert(M.notificationApp(chrome, 'chatgpt.com\n\nYour answer is ready', catalog) === CHATGPT,
    'a notification from a web app\'s site is the web app\'s');
assert(M.notificationApp(chrome, 'app.slack.com\n\nNew message', catalog) === SLACK, 'a subdomain still finds its web app');
assert(M.notificationApp(chrome, 'example.com\n\nHello', catalog) === null, 'a site with no web app stays the browser\'s');
assert(M.notificationApp(chrome, 'Hello there', catalog) === null, 'a body with no origin stays the browser\'s');
assert(M.notificationApp(catalog.lookup('nm-applet.desktop'), 'chatgpt.com\n\nx', catalog) === null,
    'only a browser\'s notifications are re-homed');

// ── The Shell uses the model ──────────────────────────────────────────
assert(/from '\.\/lumaWellModel\.js'/.test(well) && !/^export function arrange/m.test(well),
    'the Well arranges with the model');
assert(!well.includes('view-app-grid-symbolic'), 'the overflow is not a generic grid square');
assert(/luma-well-stack-face/.test(well) && /_stackCount/.test(well), 'the overflow shows stacked faces and a count');
assert(/statusItemApp\(/.test(well) && /facesAsApp/.test(well), 'a status item\'s name and face come from one app');
assert(/GetConnectionUnixProcessID/.test(well), 'a browser status icon is placed by its owner\'s process');
assert(/capacity: this\._capacity\(\)/.test(well), 'the Well passes its capacity to the model');
assert(/notificationApp\(/.test(daemon) && /this\._getApp\(pid, appId, appName, body\)/.test(daemon),
    'notifications are placed with the same identity rules');

print('well overflow and identity: ok');
