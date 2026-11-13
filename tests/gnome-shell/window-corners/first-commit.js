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
// A service-channel client's PID is the D-Bus peer: for a Flatpak (Filer)
// that is the instance's xdg-dbus-proxy. Its app-flatpak scope holds the app.
const scope = '/user.slice/user-1000.slice/user@1000.service/app.slice';
const procFiles = {
    '/proc/700/maps': '7f00-7f10 r-xp 0 00:00 1 /usr/bin/xdg-dbus-proxy\n',
    '/proc/700/cgroup': `0::${scope}/app-flatpak-org.gnome.Nautilus-3878745003.scope\n`,
    [`/sys/fs/cgroup${scope}/app-flatpak-org.gnome.Nautilus-3878745003.scope/cgroup.procs`]: '699\n700\n703\n704\n',
    '/proc/699/maps': '7f00-7f10 r-xp 0 00:00 1 /usr/bin/bwrap\n',
    '/proc/703/maps': '7f00-7f10 r-xp 0 00:00 1 /usr/bin/bwrap\n',
    '/proc/704/maps': '7f00-7f10 r-xp 0 00:00 1 /usr/lib/x86_64-linux-gnu/libadwaita-1.so.0\n',
    '/proc/800/maps': '7f00-7f10 r-xp 0 00:00 1 /opt/chromium/chrome\n',
    '/proc/800/cgroup': `0::${scope}/app-gnome-chromium-800.scope\n`,
    [`/sys/fs/cgroup${scope}/app-gnome-chromium-800.scope/cgroup.procs`]: '800\n801\n',
    '/proc/801/maps': '7f00-7f10 r-xp 0 00:00 1 /usr/lib64/libadwaita-1.so.0\n',
    '/proc/900/maps': '7f00-7f10 r-xp 0 00:00 1 /app/chrome\n',
    '/proc/900/cgroup': `0::${scope}/app-flatpak-com.example.Chat-12.scope\n`,
    [`/sys/fs/cgroup${scope}/app-flatpak-com.example.Chat-12.scope/cgroup.procs`]: '900\n',
};
const FakeGLib = {
    file_get_contents(path) {
        if (!(path in procFiles))
            throw new Error(`No such file: ${path}`);
        return [true, new TextEncoder().encode(procFiles[path])];
    },
};
const instanceMask = new Function('GLib', 'Meta', 'MASKED_TYPES', 'RADIUS',
    `${source.slice(start, end)}\nreturn wantsMask;`)(FakeGLib, Meta, new Set([0]), 15);
check(!instanceMask(makeWindow({pid: 700, frame: {width: 600, height: 400}})),
    'Flatpak reported by its D-Bus proxy lost its own corners');
check(instanceMask(makeWindow({pid: 800, frame: {width: 600, height: 400}})),
    'A non-Flatpak scope lent libadwaita to a window that does not round itself');
check(instanceMask(makeWindow({pid: 900, frame: {width: 600, height: 400}})),
    'A Flatpak without libadwaita was left square');
print('PASS first-commit ownership, real libadwaita detection, unreadable PID, five exclusions, Flatpak instances');
