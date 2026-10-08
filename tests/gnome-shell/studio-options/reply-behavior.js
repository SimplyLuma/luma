// SPDX-License-Identifier: GPL-2.0-or-later
// Production reply state transitions with deferred peers and disposable actors.
const Gio = imports.gi.Gio;
const RuntimeGLib = imports.gi.GLib;
if (!ARGV[0]) throw Error('usage: gjs reply-behavior.js APPLIED_SHELL_SOURCE');
const [, bytes] = Gio.File.new_for_path(`${ARGV[0]}/js/ui/messageList.js`).load_contents(null);
const source = new TextDecoder().decode(bytes);
Reflect.parse(source, {target: 'module'});
const begin = source.indexOf('    async _submitInlineReply()');
const uncertain = source.indexOf('    _setReplyUncertain() {', begin);
const end = source.indexOf('\n});', uncertain);
if ([begin, uncertain, end].some(value => value < 0)) throw Error('production reply methods missing');
const methods = `${source.slice(begin, uncertain)},${source.slice(uncertain, end)}`;
let sequence = 0;
const timers = [];
const GLib = {PRIORITY_DEFAULT: 0, SOURCE_REMOVE: false,
    uuid_string_random: () => `request-${++sequence}`,
    timeout_add(_priority, delay, callback) { timers.push({delay, callback}); return timers.length; }};
const St = {Label: class { constructor(properties) { Object.assign(this, properties); } }};
const MessageTray = {NotificationLifecycleState: {ACTED: 'acted'}, NotificationDestroyedReason: {DISMISSED: 2}};
function makeProduction(methodText) {
    return new Function('Gio', 'GLib', 'St', 'MessageTray', '_', `return ({${methodText}});`)
        ({DBusError: {get_remote_error: error => error.remoteName}}, GLib, St, MessageTray, value => value);
}
let production = makeProduction(methods);
function assert(ok, message) { if (!ok) throw Error(message); }
function deferred() {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return {promise, resolve, reject};
}
function editor(text) { return {reactive: true, get_text: () => text}; }
function button() { return {reactive: true, label: 'Send', remove_style_class_name() {}}; }
function card(send, text = 'Fixture reply') {
    const actor = {_replyEntry: editor(text), _replyButton: button(), _inlineReply: {send},
        _inlineReplyRow: {children: [], destroy_all_children() { this.children = []; }, add_child(child) { this.children.push(child); }},
        notification: {source: {open() {}}, destroy(reason) { this.reason = reason; }},
        _setReplyUncertain: production._setReplyUncertain};
    return actor;
}
async function retryTest() {
    timers.length = 0;
    const requests = []; let ready = false;
    const actor = card(async (text, request) => {
        requests.push([text, request]);
        if (!ready) throw {remoteName: 'org.projectluma.Messages.Error.NotReady'};
        return ['fixture', 'sent'];
    });
    await production._submitInlineReply.call(actor);
    assert(actor._replyEntry.get_text() === 'Fixture reply' && actor._replyEntry.reactive &&
        actor._replyButton.label === 'Try again' && actor._replyButton.accessible_name === 'Try again',
        'NotReady must retain editable text and an accessible retry');
    ready = true; await production._submitInlineReply.call(actor);
    assert(requests.length === 2 && requests[0][1] === requests[1][1], 'retry must retain its request ID');
    assert(actor._replyEntry === null && actor._inlineReplyRow.children[0]?.text === 'Sent', 'acknowledgement must show Sent');
    assert(timers.length === 1 && timers[0].delay === 1000, 'Sent must remain for its declared interval');
    timers[0].callback();
    assert(actor.notification.reason === 2 && actor.notification.lifecycleState === 'acted', 'acknowledged reply must dismiss with the public reason');
}
async function replacementTest() {
    for (const outcome of ['sent', 'NotReady']) {
        timers.length = 0;
        const oldPeer = deferred(), newPeer = deferred();
        const actor = card(() => oldPeer.promise);
        const oldRequest = production._submitInlineReply.call(actor);
        // The replacement boundary supplies a new editor and capability.
        actor._inlineReply = {send: () => newPeer.promise}; actor._replyPending = false;
        actor._replyRequestId = actor._replyPayload = null;
        actor._replyEntry = editor('New conversation'); actor._replyButton = button();
        const newRequest = production._submitInlineReply.call(actor);
        if (outcome === 'sent') oldPeer.resolve(['fixture', 'sent']);
        else oldPeer.reject({remoteName: 'org.projectluma.Messages.Error.NotReady'});
        await oldRequest;
        assert(actor._replyPending && !actor._replyEntry.reactive && !actor._replyButton.reactive &&
            actor._replyButton.label === 'Send' && timers.length === 0,
            'old completion must not alter or unlock the new pending editor');
        newPeer.resolve(['fixture', 'sent']); await newRequest;
        assert(actor._inlineReplyRow.children[0]?.text === 'Sent', 'the new generation must still complete');
    }
}
async function editedRetryTest() {
    const requests = [];
    const actor = card(async (text, request) => {
        requests.push([text, request]);
        throw {remoteName: 'org.projectluma.Messages.Error.NotReady'};
    });
    await production._submitInlineReply.call(actor);
    actor._replyEntry = editor('Edited reply');
    await production._submitInlineReply.call(actor);
    assert(requests.length === 2 && requests[1][0] === 'Edited reply' &&
        requests[0][1] !== requests[1][1],
        'an edited retry must send the edited text with a new request ID');
    await production._submitInlineReply.call(actor);
    assert(requests[2][1] === requests[1][1], 'an unchanged edited retry must retain its new ID');
}
async function destroyedReplyTest() {
    timers.length = 0;
    const peer = deferred();
    const actor = card(() => peer.promise);
    const request = production._submitInlineReply.call(actor);
    actor.notification = null;
    actor._inlineReply = null;
    actor._replyEntry = actor._replyButton = null;
    peer.resolve(['fixture', 'sent']);
    await request;
    assert(timers.length === 0, 'destroyed notification must ignore late reply acknowledgement');
}
async function uncertainTest() {
    const actor = card(async () => { throw {remoteName: 'org.freedesktop.DBus.Error.NoReply'}; });
    await production._submitInlineReply.call(actor);
    assert(actor._replyUncertain && !actor._replyEntry.reactive && actor._replyButton.label === 'Open Messages',
        'uncertain delivery must preserve text and reconcile instead of blindly retrying');
    let sends = 0, opens = 0;
    actor._inlineReply.send = async () => { sends++; };
    actor.notification.source.open = () => { opens++; };
    await production._submitInlineReply.call(actor);
    assert(sends === 0 && opens === 1, 'uncertain follow-up must open the producer without sending again');
}
async function inputLimitTest() {
    for (const text of ['', '   ', 'é'.repeat(2049)]) {
        let sends = 0;
        const actor = card(async () => { sends++; }, text);
        await production._submitInlineReply.call(actor);
        assert(sends === 0 && !actor._replyPending && actor._replyEntry.reactive, 'empty or over-4096-byte input must not dispatch');
    }
}
async function selfTest() {
    const fault = methods.replace(
        /if \(this\._replyPayload !== text\) \{\s*this\._replyRequestId = GLib\.uuid_string_random\(\);\s*this\._replyPayload = text;\s*\}/,
        'this._replyRequestId ??= GLib.uuid_string_random(); this._replyPayload ??= text;');
    assert(fault !== methods, 'stale-payload fault injection must find the production boundary');
    production = makeProduction(fault);
    let detected = false;
    try {
        await editedRetryTest();
    } catch (error) {
        detected = error.message.includes('edited retry must send');
    } finally {
        production = makeProduction(methods);
    }
    assert(detected, 'reply checker must reject stale text after an edited retry');
    print('PASS self-test: stale reply payload detected');
}
const loop = new RuntimeGLib.MainLoop(null, false);
(async () => {
    for (const test of [retryTest, replacementTest, editedRetryTest, destroyedReplyTest, uncertainTest, inputLimitTest]) {
        await test(); print(`PASS ${test.name}`);
    }
    if (ARGV.includes('--self-test'))
        await selfTest();
})().then(() => loop.quit()).catch(error => { printerr(error.stack ?? error); imports.system.exit(1); });
loop.run();
