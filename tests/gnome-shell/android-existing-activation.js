// SPDX-License-Identifier: GPL-2.0-or-later
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import System from 'system';
const M = await import(GLib.filename_to_uri(GLib.canonicalize_filename(ARGV[0], GLib.get_current_dir()), null));
let failed = 0;
function check(name, value) {
    if (!value) { failed++; printerr(`FAIL ${name}`); }
}
const packageName = 'org.projectluma.FrameAcceptance';
const id = `waydroid.${packageName}.desktop`;
let calls = 0;
let windows = [{get_wm_class: () => `waydroid.${packageName}`, skip_taskbar: false, is_attached_dialog: () => false}];
let marker = true;
let infoId = id;
let appId = id;
const app = {get_id: () => appId,
    get_app_info: () => ({get_id: () => infoId, get_boolean: () => marker}),
    get_windows: () => windows,
    activate_full: (workspace, timestamp) => {
        check('native activation timestamp', workspace === -1 && timestamp === 42);
        calls++;
    }};
const apps = {lookup_app: requested => requested === id ? app : null};
check('actual existing Android window restores', M.activateAndroidApplication(packageName, apps, 42));
check('once', calls === 1);
windows.push({get_wm_class: () => 'attached-dialog', skip_taskbar: false, is_attached_dialog: () => true});
check('legitimate attached dialog does not exclude managed window', M.activateAndroidApplication(packageName, apps, 42));
check('attached dialog still uses native app activation', calls === 2);
let closed = 0;
windows[0].delete = timestamp => {check('native close timestamp', timestamp === 42); closed++;};
check('normal close of exact installed managed window', M.closeAndroidApplication(packageName, apps, 42));
check('only managed window closed', closed === 1);

for (const bad of ['', '../org.foo', 'org.foo.desktop', 'org.foo;true', 'org..foo', 'a'.repeat(256), null, 'one']) {
    // org.foo.desktop is syntactically a package, but no exact app is installed.
    check(`bad or uninstalled ${bad}`, !M.activateAndroidApplication(bad, apps, 42));
}
marker = false;
check('generated marker required', !M.activateAndroidApplication(packageName, apps, 42));
marker = true; infoId = 'other.desktop';
check('exact appinfo identity', !M.activateAndroidApplication(packageName, apps, 42));
infoId = id; appId = 'other.desktop';
check('exact ShellApp identity', !M.activateAndroidApplication(packageName, apps, 42));
appId = id; windows = [];
check('does not start an absent window', !M.activateAndroidApplication(packageName, apps, 42));
windows = [{get_wm_class: () => 'other', skip_taskbar: false, is_attached_dialog: () => false}];
check('foreign window refused', !M.activateAndroidApplication(packageName, apps, 42));
windows = [{get_wm_class: () => `waydroid.${packageName}`, skip_taskbar: true, is_attached_dialog: () => false}];
check('taskbar exclusion refused', !M.activateAndroidApplication(packageName, apps, 42));
check('negatives did not activate', calls === 2);
check('notification exact id', M.androidPackageFromDesktopId(id) === packageName);
for (const bad of ['other.org.foo.desktop', 'waydroid.org.foo', 'waydroid../foo.desktop', '', null])
    check('bad notification identity', M.androidPackageFromDesktopId(bad) === null);
const initial = {owner: ':1.42', generation: 1};
check('live owner', M.sameAndroidOwner(':1.42', initial, initial, ':1.42'));
check('not uid/name prefix', !M.sameAndroidOwner(':1.420', initial, initial, ':1.42'));
check('absent owner', !M.sameAndroidOwner(':1.42', initial, {owner: null, generation: 2}, ':1.42'));
check('replaced owner', !M.sameAndroidOwner(':1.42', initial, {owner: ':1.43', generation: 2}, ':1.43'));
check('lost then reacquired owner', !M.sameAndroidOwner(':1.42', initial, {owner: ':1.42', generation: 3}, ':1.42'));
check('authoritative owner mismatch', !M.sameAndroidOwner(':1.42', initial, initial, ':1.43'));
let replies = [];
const invocation = {return_gerror: error => {
    check('reply accepts real GLib.Error', error instanceof GLib.Error);
    replies.push(error);
}};
M.replyAndroidRequestError(invocation, new Error('private window detail'));
check('ordinary error returns bounded generic failure', replies.length === 1 &&
    replies[0].matches(Gio.DBusError, Gio.DBusError.FAILED) &&
    replies[0].message === 'The Android window request failed.');
const denied = new GLib.Error(Gio.DBusError, Gio.DBusError.ACCESS_DENIED, 'refused');
M.replyAndroidRequestError(invocation, denied);
check('real GLib error retained and replied once', replies.length === 2 && replies[1] === denied);
if (failed) System.exit(1);
print('android-existing-activation: PASS');
// Caller admission is a host process check, not a UID/name/basename shortcut.
for (const bad of ['', 'org.example.App', ':x.y', ':1', ':1.2/../../', ':1.' + '2'.repeat(256), null]) {
    check('invalid forwarded sender', !M.validNativeAndroidSender(bad));
}
check('valid unique sender', M.validNativeAndroidSender(':1.42'));
function bus(values) {
    let count = 0;
    return {call: async (_d,_p,_i,method) => {
        count++;
        return new GLib.Variant('(u)', [values[method].shift()]);
    }, count: () => count};
}
let credentials = bus({GetConnectionUnixUser: [1000,1000], GetConnectionUnixProcessID: [42,42]});
let inspections = [];
const actor = await M.authenticateNativeAndroidCaller(credentials, ':1.42', (pid,uid) => {
    inspections.push([pid,uid]); return '100';
});
check('derives and rechecks actual bus identity', actor.pid === 42 && actor.uid === 1000 &&
    credentials.count() === 4 && inspections.length === 2);
async function refused(name, values, inspect) {
    let accepted = true;
    try { await M.authenticateNativeAndroidCaller(bus(values), ':1.42', inspect); }
    catch (_error) {accepted = false;}
    check(name, !accepted);
}
await refused('replaced PID refused', {GetConnectionUnixUser:[1000,1000],GetConnectionUnixProcessID:[42,43]}, ()=>'100');
await refused('changed UID refused', {GetConnectionUnixUser:[1000,1001],GetConnectionUnixProcessID:[42,42]}, ()=>'100');
let starts=['100','101'];
await refused('PID reuse refused', {GetConnectionUnixUser:[1000,1000],GetConnectionUnixProcessID:[42,42]},()=>starts.shift());
for (const code of [Gio.IOErrorEnum.PERMISSION_DENIED, Gio.IOErrorEnum.FAILED]) {
    await refused('process inspection error refuses', {GetConnectionUnixUser:[1000,1000],GetConnectionUnixProcessID:[42,42]},()=>{throw new GLib.Error(Gio.IOErrorEnum,code,'inspection failed');});
}
let invalidBusCalls=0;
try {await M.authenticateNativeAndroidCaller({call:()=>{invalidBusCalls++;}}, 'org.fake.Name',()=> '100');}
catch (_error) {}
check('invalid caller no bus lookup', invalidBusCalls === 0);
if (failed) System.exit(1);
print('android-native-caller-credentials: PASS');
