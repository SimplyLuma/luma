// SPDX-License-Identifier: GPL-2.0-or-later
// Execute production methods with a deterministic clock. This does not claim
// actor rendering, real Bluetooth pairing, or NetworkManager integration.
const Gio = imports.gi.Gio;
const root = ARGV.find(a => a !== '--self-test');
if (!root) throw Error('usage: gjs behavior.js APPLIED_SHELL_SOURCE [--self-test]');
function read(path) {
    const [, data] = Gio.File.new_for_path(`${root}/${path}`).load_contents(null);
    return new TextDecoder().decode(data);
}
function assert(ok, message) { if (!ok) throw Error(message); }
const names = ['lumaNotificationBeacon', 'messageTray', 'messageList', 'notificationDaemon',
    'lumaSurfaceMaterials', 'quickSettings', 'panel', 'status/system', 'status/doNotDisturb',
    'status/powerProfiles', 'status/bluetooth', 'status/network', 'status/volume'];
for (const name of names) Reflect.parse(read(`js/ui/${name}.js`), {target: 'module'});
let now = 0, sequence = 0;
const pending = new Map();
const GLib = {PRIORITY_DEFAULT: 0, SOURCE_REMOVE: false,
    get_monotonic_time: () => now * 1000,
    timeout_add: (_p, delay, fn) => { const id = ++sequence; pending.set(id, {at: now + delay, fn}); return id; },
    source_remove: id => pending.delete(id)};
function advance(ms) {
    const end = now + ms;
    while (true) {
        const next = [...pending].sort((a, b) => a[1].at - b[1].at)[0];
        if (!next || next[1].at > end) break;
        now = next[1].at; pending.delete(next[0]); next[1].fn();
    }
    now = end;
}
const life = {ACTED: 'acted', DISMISSED: 'dismissed', EXPIRED: 'expired', WAITING: 'waiting', ARRIVING: 'arriving'};
const MessageTray = {NotificationLifecycleState: life, NotificationDestroyedReason: {DISMISSED: 2}, Urgency: {NORMAL: 1, CRITICAL: 2}};
const Main = {messageTray: {getSources: () => []}};
const GObject = {registerClass: c => c};
const St = {Bin: class {}, Widget: class {}};
let moduleText = read('js/ui/lumaNotificationBeacon.js').replace(/^import .*;\n/gm, '').replace(/^export /gm, '');
const {Lip, records} = new Function('GObject', 'St', 'MessageTray', 'Main', 'GLib',
    moduleText + '\nreturn {Lip: NotificationLip, records};')(GObject, St, MessageTray, Main, GLib);
function reset() { now = 0; sequence = 0; pending.clear(); }
function lip() {
    const actor = Object.create(Lip.prototype);
    Object.assign(actor, {_state: 'idle', _current: null, _timer: 0, _remaining: 6500,
        _settings: {get_boolean: () => true}, _queueSync() {}, _render() {},
        _createCard: () => ({}), _setContent() {}, _engaged: () => false, _buzz() { actor.buzzed = true; }});
    return actor;
}
function notification(urgency = 1) {
    return {source: {policy: {enable: true, showBanners: true}}, urgency, lifecycleState: 'queued', playSound() { this.sounds = (this.sounds ?? 0) + 1; }};
}
function timerTest() {
    reset(); const a = lip(), n = notification(); a.receive(n);
    assert(n.lifecycleState === life.ARRIVING && n.sounds === 1, 'ordinary arrival must present and sound once');
    advance(6499); assert(a._current === n, 'must not retract before 6500 ms');
    advance(1); assert(a._current === null && n.lifecycleState === life.WAITING, '6500 ms must retract without destroying record');
    advance(479); assert(!a.buzzed, 'buzz must wait 480 ms'); advance(1); assert(a.buzzed, 'buzz must follow retraction');
}
function pauseTest() {
    reset(); const a = lip(), n = notification(); a.receive(n); advance(2000); a._pause();
    assert(a._remaining === 4500 && !a._timer, 'pause must preserve unspent time');
    advance(10000); assert(a._current === n, 'engagement must prevent retraction');
    a._arm(); advance(4499); assert(a._current === n, 'resume must not lose time');
    advance(1); assert(a._current === null, 'resume must expire remaining time');
}
function urgencyTest() {
    reset(); const a = lip(), urgent = notification(2), ordinary = notification();
    a.receive(urgent); a.receive(ordinary); advance(60000);
    assert(a._current === urgent && !a._timer, 'urgent card must hold surface without timer');
    assert(ordinary.lifecycleState === life.WAITING && !ordinary.sounds, 'ordinary arrival must wait behind urgent');
}
function quietTest() {
    reset(); for (const mode of ['dnd', 'policy', 'quiet', 'tray']) {
        const a = lip(), n = notification();
        if (mode === 'dnd') a._settings.get_boolean = () => false;
        if (mode === 'policy') n.source.policy.showBanners = false;
        if (mode === 'tray') a._state = 'tray';
        a.receive(n, mode === 'quiet');
        assert(n.lifecycleState === life.WAITING && !n.sounds && !a._current && !a._timer, `${mode} must not present or sound`);
    }
}
function recordTest() {
    const good = notification(), acted = notification(), disabled = notification();
    acted.lifecycleState = life.ACTED; disabled.source.policy.enable = false;
    Main.messageTray.getSources = () => [{policy: {enable: true}, notifications: [good, acted]},
        {policy: {enable: false}, notifications: [disabled]}];
    assert(records().length === 1 && records()[0] === good, 'collection must omit disabled and acted records');
}
const bt = read('js/ui/status/bluetooth.js');
const pairText = bt.slice(bt.indexOf('class PairingAgent {'), bt.indexOf('const STATE_CHANGE_FAILED_TIMEOUT_MS'));
const Pair = new Function(pairText + '\nreturn PairingAgent;')();
const scanStart = bt.indexOf('    _scanNearby() {');
const scanFinish = bt.indexOf('    _finishScan() {', scanStart);
const scanStop = bt.indexOf('    _stopNearby() {', scanFinish);
const scanEnd = bt.indexOf('    _syncNearby() {', scanStop);
assert([scanStart, scanFinish, scanStop, scanEnd].every(index => index >= 0), 'discovery methods must exist');
const scanMethods = [bt.slice(scanStart, scanFinish), bt.slice(scanFinish, scanStop), bt.slice(scanStop, scanEnd)].join(',');
const scanning = new Function('GLib', '_', `return ({${scanMethods}});`)(GLib, value => value);
function discoveryOwnershipTest() {
    for (const external of [false, true]) {
        reset();
        let cancelled = 0;
        const adapter = {default_adapter_setup_mode: external};
        const actor = Object.assign({_client: {active: true, _client: adapter}, _scanId: 0,
            _nearStatus: {}, _lookAgain: {hide() {}, show() {}}, _syncNearby() {},
            _pairAgent: {cancel() { cancelled++; }}}, scanning);
        actor._scanNearby();
        assert(adapter.default_adapter_setup_mode && pending.size === 1, 'scan must start one bounded observation interval');
        actor._scanNearby();
        assert(pending.size === 1, 'repeated scan must not add a timer');
        actor._stopNearby();
        assert(adapter.default_adapter_setup_mode === external && !pending.size && cancelled === 1,
            'closing must cancel owned work while preserving external discovery');
        actor._client.active = false;
        actor._scanNearby();
        assert(!pending.size && adapter.default_adapter_setup_mode === external, 'an off radio must not start discovery');
    }
}
function securityTest() {
    const agent = Object.create(Pair.prototype); agent._path = '/device/one'; agent._ownerProxy = {g_name_owner: ':1.42'};
    for (const [path, sender, accepted] of [['/device/one', ':1.42', true], ['/device/two', ':1.42', false], ['/device/one', ':1.43', false]]) {
        let errors = 0; const inv = {get_sender: () => sender, return_dbus_error() { errors++; }};
        assert(agent._check(path, inv) === accepted && errors === (accepted ? 0 : 1), 'pairing must reject wrong sender/device');
    }
    let replies = 0; agent._pending = {return_dbus_error() { replies++; }};
    agent._rejectPending(); agent._rejectPending(); assert(replies === 1 && agent._pending === null, 'pair cancellation must answer once');
}
const daemon = read('js/ui/notificationDaemon.js');
const factoryText = daemon.slice(daemon.indexOf('    _createInlineReply(source,'), daemon.indexOf('    CloseNotification(id)'));
const factory = new Function('Gio', 'GLib', 'MESSAGES_REPLY_PATH', 'MESSAGES_REPLY_IFACE', 'INLINE_REPLY_TIMEOUT_MS',
    'return ({' + factoryText + '})._createInlineReply;')({}, {}, '/org/projectluma/Messages/Notifications',
        'org.projectluma.Messages.NotificationReply1', 30000);
function replyCapabilityTest() {
    const path = '/org/projectluma/Messages/Notifications';
    const hints = {'x-luma-inline-reply-path': path, 'x-luma-inline-reply-token': '0123456789abcdef0123456789abcdef'};
    const source = {app: {get_id: () => 'org.projectluma.Messages.desktop'}};
    const valid = factory(source, 7, ':1.99', hints);
    assert(valid && Object.isFrozen(valid) && valid.sender === ':1.99', 'reply capability must bind unique producer');
    for (const [s, owner, h] of [[source, null, hints], [source, 'org.example.Name', hints],
        [source, ':1.99', {...hints, 'x-luma-inline-reply-path': '/wrong'}],
        [source, ':1.99', {...hints, 'x-luma-inline-reply-token': 'bad'}],
        [{app: {get_id: () => 'org.example.Impostor.desktop'}}, ':1.99', hints]])
        assert(factory(s, 7, owner, h) === null, 'invalid producer/path/token/app must not receive reply capability');
}
const listSource = read('js/ui/messageList.js');
const callText = listSource.slice(listSource.indexOf('const AFFIRMATIVE_ACTIONS'),
    listSource.indexOf('export const MESSAGE_ANIMATION_TIME')).replace(/^export /gm, '');
const isCall = new Function('MessageTray', callText + '\nreturn isIncomingCallNotification;')(MessageTray);
function callClassificationTest() {
    const actions = [{id: 'accept'}, {id: 'decline'}];
    assert(isCall({urgency: 2, actions}) === true, 'incoming call action pair must receive call presentation');
    for (const n of [{urgency: 1, actions}, {urgency: 2, actions: [{id: 'accept'}]},
        {urgency: 2, actions: [{id: 'open'}]}, {urgency: 2, actions: []}, {urgency: 2}])
        assert(isCall(n) === false, 'ordinary and generic critical records must not receive ringing presentation');
}
const tests = [timerTest, pauseTest, urgencyTest, quietTest, recordTest, securityTest, discoveryOwnershipTest, replyCapabilityTest, callClassificationTest];
if (ARGV.includes('--self-test')) {
    for (const [object, name, replacement, test] of [[Lip.prototype, '_arm', () => {}, timerTest], [Pair.prototype, '_check', () => true, securityTest],
        [scanning, '_finishScan', function () { this._client._client.default_adapter_setup_mode = false; }, discoveryOwnershipTest]]) {
        const original = object[name]; object[name] = replacement; let caught = false;
        try { test(); } catch { caught = true; } finally { object[name] = original; }
        assert(caught, `self-test failed to detect broken ${name}`);
    }
    print('SELF-TEST detected missing timer, spoofed pairing acceptance and external discovery cancellation');
}
for (const test of tests) { test(); print(`PASS ${test.name}`); }
print(`PASS ${names.length} production modules parsed; ${tests.length} behavioral cases executed`);
