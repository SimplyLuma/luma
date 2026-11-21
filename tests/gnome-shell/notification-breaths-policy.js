// SPDX-License-Identifier: GPL-2.0-or-later
// The notification pill's glow (0358): six breaths per notification that
// arrives, then the steady glow. Showing the pill again (closing a menu or the
// overview maps it) and re-rendering it neither start the breaths over nor
// interrupt one in flight.
// Run: gjs -m notification-breaths-policy.js js/ui/lumaNotificationBeacon.js
// (node notification-breaths-policy.js ... works too).
const gjs = typeof globalThis.ARGV !== 'undefined';
const args = gjs ? globalThis.ARGV : process.argv.slice(2);
const GLib = gjs ? (await import('gi://GLib')).default : null;
const fs = gjs ? null : await import('node:fs');
const read = path => (gjs ? new TextDecoder().decode(GLib.file_get_contents(path)[1])
    : fs.readFileSync(path, 'utf8'));
const print = gjs ? globalThis.print : console.log;

function assert(value, message) {
    if (!value)
        throw new Error(`FAIL: ${message}`);
}

const source = read(args[0]);
const breaths = Number(source.match(/^const BREATHS = (\d+);/m)?.[1]);
assert(breaths > 0, 'BREATHS is a count');

// The method body, run against a stand-in pill.
const start = source.indexOf('    _breathe() {');
const end = source.indexOf('\n    }\n', start);
assert(start >= 0 && end > start, 'lumaNotificationBeacon.js has _breathe()');
const body = source.slice(source.indexOf('{', start) + 1, end);
const breathe = new Function('Main', 'St', 'Clutter', body);

assert(/receive\(notification, quiet = false\) \{[^}]*this\._breaths = BREATHS;/.test(source),
    'a notification that arrives gives the pill its breaths');

const settings = {enable_animations: true};
const St = {Settings: {get: () => settings}};
const Clutter = {AnimationMode: {EASE_IN_OUT_SINE: 0}};
const Main = {sessionMode: {isLocked: false, isGreeter: false}};

// An ease runs until finish() completes it, like a transition in flight.
function makeGlow() {
    const glow = {opacity: 0, mapped: true, eases: 0, pending: null};
    glow.get_transition = () => (glow.pending ? {} : null);
    glow.remove_all_transitions = () => {
        glow.pending = null;
    };
    glow.ease = params => {
        glow.eases++;
        glow.pending = params;
    };
    glow.finish = () => {
        const p = glow.pending;
        glow.pending = null;
        glow.opacity = p.opacity;
        p.onComplete?.();
    };
    return glow;
}

function makePill() {
    return {_glow: makeGlow(), _destroying: false, mapped: true, _state: 'pill', _fresh: true, dnd: false};
}

const run = pill => breathe.call(pill, Main, St, Clutter);
const settle = pill => {
    let n = 0;
    while (pill._glow.pending && n++ < 100)
        pill._glow.finish();
};

// A notification arrives: the pill breathes BREATHS times, then glows.
let pill = makePill();
pill._breaths = breaths;
run(pill);
settle(pill);
assert(pill._glow.eases === breaths, `breathes ${breaths} times (${pill._glow.eases})`);
assert(pill._glow.opacity === 128, 'then keeps the steady glow');

// Shown again (a menu or the overview closed): no breaths, the steady glow.
run(pill);
assert(!pill._glow.pending, 'showing it again does not breathe');
assert(pill._glow.opacity === 128, 'showing it again keeps the steady glow');

// Re-rendered during a breath: the breath carries on.
pill = makePill();
pill._breaths = breaths;
run(pill);
pill._glow.finish();
const before = pill._glow.pending;
run(pill);
assert(pill._glow.pending === before, 'a render during a breath leaves it running');

// Hidden part way, shown again: only the breaths left.
pill._glow.mapped = false;
pill._glow.remove_all_transitions();
pill._glow.mapped = true;
run(pill);
settle(pill);
assert(pill._glow.eases === breaths, `an interrupted series finishes its ${breaths} breaths (${pill._glow.eases})`);
assert(pill._glow.opacity === 128, 'and keeps the steady glow');

// Animations off: the steady glow at once.
settings.enable_animations = false;
pill = makePill();
pill._breaths = breaths;
run(pill);
assert(pill._glow.eases === 0 && pill._glow.opacity === 128, 'without animations, the steady glow');
settings.enable_animations = true;

// Read: no glow.
pill = makePill();
pill._fresh = false;
pill._breaths = breaths;
run(pill);
assert(pill._glow.eases === 0 && pill._glow.opacity === 0, 'a read pill has no glow');

print('notification breaths policy: ok');
