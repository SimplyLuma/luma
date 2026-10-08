// SPDX-License-Identifier: GPL-2.0-or-later
// Seal evidence harness: drives Luma's administrator prompt in a headless
// Shell against real polkitd, the real polkit agent helper, the Fedora
// with-fingerprint PAM stack and fprintd with libfprint's virtual reader.
// Cases are chosen with SEAL_ONLY (comma separated).
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';

const OUT = GLib.getenv('SEAL_OUT');
const PASSWORD = 'luma-test';
const FINGER = 'nick-right-index';
const log = (m, o) => console.log(`[seal] ${m} ${JSON.stringify(o ?? {})}`);
const sleep = ms => new Promise(r => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
    r();
    return GLib.SOURCE_REMOVE;
}));
const now = () => GLib.get_monotonic_time() / 1000;
const r1 = v => Math.round(v * 10) / 10;
const rect = a => {
    const [x, y] = a.get_transformed_position();
    const [w, h] = a.get_transformed_size();
    return {x: r1(x), y: r1(y), w: r1(w), h: r1(h)};
};

async function waitFor(what, predicate, timeout = 8000, step = 20) {
    const start = now();
    while (now() - start < timeout) {
        const value = predicate();
        if (value)
            return {value, ms: Math.round(now() - start)};
        await sleep(step);
    }
    throw new Error(`timed out waiting for ${what}`);
}

// A process in its own systemd scope, as an app launched from the shell is.
function spawn(argv, scope) {
    const full = scope
        ? ['systemd-run', '--user', '--scope', '--quiet', `--unit=${scope}`, ...argv]
        : argv;
    const proc = Gio.Subprocess.new(full, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE);
    const done = new Promise(resolve => {
        proc.communicate_utf8_async(null, null, (p, res) => {
            let out = '';
            try {
                [, out] = p.communicate_utf8_finish(res);
            } catch (e) {
                out = String(e);
            }
            resolve({status: p.get_if_exited() ? p.get_exit_status() : -1, out: out.trim()});
        });
    });
    return {proc, done};
}

let serial = 0;
const scopeFor = id => `app-gnome-${id.replace(/-/g, '\\x2d')}-${GLib.random_int_range(1000, 99999)}${serial++}.scope`;

function request(appId, action, {message = null, details = {}} = {}) {
    const req = {action, details: {...details}};
    if (message)
        Object.assign(req.details, {'polkit.message': message.text, 'polkit.gettext_domain': message.domain});
    const scope = appId ? scopeFor(appId) : `seal-background-${serial++}.scope`;
    const {done} = spawn(['python3', '/seal/harness/requester.py', JSON.stringify(req)], scope);
    return done.then(({out}) => {
        try {
            return JSON.parse(out.split('\n').pop());
        } catch {
            return {raw: out};
        }
    });
}

async function root(command) {
    const {done} = spawn(['python3', '/seal/harness/requester.py', JSON.stringify(command)]);
    return (await done).out;
}

const reader = command => root({reader: command});

export default async function ({Main, shot}) {
    const only = (GLib.getenv('SEAL_ONLY') || '').split(',').filter(Boolean);
    const want = name => only.length === 0 || only.includes(name);
    const theme = GLib.getenv('SEAL_THEME');
    const scaleWanted = Number(GLib.getenv('SEAL_SCALE') || 1);
    const results = {theme, cases: {}};
    const save = () => GLib.file_set_contents(`${OUT}/results.json`, JSON.stringify(results, null, 1));

    if (scaleWanted !== 1) {
        const call = (method, params, sig) => new Promise((resolve, reject) => Gio.DBus.session.call(
            'org.gnome.Mutter.DisplayConfig', '/org/gnome/Mutter/DisplayConfig', 'org.gnome.Mutter.DisplayConfig',
            method, params, sig ? new GLib.VariantType(sig) : null, 0, -1, null, (c, r) => {
                try {
                    resolve(c.call_finish(r));
                } catch (e) {
                    reject(e);
                }
            }));
        const [serialNo, monitors] = (await call('GetCurrentState', null)).deepUnpack();
        const [[connector], modes] = monitors[0];
        const current = modes.find(m => m[6]?.['is-current']?.deepUnpack?.()) ?? modes[0];
        await call('ApplyMonitorsConfig', new GLib.Variant('(uua(iiduba(ssa{sv}))a{sv})',
            [serialNo, 1, [[0, 0, scaleWanted, 0, true, [[connector, current[0], {}]]]], {}]));
        await sleep(4000);
    }
    await sleep(1500);
    if (Main.actionMode === 0) {
        const dummy = new St.Widget();
        Main.uiGroup.add_child(dummy);
        Main.popModal(Main.pushModal(dummy));
        dummy.destroy();
    }

    const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor;
    results.scale = scale;
    const monitorNow = () => Main.layoutManager.primaryMonitor;
    let monitor = monitorNow();
    const seat = Clutter.get_default_backend().get_default_seat();
    const keyboard = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
    const t = () => GLib.get_monotonic_time();
    const key = async (keyval, wait = 40) => {
        keyboard.notify_keyval(t(), keyval, Clutter.KeyState.PRESSED);
        keyboard.notify_keyval(t(), keyval, Clutter.KeyState.RELEASED);
        await sleep(wait);
    };
    const type = async text => {
        for (const ch of text)
            await key(Clutter.unicode_to_keysym(ch.codePointAt(0)), 30);
    };

    const destroyed = new Set();
    const agent = () => Main.componentManager._allComponents?.polkitAgent;
    const dialog = () => agent()?._currentDialog ?? null;
    const statusText = d => d._statusLabel.text;

    async function openPrompt(label, promise) {
        const found = await waitFor(`${label} prompt`, () => dialog(), 10000, 5);
        const d = found.value;
        d.connect('destroy', () => destroyed.add(d));
        // The very first frame: the field is there and has the keyboard.
        const focus = global.stage.key_focus;
        const first = {
            msToDialog: found.ms,
            fieldVisible: d._field.visible && d._entry.visible && d._entry.mapped !== undefined,
            fieldFocused: focus === d._entry || focus === d._entry.clutter_text,
            readerKnown: d._finger.visible,
        };
        log(`${label} first frame`, first);
        return {d, first, promise};
    }

    function measure(d) {
        const card = rect(d._card);
        const axis = card.x + card.w / 2;
        const textCenter = label => {
            const r = rect(label);
            const [, logical] = label.clutter_text.get_layout().get_pixel_extents();
            const [lx] = label.clutter_text.get_transformed_position();
            // Pango measures in the text's resource pixels.
            const k = label.clutter_text.get_resource_scale();
            // Centered lines: the widest line's centre.
            return r1(lx + (logical.x + logical.width / 2) / k) || r1(r.x + r.w / 2);
        };
        const center = actor => {
            const r = rect(actor);
            return r1(r.x + r.w / 2);
        };
        const icon = d._appIcon ? center(d._appIcon) : center(d._who);
        const out = {
            card,
            axis: r1(axis),
            offsets: {
                icon: r1(icon - axis),
                title: r1(textCenter(d._title) - axis),
                body: r1(textCenter(d._body) - axis),
                status: d._statusLabel.text ? r1(center(d._status) - axis) : null,
                field: r1(center(d._field) - axis),
                details: r1(center(d._detailsToggle) - axis),
            },
            buttons: {cancel: rect(d._cancelButton), allow: rect(d._allowButton)},
            logical: {cardWidth: r1(card.w / scale)},
        };
        out.equalButtons = Math.abs(out.buttons.cancel.w - out.buttons.allow.w) < 1;
        out.centred = Object.values(out.offsets).every(v => v === null || Math.abs(v) <= 1.5);
        return out;
    }

    async function shotCard(name, d, pad = 72) {
        // The card's own box, not its transformed bounds.
        const c = {x: d._holder.x, y: d._holder.y, w: d._holder.width, h: d._holder.height};
        const x = Math.max(monitor.x, Math.floor(c.x - pad * scale));
        const y = Math.max(monitor.y, Math.floor(c.y - pad * scale));
        const w = Math.min(monitor.x + monitor.width - x, Math.ceil(c.w + 2 * pad * scale));
        const h = Math.min(monitor.y + monitor.height - y, Math.ceil(c.h + 2 * pad * scale));
        await shot(name, x, y, w, h);
    }

    const settle = async d => {
        await waitFor('open motion', () => d._holder.opacity === 255 && d._holder.translation_y === 0 && d._holder.scale_x === 1, 2000);
        await sleep(250);
    };

    const run = async (name, fn) => {
        if (!want(name))
            return;
        const started = now();
        try {
            results.cases[name] = await fn();
            results.cases[name].ms = Math.round(now() - started);
            log(`case ${name}`, results.cases[name]);
        } catch (e) {
            results.cases[name] = {error: `${e}`, stack: e.stack};
            log(`case ${name} ERROR`, results.cases[name]);
            const d = dialog();
            if (d)
                d.cancel();
        }
        save();
        await waitFor('no prompt', () => !dialog(), 5000).catch(() => {});
        await sleep(900);
    };

    const usb = () => request('org.projectluma.Imager', 'org.freedesktop.udisks2.open-device', {
        message: {text: 'Authentication is required to open $(drive) for writing', domain: 'udisks2'},
        details: {drive: 'USB DISK 3.0 (/dev/sda)', device: '/dev/sda'},
    });

    // 1. Imager writes to a USB drive: idle, typing, details, accepted, and
    //    the password taken while the reader is still listening.
    await run('usb', async () => {
        const {d, first, promise} = await openPrompt('usb', usb());
        const listening = await waitFor('reader listening', () => d._finger.visible && statusText(d) === 'Or touch the fingerprint reader.');
        await settle(d);
        const layout = measure(d);
        await shotCard(`usb-idle-${theme}`, d);
        await shot(`usb-idle-fullscreen-${theme}`, monitor.x, monitor.y, monitor.width, monitor.height);
        await type(PASSWORD.slice(0, 6));
        await sleep(200);
        const allowAfterTyping = d._allowButton.reactive;
        await shotCard(`usb-typing-${theme}`, d);
        d._detailsToggle.checked = true;
        await sleep(400);
        const facts = d._facts.get_children().map(row => row.get_children().map(l => l.text.replace(/\u200b/g, '')).join(': '));
        await shotCard(`usb-details-${theme}`, d);
        d._detailsToggle.checked = false;
        await sleep(200);
        await type(PASSWORD.slice(6));
        const stillListening = d._conversations.fingerprint === 'listening';
        await key(Clutter.KEY_Return, 0);
        const accepted = await waitFor('accepted', () => statusText(d) === 'Password accepted.', 6000, 5);
        await shotCard(`usb-accepted-${theme}`, d);
        const answer = await promise;
        await waitFor('prompt closed', () => destroyed.has(d), 3000);
        return {
            first, listeningAfterMs: listening.ms, layout, allowAfterTyping, facts,
            readerListeningWhenSubmitted: stillListening,
            acceptedAfterMs: accepted.ms,
            answer,
            pass: first.fieldVisible && first.fieldFocused && layout.centred && layout.equalButtons &&
                stillListening && answer.authorized === true && accepted.ms < 5000 && allowAfterTyping,
        };
    });

    // 2. A wrong password, then the right one, with the reader listening.
    await run('wrong', async () => {
        const {d, first, promise} = await openPrompt('wrong', usb());
        await waitFor('reader listening', () => d._finger.visible);
        await settle(d);
        await type('not-it');
        await key(Clutter.KEY_Return, 0);
        const rejected = await waitFor('rejected', () => statusText(d) === 'That password is not right.', 8000, 10);
        await sleep(450);
        await shotCard(`wrong-password-${theme}`, d);
        const selected = d._entry.clutter_text.get_selection() === 'not-it';
        const errorOutline = d._field.has_style_pseudo_class('error');
        const listening = d._conversations.fingerprint === 'listening';
        await type(PASSWORD);
        await key(Clutter.KEY_Return, 0);
        await waitFor('accepted', () => statusText(d) === 'Password accepted.', 8000, 10);
        const answer = await promise;
        return {first, rejectedAfterMs: rejected.ms, selected, errorOutline, listeningAfterRejection: listening, answer,
            pass: selected && errorOutline && listening && answer.authorized === true};
    });

    // 3. Settings changes the time; a touch with the field empty allows it.
    await run('fingerprint', async () => {
        const {d, first, promise} = await openPrompt('time', request('org.gnome.Settings', 'org.freedesktop.timedate1.set-time'));
        await waitFor('reader listening', () => d._finger.visible);
        await settle(d);
        await shotCard(`time-idle-${theme}`, d);
        const layout = measure(d);
        await reader(`SCAN ${FINGER}`);
        const matched = await waitFor('recognized', () => statusText(d) === 'Fingerprint recognized.', 8000, 5);
        const empty = d._entry.get_text() === '';
        const green = d._finger.has_style_class_name('luma-seal-finger-matched');
        await shotCard(`fingerprint-match-${theme}`, d);
        const answer = await promise;
        return {first, layout, matchedAfterMs: matched.ms, empty, green, answer,
            pass: answer.authorized === true && layout.centred};
    });

    // 4. Depot installs for everyone; an unknown finger is refused, then the
    //    password still works.
    await run('miss', async () => {
        const {d, first, promise} = await openPrompt('depot', request('org.projectluma.Depot', 'org.freedesktop.Flatpak.app-install'));
        await waitFor('reader listening', () => d._finger.visible);
        await settle(d);
        await shotCard(`depot-idle-${theme}`, d);
        await reader('SCAN someone-else');
        await waitFor('not recognized', () => statusText(d) === 'Fingerprint not recognized.', 8000, 5);
        await sleep(380);
        await shotCard(`fingerprint-miss-${theme}`, d);
        const red = d._finger.has_style_class_name('luma-seal-finger-miss');
        await type(PASSWORD);
        await key(Clutter.KEY_Return, 0);
        await waitFor('accepted', () => statusText(d) === 'Password accepted.', 8000, 10);
        const answer = await promise;
        return {first, red, answer, pass: red && answer.authorized === true};
    });

    // 5. pkexec from a terminal; a touch with the field partly typed.
    await run('pkexec', async () => {
        const scope = scopeFor('org.gnome.Ptyxis');
        // The terminal runs each shell in a scope of its own.
        const inner = `vte-spawn-${GLib.uuid_string_random()}.scope`;
        const {done} = spawn(['bash', '-c',
            `systemd-run --user --scope --quiet --unit=${inner} pkexec rpm-ostree kargs --append=quiet; echo "exit=$?"`], scope);
        const {d, first} = await openPrompt('pkexec', done);
        await waitFor('reader listening', () => d._finger.visible);
        await settle(d);
        await shotCard(`pkexec-idle-${theme}`, d);
        const facts = d._facts.get_children().map(row => row.get_children().map(l => l.text.replace(/\u200b/g, '')).join(': '));
        await type('lu');
        await sleep(150);
        await reader(`SCAN ${FINGER}`);
        await waitFor('recognized', () => statusText(d) === 'Fingerprint recognized.', 8000, 5);
        const title = d._title.text;
        const result = await done;
        return {first, facts, title, result: result.out,
            pass: /exit=0/.test(result.out) && title === 'Terminal wants to run a command as administrator'};
    });

    // 6. 0085: a fingerprint error arrives while the password is being
    //    accepted. The unknown finger is touched at several offsets from
    //    Enter, so the reader's no-match lands before, during and after the
    //    password check; the success must never be held or undone.
    await run('race', async () => {
        const rounds = [];
        for (const offset of [0, 40, 90, 160, 300]) {
            const {d, first, promise} = await openPrompt(`race +${offset}ms`, usb());
            await waitFor('reader listening', () => d._finger.visible);
            await settle(d);
            const events = [];
            const original = d._onConversationEvent.bind(d);
            const t0 = now();
            d._onConversationEvent = (name, data) => {
                events.push([name, Math.round(now() - t0)]);
                original(name, data);
            };
            await type(PASSWORD);
            await key(Clutter.KEY_Return, 0);
            await sleep(offset);
            const touched = reader('SCAN someone-else');
            const accepted = await waitFor('accepted', () => statusText(d) === 'Password accepted.', 8000, 5);
            const status = statusText(d);
            const answer = await promise;
            const answeredMs = Math.round(now() - t0);
            await touched;
            const closed = await waitFor('prompt closed', () => destroyed.has(d), 3000);
            const miss = events.findIndex(([n]) => n === 'fingerprint-miss');
            const success = events.findIndex(([n]) => n === 'success');
            rounds.push({offset, first, events, missBeforeSuccess: miss >= 0 && miss < success, acceptedAfterMs: accepted.ms,
                answeredMs, closedAfterMs: closed.ms, status, authorized: answer.authorized,
                ok: answer.authorized === true && status === 'Password accepted.' && answeredMs < 4000});
            await sleep(700);
        }
        return {rounds, pass: rounds.every(r => r.ok) && rounds.some(r => r.missBeforeSuccess)};
    });

    // 7. Escape cancels, and the action is denied.
    await run('escape', async () => {
        const {d, first, promise} = await openPrompt('escape', usb());
        await settle(d);
        await type('abc');
        await key(Clutter.KEY_Escape, 0);
        const answer = await promise;
        return {first, answer, pass: answer.authorized === false};
    });

    // 8. Keyboard only: focus order and Enter with an empty field.
    await run('keyboard', async () => {
        const {d, first, promise} = await openPrompt('keyboard', usb());
        await waitFor('reader listening', () => d._finger.visible);
        await settle(d);
        await key(Clutter.KEY_Return, 150);
        const emptyEnterIgnored = !d._conversations.checking && dialog() === d;
        await type('x');
        const names = new Map([[d._entry.clutter_text, 'field'], [d._entry, 'field'], [d._reveal, 'show password'],
            [d._finger, 'fingerprint'], [d._cancelButton, 'Cancel'], [d._allowButton, 'Allow'], [d._detailsToggle, 'Details']]);
        const order = [names.get(global.stage.key_focus) ?? `${global.stage.key_focus}`];
        for (let i = 0; i < 6; i++) {
            await key(Clutter.KEY_Tab, 80);
            order.push(names.get(global.stage.key_focus) ?? `${global.stage.key_focus?.constructor?.name}`);
        }
        await shotCard(`keyboard-focus-${theme}`, d);
        const accessible = {field: d._entry.accessible_name, finger: d._finger.accessible_name, card: d._card.accessible_name};
        await key(Clutter.KEY_Escape, 0);
        const answer = await promise;
        const expected = ['field', 'show password', 'fingerprint', 'Cancel', 'Allow', 'Details', 'field'];
        return {first, emptyEnterIgnored, order, answer, accessible,
            pass: emptyEnterIgnored && JSON.stringify(order) === JSON.stringify(expected)};
    });

    // 9. An unknown process asks for an action Luma has no words for.
    await run('unknown', async () => {
        const {d, first, promise} = await openPrompt('unknown', request(null, 'org.freedesktop.hostname1.set-static-hostname'));
        await waitFor('reader listening', () => d._finger.visible);
        await settle(d);
        const layout = measure(d);
        await shotCard(`unknown-app-${theme}`, d);
        const neutral = !d._appIcon && d._who.has_style_class_name('luma-seal-system');
        await type(PASSWORD);
        await key(Clutter.KEY_Return, 0);
        await waitFor('accepted', () => statusText(d) === 'Password accepted.', 8000, 10);
        const title = d._title.text;
        const body = d._body.text;
        const answer = await promise;
        return {first, title, body, neutral, layout, answer,
            pass: neutral && title === 'An app wants administrator access' && answer.authorized === true};
    });

    // 10. No reader (fprintd stopped): no fingerprint button, password only.
    await run('noreader', async () => {
        await root({fprintd: 'stop'});
        try {
            const {d, first, promise} = await openPrompt('noreader', usb());
            await settle(d);
            await sleep(1500);
            const hidden = !d._finger.visible && !d._divider.visible && statusText(d) === '';
            await shotCard(`no-reader-${theme}`, d);
            await type(PASSWORD);
            await key(Clutter.KEY_Return, 0);
            await waitFor('accepted', () => statusText(d) === 'Password accepted.', 8000, 10);
            const answer = await promise;
            const sessions = d._conversations.sessionCount;
            return {first, hidden, answer, sessions, pass: hidden && answer.authorized === true};
        } finally {
            await root({fprintd: 'start'});
            await sleep(1500);
        }
    });

    // 11. Reduced motion: no transforms at any point of the opening.
    await run('reduced', async () => {
        const settings = St.Settings.get();
        const {d, first, promise} = await openPrompt('reduced', usb());
        const atOpen = {translation: d._holder.translation_y, scale: d._holder.scale_x, opacity: d._holder.opacity};
        await waitFor('reader listening', () => d._finger.visible);
        await sleep(300);
        const breathing = !!d._finger.child.get_transition('opacity');
        await key(Clutter.KEY_Escape, 0);
        await promise;
        return {first, animations: settings.enable_animations, atOpen, breathing,
            pass: !settings.enable_animations && atOpen.translation === 0 && atOpen.scale === 1 && !breathing};
    });

    const cases = Object.entries(results.cases);
    results.summary = {passed: cases.filter(([, c]) => c.pass).map(([n]) => n), failed: cases.filter(([, c]) => !c.pass).map(([n]) => n)};
    save();
    log('summary', results.summary);
}
