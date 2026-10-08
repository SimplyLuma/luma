// SPDX-License-Identifier: GPL-2.0-or-later
// Check the packaged lifecycle tracker contract without starting a Shell session.
import Gio from 'gi://Gio';

const [ok, bytes] = Gio.File.new_for_path(ARGV[0]).load_contents(null);
if (!ok)
    throw new Error('Cannot read packaged sessionApplications.js');
const source = new TextDecoder().decode(bytes);

function assert(value, why) {
    if (!value)
        throw new Error(why);
}

const mergeStart = source.indexOf('export function mergePids');
const mergeEnd = source.indexOf('\n}\n', mergeStart) + 2;
const mergeSource = source.slice(mergeStart, mergeEnd).replace('export ', '');
const mergePids = new Function(`${mergeSource}; return mergePids;`)();
assert(JSON.stringify(mergePids([31, 20, 0], [20, 42, -1])) ===
    JSON.stringify([20, 31, 42]), 'PID identity must be positive, unique, and stable');

const subscribe = source.indexOf("connectSignal('NameOwnerChanged'");
const snapshot = source.indexOf('ListNamesAsync()');
assert(subscribe > 0 && subscribe < snapshot,
    'D-Bus ownership subscription must precede the initial snapshot');
const staleOwnerCheck = source.indexOf('registration.owner !== owner');
const pidAssignment = source.indexOf('registration.pid = pid');
assert(staleOwnerCheck > 0 && staleOwnerCheck < pidAssignment,
    'A late PID result must not attach to a replacement bus owner');
assert(source.includes("get_boolean?.('DBusActivatable')"),
    'Only registered D-Bus application desktop IDs may extend lifecycle');
assert(source.includes("'player-added'") && source.includes("'player-removed'"),
    'MPRIS lifecycle must be event-driven');
assert(source.includes("activate_action('quit'"),
    'Headless applications must receive their supported quit action');
for (const forbidden of [
    'kill(', 'force_exit', 'setInterval', 'timeout_add', 'get_ppid', '/proc/',
])
    assert(!source.includes(forbidden), `Lifecycle tracker must not use ${forbidden}`);

print('PASS registered D-Bus/MPRIS lifecycle, PID identity, graceful quit, no polling');
