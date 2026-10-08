// SPDX-License-Identifier: GPL-2.0-or-later
// Luma Seal: the conversation coordinator (js/ui/lumaSealAuth.js) and the copy
// table (js/ui/lumaSealCopy.js), with scripted polkit sessions that behave
// like the helper does under Luma's PAM stack (pam_fprintd, then pam_unix).
// Run: gjs -m seal-conversations.js path/to/lumaSealAuth.js path/to/lumaSealCopy.js
import GLib from 'gi://GLib';
import System from 'system';

const load = path => import(GLib.filename_to_uri(GLib.canonicalize_filename(path, GLib.get_current_dir()), null));
const Auth = await load(ARGV[0]);
const Copy = await load(ARGV[1]);

let failures = 0;
let checks = 0;
const check = (name, ok, detail = '') => {
    checks++;
    if (!ok) {
        failures++;
        printerr(`FAIL ${name} ${detail}`);
    }
};

// A polkit session as the helper drives it. The script decides what PAM does.
class FakeSession {
    constructor(world) {
        this.world = world;
        this.handlers = new Map();
        this.nextId = 1;
        this.responses = [];
        this.cancelled = false;
        this.id = world.sessions.length + 1;
        world.sessions.push(this);
    }

    connect(signal, handler) {
        const id = this.nextId++;
        this.handlers.set(id, [signal, handler]);
        return id;
    }

    disconnect(id) {
        this.handlers.delete(id);
    }

    emit(signal, ...args) {
        for (const [name, handler] of [...this.handlers.values()]) {
            if (name === signal)
                handler(this, ...args);
        }
    }

    initiate() {
        this.world.onInitiate(this);
    }

    response(text) {
        this.responses.push(text);
        this.world.onResponse(this, text);
    }

    cancel() {
        this.cancelled = true;
        // PolkitAgent.Session emits completed(false) when cancelled.
        this.emit('completed', false);
        this.world.onCancel(this);
    }
}

// The reader: one claim at a time, like fprintd.
function makeWorld({reader = true, password = 'luma', autoAsk = true} = {}) {
    const world = {
        sessions: [],
        events: [],
        holder: null,
        onInitiate(session) {
            if (reader && world.holder === null) {
                world.holder = session;
                session.emit('show-info', 'Place your right index finger on the fingerprint reader');
            } else if (autoAsk) {
                session.emit('request', 'Password: ', false);
            }
        },
        onResponse(session, text) {
            world.pendingCheck = {session, ok: text === password};
        },
        onCancel(session) {
            if (world.holder === session)
                world.holder = null;
        },
        finishCheck() {
            const {session, ok} = world.pendingCheck;
            world.pendingCheck = null;
            session.emit('completed', ok);
        },
        timeout(session) {
            session.emit('show-info', 'Verification timed out');
            world.holder = null;
            session.emit('request', 'Password: ', false);
        },
        miss(session) {
            session.emit('show-error', 'Failed to match fingerprint');
        },
        match(session) {
            world.holder = null;
            session.emit('completed', true);
        },
    };
    world.conversations = new Auth.Conversations({
        createSession: () => new FakeSession(world),
        onEvent: (name, data) => world.events.push([name, data]),
        now: () => 0,
    });
    return world;
}
const names = world => world.events.map(([n]) => n);

// 1. A password typed while the reader listens is accepted without waiting
//    for the reader and without retyping.
{
    const w = makeWorld();
    w.conversations.start();
    check('reader listens first', w.conversations.fingerprint === 'listening' && w.sessions.length === 1);
    w.conversations.submit('luma');
    check('a second session starts beside the reader', w.sessions.length === 2);
    check('the typed password reaches the second session once', w.sessions[1].responses.length === 1 && w.sessions[1].responses[0] === 'luma');
    check('the reader session is not given the password', w.sessions[0].responses.length === 0);
    w.finishCheck();
    check('success by password', names(w).includes('success') && w.events.find(([n]) => n === 'success')[1].method === 'password');
    check('the reader session is cancelled after success', w.sessions[0].cancelled);
    check('the reader is released', w.holder === null);
    check('settled', w.conversations.settled);
}

// 2. A fingerprint error arriving while the password is accepted can neither
//    block nor undo the success (0085).
{
    const w = makeWorld();
    w.conversations.start();
    w.conversations.submit('luma');
    // pam_fprintd reports a no-match on its way out just as pam_unix succeeds.
    w.finishCheck();
    const before = w.events.length;
    w.miss(w.sessions[0]);
    w.sessions[0].emit('completed', false);
    check('success is emitted exactly once', names(w).filter(n => n === 'success').length === 1);
    check('nothing from the reader session reaches the prompt after success', w.events.length === before);
    check('no failure after success', !names(w).includes('failed') && !names(w).includes('password-rejected'));
}

// 3. A late fingerprint error queued before the password completes still
//    does not stop the password from being accepted.
{
    const w = makeWorld();
    w.conversations.start();
    w.conversations.submit('luma');
    w.miss(w.sessions[0]);
    w.finishCheck();
    check('miss then success still succeeds', names(w).includes('fingerprint-miss') && names(w).at(-1) === 'success');
}

// 4. A touch with the field empty completes the request.
{
    const w = makeWorld();
    w.conversations.start();
    w.match(w.sessions[0]);
    const success = w.events.find(([n]) => n === 'success');
    check('fingerprint alone succeeds', success && success[1].method === 'fingerprint');
    check('only one session was needed', w.sessions.length === 1);
}

// 5. A touch while a password is being checked wins, and the password
//    session is cancelled.
{
    const w = makeWorld();
    w.conversations.start();
    w.conversations.submit('typed-partly');
    w.match(w.sessions[0]);
    check('fingerprint during a check succeeds', w.events.find(([n]) => n === 'success')?.[1].method === 'fingerprint');
    check('the password session is cancelled', w.sessions[1].cancelled);
    const before = w.events.length;
    w.sessions[1].emit('completed', false);
    check('its late result is ignored', w.events.length === before);
}

// 6. A wrong password is rejected, the reader keeps listening, and the next
//    attempt works without restarting the reader.
{
    const w = makeWorld();
    w.conversations.start();
    w.conversations.submit('wrong');
    w.finishCheck();
    check('rejected', names(w).includes('password-rejected'));
    check('the reader session still listens', !w.sessions[0].cancelled && w.conversations.fingerprint === 'listening');
    check('a fresh password session is ready', w.sessions.length === 3 && !w.sessions[2].cancelled);
    check('submit is accepted again', w.conversations.submit('luma'));
    check('handed to the ready session', w.sessions[2].responses[0] === 'luma');
    w.finishCheck();
    check('then success', names(w).at(-1) === 'success');
}

// 7. No reader: password only, nothing about fingerprints.
{
    const w = makeWorld({reader: false});
    w.conversations.start();
    check('asks for the password first', w.sessions.length === 1 && w.conversations.fingerprint === 'unknown');
    w.conversations.submit('luma');
    check('answered in the same session', w.sessions[0].responses[0] === 'luma' && w.sessions.length === 1);
    w.finishCheck();
    check('no fingerprint events', !names(w).some(n => n.startsWith('fingerprint')));
    check('success', names(w).includes('success'));
}

// 8. The reader times out: the session asks for the password and a new
//    conversation arms the reader again.
{
    const w = makeWorld();
    w.conversations.start();
    w.timeout(w.sessions[0]);
    check('reader armed again', w.sessions.length === 2 && w.holder === w.sessions[1]);
    check('listening again', w.conversations.fingerprint === 'listening');
    w.conversations.submit('luma');
    check('the waiting session takes the password', w.sessions[0].responses[0] === 'luma' && w.sessions.length === 2);
}

// 9. Submitted just as the reader times out: the password goes to whichever
//    session asks first, exactly once.
{
    const w = makeWorld({autoAsk: false});
    w.conversations.start();
    w.conversations.submit('luma');
    // The second session has not spoken yet when the first one times out.
    w.timeout(w.sessions[0]);
    check('the first asking session gets it', w.sessions[0].responses.join() === 'luma');
    w.sessions[1].emit('request', 'Password: ', false);
    check('the second is not given it again', w.sessions[1].responses.length === 0);
    check('and is dropped', w.sessions[1].cancelled);
}

// 10. Three misses: pam_fprintd gives up, the password still works.
{
    const w = makeWorld();
    w.conversations.start();
    for (let i = 0; i < 3; i++)
        w.miss(w.sessions[0]);
    w.holder = null;
    w.sessions[0].emit('request', 'Password: ', false);
    check('exhausted after misses', w.conversations.fingerprint === 'exhausted' && names(w).includes('fingerprint-exhausted'));
    check('not re-armed after misses', w.sessions.length === 1);
    w.conversations.submit('luma');
    check('password answered by the same session', w.sessions[0].responses[0] === 'luma');
}

// 11. A helper that keeps failing stops being restarted.
{
    const w = makeWorld({reader: false, autoAsk: false});
    w.conversations.start();
    for (let i = 0; i < 8 && !w.conversations.settled; i++)
        w.sessions.at(-1).emit('completed', false);
    check('gives up instead of spinning', names(w).includes('failed') && w.sessions.length <= 6);
}

// 12. Cancel drops everything and ignores late signals.
{
    const w = makeWorld();
    w.conversations.start();
    w.conversations.submit('luma');
    w.conversations.cancel();
    check('all sessions cancelled', w.sessions.every(s => s.cancelled));
    const before = w.events.length;
    w.sessions[1].emit('completed', true);
    check('late success after cancel is ignored', w.events.length === before);
}

// ── Copy ──────────────────────────────────────────────────────────────────
const fakeDevice = () => ({size: 31_914_983_424, partition: false});
{
    const d = Copy.describe({
        actionId: 'org.freedesktop.udisks2.open-device',
        message: 'Authentication is required to open USB DISK 3.0 (/dev/sda) for writing',
        appName: 'Imager', executable: '/usr/bin/luma-imager', device: fakeDevice,
    });
    check('udisks write title', d.title === 'Imager wants to write to “USB DISK 3.0”', d.title);
    check('udisks write body', d.body === 'Everything on this 32 GB drive will be erased and replaced. Writing to a disk needs an administrator.', d.body);
    check('udisks facts', JSON.stringify(d.facts) === JSON.stringify([['Action', 'org.freedesktop.udisks2.open-device'], ['Device', '/dev/sda · USB DISK 3.0'], ['Requested by', '/usr/bin/luma-imager · Imager']]), JSON.stringify(d.facts));
}
{
    const d = Copy.describe({actionId: 'org.freedesktop.Flatpak.app-install', message: 'Authentication is required to install software', appName: 'Depot', executable: '/usr/bin/luma-depot'});
    check('flatpak title', d.title === 'Depot wants to install an app for everyone on this computer', d.title);
}
{
    const d = Copy.describe({actionId: 'org.freedesktop.timedate1.set-time', message: 'Authentication is required to set the system time.', appName: 'Settings'});
    check('time title', d.title === 'Settings wants to change the date and time', d.title);
    check('time body', d.body === 'The clock affects every account, and the certificates apps use to connect securely.');
}
{
    const d = Copy.describe({
        actionId: 'org.freedesktop.policykit.exec',
        message: 'Authentication is needed to run `/usr/bin/rpm-ostree kargs --append=quiet\' as the super user',
        appName: 'Terminal', executable: '/usr/bin/bash', command: 'rpm-ostree kargs --append=quiet',
    });
    check('pkexec title', d.title === 'Terminal wants to run a command as administrator', d.title);
    check('pkexec command fact', d.facts.some(([k, v]) => k === 'Command' && v === 'rpm-ostree kargs --append=quiet'));
    const other = Copy.describe({
        actionId: 'org.freedesktop.policykit.exec',
        message: 'Authentication is needed to run `x\' as the super user\' as user bob',
        appName: 'Terminal',
    });
    check('pkexec as another user falls back', other.template === 'fallback', other.template);
}
{
    const d = Copy.describe({actionId: 'org.example.frobnicate', message: 'Authentication is required to frobnicate.', appName: null, executable: '/usr/libexec/frob'});
    check('unknown action, unknown app', d.title === 'An app wants administrator access' && d.body === 'Authentication is required to frobnicate.', d.title);
    const known = Copy.describe({actionId: 'org.example.frobnicate', message: 'x', appName: 'Files'});
    check('unknown action, known app', known.title === 'Files wants administrator access', known.title);
    check('app name with %s is kept as it is', Copy.describe({actionId: 'a.b', message: 'x', appName: '%s App'}).title === '%s App wants administrator access');
}
check('cgroup scope', Copy.appFromCgroup('0::/user.slice/user-1000.slice/user@1000.service/app.slice/app-gnome-org.gnome.Settings-3021.scope\n')?.id === 'org.gnome.Settings');
check('cgroup flatpak', JSON.stringify(Copy.appFromCgroup('0::/user.slice/user-1000.slice/user@1000.service/app.slice/app-flatpak-dev.lattice.Lattice-4121.scope')) === JSON.stringify({id: 'dev.lattice.Lattice', flatpak: true}));
check('cgroup dbus service', Copy.appFromCgroup('0::/user.slice/user-1000.slice/user@1000.service/app.slice/dbus-:1.2-org.gnome.Settings@0.service')?.id === 'org.gnome.Settings');
check('cgroup escaped dash', Copy.appFromCgroup('0::/user.slice/user-1000.slice/user@1000.service/app.slice/app-gnome-io.luma\\x2dimager-77.scope')?.id === 'io.luma-imager');
check('cgroup vte scope is not an app', Copy.appFromCgroup('0::/user.slice/user-1000.slice/user@1000.service/app.slice/vte-spawn-2b2a.scope') === null);
check('cgroup session is not an app', Copy.appFromCgroup('0::/user.slice/user-1000.slice/session-2.scope') === null);
check('size 32 GB', Copy.formatSize(31_914_983_424) === '32 GB');
check('size 7.8 GB', Copy.formatSize(7_800_000_000) === '7.8 GB');
check('pkexec argv', Copy.pkexecCommand(['pkexec', '--keep-cwd', 'rpm-ostree', 'kargs', '--append=quiet']) === 'rpm-ostree kargs --append=quiet');
check('pkexec argv with --user', Copy.pkexecCommand(['/usr/bin/pkexec', '--user', 'bob', 'id']) === 'id');
check('not pkexec', Copy.pkexecCommand(['bash', '-c', 'x']) === null);

if (failures) {
    printerr(`seal-conversations: ${failures}/${checks} FAILED`);
    System.exit(1);
}
print(`seal-conversations: ${checks} checks PASS`);
