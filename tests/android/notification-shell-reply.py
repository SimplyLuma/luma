#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run the effective Shell reply method with explicit D-Bus dependency fixtures.

Pass a prepared, fully patched GNOME Shell source tree. This tests production
JavaScript admission/routing, not the GJS runtime, renderer or producer attestation.
"""
import pathlib
import subprocess
import sys

root = pathlib.Path(sys.argv[1])
source = (root / 'js/ui/notificationDaemon.js').read_text()
for name in ('notificationDaemon.js', 'messageList.js'):
    subprocess.run(['node', '--input-type=module', '--check'],
                   input=(root / 'js/ui' / name).read_text(), text=True,
                   check=True, timeout=20)
method = source[source.index('    _createInlineReply('):source.index('    CloseNotification(')]
constants = '\n'.join(line for line in source.splitlines()
                      if line.startswith('const ') and any(name in line for name in
                          ('MESSAGES_REPLY_', 'ANDROID_REPLY_', 'INLINE_REPLY_TIMEOUT_MS')))
script = r"""
const assert = require('node:assert/strict');
const calls = [];
const Gio = {DBusCallFlags: {NONE: 0}, DBus: {session: {call: async (...args) => {
    calls.push(args); return {deepUnpack: () => ['receipt', 'sent']};
}}}};
const GLib = {Variant: class {constructor(type, value) {this.type = type; this.value = value;}},
    VariantType: class {constructor(value) {this.value = value;}}};
CONSTANTS
const object = {METHOD};
const token = 'a'.repeat(32);
const source = id => ({app: {get_id: () => id}});
const hints = path => ({'x-luma-inline-reply-path': path, 'x-luma-inline-reply-token': token});
async function run() {
    for (const [id, path, iface] of [
        ['org.projectluma.Messages.desktop', MESSAGES_REPLY_PATH, MESSAGES_REPLY_IFACE],
        ['waydroid.org.example.Fixture.desktop', ANDROID_REPLY_PATH, ANDROID_REPLY_IFACE],
    ]) {
        const reply = object._createInlineReply(source(id), 17, ':1.42', hints(path));
        assert(reply); assert(Object.isFrozen(reply));
        assert.deepEqual(await reply.send('fixture text', 'request'), ['receipt', 'sent']);
        const call = calls.at(-1);
        assert.equal(call[0], ':1.42'); assert.equal(call[1], path); assert.equal(call[2], iface);
        assert.equal(call[3], 'Reply'); assert.equal(call[4].type, '(usss)');
        assert.deepEqual(call[4].value, [17, token, 'request', 'fixture text']);
        assert.equal(call[7], INLINE_REPLY_TIMEOUT_MS);
    }
    for (const [id, path, sender, badToken] of [
        ['waydroid.org.example.Fixture.desktop', MESSAGES_REPLY_PATH, ':1.42'],
        ['org.projectluma.Messages.desktop', ANDROID_REPLY_PATH, ':1.42'],
        ['org.example.Fixture.desktop', ANDROID_REPLY_PATH, ':1.42'],
        ['waydroid.org.example.Fixture.desktop', '/arbitrary/object', ':1.42'],
        ['waydroid.org.example.Fixture.desktop', ANDROID_REPLY_PATH, 'org.example.Service'],
        ['waydroid.org.example.Fixture.desktop', ANDROID_REPLY_PATH, null],
        ['waydroid.org.example.Fixture.desktop', ANDROID_REPLY_PATH, ':1.42', 'invalid'],
        ['waydroid...desktop', ANDROID_REPLY_PATH, ':1.42'],
        [null, ANDROID_REPLY_PATH, ':1.42'],
    ]) {
        const h = hints(path); if (badToken) h['x-luma-inline-reply-token'] = badToken;
        assert.equal(object._createInlineReply(source(id), 17, sender, h), null);
    }
    console.log('PASS effective Shell reply method: both routes, fixed endpoints, tokens and caller shape');
}
run().catch(error => {console.error(error); process.exitCode = 1;});
"""
subprocess.run(['node', '-e', script.replace('CONSTANTS', constants).replace('METHOD', method)],
               check=True, timeout=20)
assert "'x-luma-android-inline-reply-v1'" in source
labels = (root / 'js/ui/messageList.js').read_text()
assert "_('Open app')" in labels and "_('Open app to check delivery')" in labels
