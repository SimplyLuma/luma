// SPDX-License-Identifier: GPL-2.0-or-later
// Execute the actual patched method bodies with deterministic ShellApp fixtures.
import Gio from 'gi://Gio';
const [ok, bytes] = Gio.File.new_for_path(ARGV[0]).load_contents(null);
if (!ok) throw new Error('Cannot read patched introspect.js');
const source = new TextDecoder().decode(bytes);
const classSource = source.slice(source.indexOf('export class IntrospectService')).replace('export class', 'class');
const Service = new Function('GLib', 'global', `${classSource}; return IntrospectService;`)({
    Variant: class { constructor(type, value) { this.type = type; this.value = value; } },
}, {display: {get_current_time_roundtrip: () => 12345}});
function assert(value, why) { if (!value) throw new Error(why); }
let quit = 0;
const order = [];
const app = {get_id: () => 'org.example.Editor.desktop', get_pids: () => [52, 53],
    get_windows: () => [{transient_for: null}], activate_full: (workspace, timestamp) => { assert(workspace === -1 && timestamp === 12345, 'Fresh server time required for asynchronous D-Bus activation'); order.push('activate'); },
    request_quit: () => { order.push('quit'); quit++; return true; }};
const service = Object.create(Service.prototype);
const headless = {get_id: () => 'org.example.Player.desktop', get_pids: () => [],
    get_windows: () => []};
let headlessQuit = 0;
service._sessionApplications = {
    getRunning: () => [app, headless],
    getPids: target => target === app ? [52, 53] : target === headless ? [71] : [],
    isRegistered: target => target === headless,
    requestQuit: async target => {
        if (target === app) {
            order.push('quit'); quit++; return true;
        }
        if (target === headless) {
            headlessQuit++; return true;
        }
        return false;
    },
};
let deny = false;
service._monitorSenderChecker = {checkInvocation: async () => { if (deny) throw Error('denied'); }};
function invocation() { return {return_value(v) { this.result = v; }, return_gerror(e) { this.error = e; }}; }
let call = invocation();
await service.GetMonitorApplicationsAsync([], call);
assert(call.result.type === '(a{sau})', 'Only app/PID schema');
assert(call.result.value[0]['org.example.Editor.desktop'].join() === '52,53', 'Actual Shell PIDs');
assert(call.result.value[0]['org.example.Player.desktop'].join() === '71',
    'Registered headless application PID');
for (const pids of [[], [1], [52]]) {
    call = invocation(); await service.RequestMonitorQuitAsync([app.get_id(), pids], call);
    assert(call.result.value[0] === false && quit === 0, 'Stale or incomplete selection must not quit');
}
call = invocation(); await service.RequestMonitorQuitAsync(['missing.desktop', [52,53]], call);
assert(!call.result.value[0] && quit === 0, 'Missing app must not quit');
call = invocation(); await service.RequestMonitorQuitAsync([app.get_id(), [52,53,54]], call);
assert(!call.result.value[0] && quit === 0, 'Changed process identity must be rejected');
call = invocation(); await service.RequestMonitorQuitAsync([app.get_id(), [52,53]], call);
assert(call.result.value[0] && quit === 1, 'Graceful request accepted for exact identity');
assert(order.join() === 'activate,quit', 'Activate only validated existing app before graceful quit');
call = invocation(); await service.RequestMonitorQuitAsync([headless.get_id(), [71]], call);
assert(call.result.value[0] && headlessQuit === 1,
    'Registered headless application receives graceful quit without activation');
call = invocation(); await service.RequestMonitorQuitAsync([headless.get_id(), [71, 72]], call);
assert(!call.result.value[0] && headlessQuit === 1,
    'An inherited child PID cannot broaden registered application identity');
deny = true;
for (const method of ['GetMonitorApplicationsAsync', 'RequestMonitorQuitAsync']) {
    call = invocation(); await service[method]([app.get_id(), [52,53]], call);
    assert(call.error && !call.result && quit === 1, 'Denied caller has no side effects');
}
assert(order.join() === 'activate,quit', 'Denied calls never activate an app');
assert(source.includes("new DBusSenderChecker(['io.luma.Monitor'])"), 'Dedicated Monitor name checker');
assert(!source.slice(source.indexOf('const APP_ALLOWLIST'), source.indexOf('const INTROSPECT')).includes('io.luma.Monitor'), 'Portal permissions unchanged');
print('PASS Monitor shell identities, stale/unknown selection, graceful quit, denied calls');
