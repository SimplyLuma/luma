// SPDX-License-Identifier: GPL-2.0-or-later
// Disposable real D-Bus producer. The harness launches it on its private bus.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
Gio._promisify(Gio.DBusConnection.prototype, 'call', 'call_finish');
const file = Gio.File.new_for_path(ARGV[0]);
const state = {status: 'starting', actions: [], closed: [], replies: 0, rejectedReplies: 0};
const save = () => file.replace_contents(JSON.stringify(state), null, false, Gio.FileCreateFlags.REPLACE_DESTINATION, null);
const loop = new GLib.MainLoop(null, false);
const connection = Gio.DBus.session;
const token = '0123456789abcdef0123456789abcdef';
const oldToken = 'fedcba9876543210fedcba9876543210';
const xml = `<node><interface name="org.projectluma.Messages.NotificationReply1"><method name="Reply"><arg type="u" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="out"/><arg type="s" direction="out"/></method></interface></node>`;
let shellOwner;
const reply = Gio.DBusExportedObject.wrapJSObject(xml, {
    ReplyAsync([id, suppliedToken, request, text], invocation) {
        if (invocation.get_sender() !== shellOwner || id !== state.id || suppliedToken !== token || text !== 'Fixture reply' || !request) {
            invocation.return_dbus_error('org.projectluma.Messages.Error.NotReady', 'Fixture rejected invalid request'); return;
        }
        if (!state.rejectedReplies) {
            state.rejectedReplies++; state.retryRequest = request; save();
            invocation.return_dbus_error('org.projectluma.Messages.Error.NotReady', 'Fixture deliberately rejects before dispatch'); return;
        }
        if (request !== state.retryRequest) {
            invocation.return_dbus_error('org.projectluma.Messages.Error.NotReady', 'Retry changed request ID'); return;
        }
        state.replies++; save();
        invocation.return_value(new GLib.Variant('(ss)', ['fixture-producer', 'sent']));
    },
});
reply.export(connection, '/org/projectluma/Messages/Notifications');
for (const [signal, key] of [['ActionInvoked', 'actions'], ['NotificationClosed', 'closed']]) {
    connection.signal_subscribe('org.freedesktop.Notifications', 'org.freedesktop.Notifications', signal,
        '/org/freedesktop/Notifications', null, Gio.DBusSignalFlags.NONE,
        (_c, _s, _p, _i, _n, parameters) => { state[key].push(parameters.deepUnpack()); save(); });
}
(async () => {
    const owner = await connection.call('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus', 'GetNameOwner',
        new GLib.Variant('(s)', ['org.gnome.Shell']), new GLib.VariantType('(s)'), Gio.DBusCallFlags.NONE, 10000, null);
    [shellOwner] = owner.deepUnpack();
    const hints = {'desktop-entry': new GLib.Variant('s', 'org.projectluma.Messages'),
        'urgency': new GLib.Variant('y', 1), 'resident': new GLib.Variant('b', true),
        'x-luma-inline-reply-path': new GLib.Variant('s', '/org/projectluma/Messages/Notifications'),
        'x-luma-inline-reply-token': new GLib.Variant('s', token)};
    const send = async (id, title, replyToken = token) => {
        const notificationHints = {...hints, 'x-luma-inline-reply-token': new GLib.Variant('s', replyToken)};
        const result = await connection.call('org.freedesktop.Notifications', '/org/freedesktop/Notifications',
            'org.freedesktop.Notifications', 'Notify', new GLib.Variant('(susssasa{sv}i)',
                ['Luma Wire Fixture', id, 'dialog-information', title, 'Fixture body',
                    ['default', 'Open', 'update', 'Update', 'later', 'Later'], notificationHints, -1]),
            new GLib.VariantType('(u)'), Gio.DBusCallFlags.NONE, 10000, null);
        return result.deepUnpack()[0];
    };
    state.activationId = await send(0, 'Wire activation');
    state.actionId = await send(0, 'Wire action');
    state.firstId = await send(0, 'Wire fixture', oldToken);
    state.id = await send(state.firstId, 'Wire replacement');
    if (state.id !== state.firstId) throw Error('replacement changed ID');
    state.status = 'published'; save();
})().catch(e => { state.status = 'error'; state.error = e.message; save(); loop.quit(); });
save(); loop.run(); reply.unexport();
