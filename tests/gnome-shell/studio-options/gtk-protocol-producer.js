// SPDX-License-Identifier: GPL-2.0-or-later
// Disposable public GNotification producer. Private bus and XDG dirs required.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

if (!ARGV[0]) throw Error('usage: gjs -m gtk-protocol-producer.js STATUS_JSON');
const appId = 'org.projectluma.ShellWireGTK';
const state = {status: 'starting', activations: 0, actions: [], replies: []};
const file = Gio.File.new_for_path(ARGV[0]);
function save() {
    file.replace_contents(new TextEncoder().encode(JSON.stringify(state)), null, false,
        Gio.FileCreateFlags.REPLACE_DESTINATION, null);
}
const app = new Gio.Application({application_id: appId,
    flags: Gio.ApplicationFlags.IS_SERVICE});
for (const [name, type, callback] of [
    ['open', 's', target => { state.activations++; state.openTarget = target; }],
    ['update', 's', target => state.actions.push(target)],
    ['notification-reply', '(ss)', target => state.replies.push(target)],
]) {
    const action = new Gio.SimpleAction({name, parameter_type: new GLib.VariantType(type)});
    action.connect('activate', (_action, parameter) => { callback(parameter.deepUnpack()); save(); });
    app.add_action(action);
}
app.connect('activate', () => { state.unexpectedHomeActivation = true; save(); });
save();
try {
    if (!app.register(null) || app.get_is_remote()) throw Error('disposable application name not owned');
    app.hold();
    function publish(id, title, reply = false) {
        const notification = new Gio.Notification();
        notification.set_title(title);
        notification.set_body('Disposable GTK fixture body');
        notification.set_icon(new Gio.ThemedIcon({name: 'dialog-information'}));
        // Introspection exposes the Variant-taking C functions under these
        // names; the C varargs entry points are not available in GJS.
        notification.set_default_action_and_target('app.open', new GLib.Variant('s', id));
        if (reply)
            notification.add_button_with_target('Reply', 'app.notification-reply',
                new GLib.Variant('(ss)', ['fixture-conversation', '']));
        else
            notification.add_button_with_target('Update', 'app.update', new GLib.Variant('s', id));
        app.send_notification(id, notification);
    }
    publish('replacement', 'GTK Before');
    publish('activation', 'GTK Activation');
    publish('action', 'GTK Action');
    publish('reply', 'GTK Reply', true);
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, 300, () => {
        publish('replacement', 'GTK Replacement');
        state.status = 'published'; save();
        return GLib.SOURCE_REMOVE;
    });
    new GLib.MainLoop(null, false).run();
} catch (error) {
    state.status = 'error'; state.error = error.message; save();
    throw error;
}
