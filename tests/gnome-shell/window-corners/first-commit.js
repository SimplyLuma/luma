// SPDX-License-Identifier: GPL-2.0-or-later
// Source decision gate, not compositor/pixel evidence. Run under GJS with the
// exact extracted packaged js/ui/lumaWindowCorners.js as the sole argument.
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import Adw from 'gi://Adw';

if (ARGV.length !== 1)
    throw new Error('Usage: gjs -m first-commit.js lumaWindowCorners.js');
// Ensure the actual current process has libadwaita mapped, without a display.
if (Adw.get_major_version() < 1)
    throw new Error('libadwaita fixture unavailable');
const [ok, bytes] = GLib.file_get_contents(ARGV[0]);
if (!ok)
    throw new Error('Missing packaged window-corners source');
const source = new TextDecoder().decode(bytes);
const start = source.indexOf('function roundsItself(window) {');
const end = source.indexOf('export class WindowCorners {', start);
if (start < 0 || end <= start)
    throw new Error('No production corner-ownership functions found');
// Execute the unchanged production functions, including real GLib /proc IO.
// Window objects model only first-commit metadata; no Shell actor is simulated.
const Meta = {WindowClientType: {X11: 0}};
const wantsMask = new Function('GLib', 'Meta', 'MASKED_TYPES', 'RADIUS',
    `${source.slice(start, end)}\nreturn wantsMask;`)(GLib, Meta, new Set([0]), 15);
const selfPid = new Gio.Credentials().get_unix_pid();
const check = (value, message) => {
    if (!value)
        throw new Error(message);
};
const makeWindow = (changes = {}) => ({
    pid: 0, frame: {width: 0, height: 0}, type: 0,
    fullscreen: false, override: false, wmclass: null, client: 1,
    decorated: false,
    get_pid() { return this.pid; },
    get_frame_rect() { return this.frame; },
    get_window_type() { return this.type; },
    is_fullscreen() { return this.fullscreen; },
    is_override_redirect() { return this.override; },
    get_wm_class() { return this.wmclass; },
    get_client_type() { return this.client; },
    ...changes,
});
for (const first of [
    {pid: 0, frame: {width: 0, height: 0}},
    {pid: selfPid, frame: {width: 0, height: 0}},
    {pid: 0, frame: {width: 600, height: 400}},
]) {
    const window = makeWindow(first);
    check(!wantsMask(window), 'Unsettled first commit must retain client shape');
    check(window._lumaRoundsItself === undefined,
        'Unsettled first commit incorrectly cached decoration ownership');
    window.pid = selfPid;
    window.frame = {width: 600, height: 400};
    check(!wantsMask(window), 'Self-rounded libadwaita window got a second mask');
    check(window._lumaRoundsItself === true, 'Real libadwaita mapping was not recognized');
}
// Known but unreadable process retains the existing conservative mask fallback.
check(wantsMask(makeWindow({pid: 2147483647, frame: {width: 600, height: 400}})),
    'Unreadable known process lost compatibility corners');
for (const changes of [
    {override: true}, {type: 99}, {fullscreen: true},
    {wmclass: 'Waydroid.app'}, {client: 0, decorated: true},
]) {
    const window = makeWindow({pid: selfPid, frame: {width: 600, height: 400}, ...changes});
    check(!wantsMask(window), 'Existing window exclusion regressed');
    check(window._lumaRoundsItself === undefined, 'Excluded window was probed');
}
print('PASS first-commit ownership, real libadwaita detection, unreadable PID, five exclusions');
