// SPDX-License-Identifier: GPL-2.0-or-later
// Luma Cast UI render oracle. CU_CASE: comma-separated scenarios.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';
import Shell from 'gi://Shell';

const OUT = GLib.getenv('ORACLE_OUT');
const log = (name, data = {}) => console.log(`[cu] ${name} ${JSON.stringify(data)}`);

function mock(method, sig = null, args = null) {
    return new Promise((resolve, reject) => Gio.DBus.session.call('org.projectluma.Cast1', '/org/projectluma/Cast1',
        'org.projectluma.Cast1.Mock', method, sig ? new GLib.Variant(sig, args) : null, null,
        Gio.DBusCallFlags.NONE, 5000, null, (c, res) => {
            try {
                resolve(c.call_finish(res));
            } catch (e) {
                reject(e);
            }
        }));
}

function dbusCall(dest, path, iface, method, sig, args, reply) {
    return new Promise((resolve, reject) => Gio.DBus.session.call(dest, path, iface, method,
        sig ? new GLib.Variant(sig, args) : null, reply ? new GLib.VariantType(reply) : null,
        Gio.DBusCallFlags.NONE, -1, null, (c, res) => {
            try {
                resolve(c.call_finish(res));
            } catch (e) {
                reject(e);
            }
        }));
}

function extents(actor) {
    const [x, y] = actor.get_transformed_position();
    const [w, h] = actor.get_transformed_size();
    return [Math.round(x), Math.round(y), Math.round(w), Math.round(h)];
}

function findAll(root, pred, acc = []) {
    for (const child of root.get_children()) {
        if (pred(child))
            acc.push(child);
        findAll(child, pred, acc);
    }
    return acc;
}

export default async function ({Main, Scripting, shot: rawShot}) {
    const sleep = ms => Scripting.sleep(ms);
    const pad = 28;
    const shot = (name, x, y, w, h) => {
        const x1 = Math.max(0, Math.round(x)), y1 = Math.max(0, Math.round(y));
        const x2 = Math.min(global.stage.width, Math.round(x + w)), y2 = Math.min(global.stage.height, Math.round(y + h));
        // Logical layout: the stage texture is in device pixels, so a 2x
        // monitor is captured at 2x.
        const k = Math.max(1, ...global.stage.peek_stage_views().map(v => v.get_scale())) /
            St.ThemeContext.get_for_stage(global.stage).scale_factor;
        log('shot', {name, box: [x1, y1, x2 - x1, y2 - y1], k});
        return rawShot(name, x1 * k, y1 * k, (x2 - x1) * k, (y2 - y1) * k);
    };
    const shotActor = (name, actor, extra = pad) => {
        const [x, y, w, h] = extents(actor);
        return shot(name, x - extra, y - extra, w + 2 * extra, h + 2 * extra);
    };
    const results = {steps: []};
    const save = () => GLib.file_set_contents(`${OUT}/results.json`, JSON.stringify(results, null, 1));

    const scale = Number(GLib.getenv('CU_SCALE') || 1);
    if (scale !== 1) {
        const state = (await dbusCall('org.gnome.Mutter.DisplayConfig', '/org/gnome/Mutter/DisplayConfig',
            'org.gnome.Mutter.DisplayConfig', 'GetCurrentState', null, null,
            '(ua((ssss)a(siiddada{sv})a{sv})a(iiduba(ssss)a{sv})a{sv})')).deepUnpack();
        const [serial, monitors] = state;
        const configs = [];
        let x = 0;
        for (const [[connector], modes] of monitors) {
            const current = modes.find(m => m[6]?.['is-current']?.deepUnpack?.()) ?? modes[0];
            configs.push([x, 0, scale, 0, configs.length === 0, [[connector, current[0], {}]]]);
            x += Math.round(current[1] / scale);
        }
        await dbusCall('org.gnome.Mutter.DisplayConfig', '/org/gnome/Mutter/DisplayConfig',
            'org.gnome.Mutter.DisplayConfig', 'ApplyMonitorsConfig', '(uua(iiduba(ssa{sv}))a{sv})',
            [serial, 1, configs, {}]);
        await sleep(4000);
        log('scale', {scale: St.ThemeContext.get_for_stage(global.stage).scale_factor});
    }

    const {getCast} = await import('resource:///org/gnome/shell/ui/lumaCast.js');
    const cast = getCast();
    const qs = Main.panel.statusArea.quickSettings;
    for (let i = 0; i < 40 && !qs._cast; i++)
        await sleep(100);
    const toggle = qs._cast.quickSettingsItems[0];
    const pane = toggle._pane;
    log('ready', {available: cast.available, visible: toggle.visible, displays: cast.displays});

    const qsBox = () => qs.menu._boxPointer;
    const openQS = async () => {
        if (!qs.menu.isOpen)
            Main.panel.toggleQuickSettings();
        await sleep(700);
    };
    const closeQS = async () => {
        if (qs.menu.isOpen)
            qs.menu.close();
        await sleep(500);
    };
    const openDetail = async () => {
        await openQS();
        if (!toggle.menu.isOpen)
            toggle.menu.open();
        await sleep(900);
    };
    const shotQS = name => shotActor(name, qsBox());
    const device = name => [...cast.devices.values()].find(d => d.name === name);
    const click = actor => actor.emit('clicked', 1);
    const optionRows = () => findAll(pane, a => a.constructor?.name?.includes('OptionRow') || a.has_style_class_name?.('luma-cast-option'));
    const segments = () => findAll(pane, a => a.has_style_class_name?.('luma-cast-segment'));
    const launch = async ids => {
        for (const id of ids) {
            Shell.AppSystem.get_default().lookup_app(`org.oracle.${id}.desktop`)?.activate();
            await sleep(1500);
        }
        await sleep(2500);
    };
    const dumpPane = () => {
        const dump = actor => ({type: actor.constructor.name, style: actor.style_class ?? '',
            text: actor.text ?? undefined, visible: actor.visible, box: extents(actor),
            children: actor.get_children().filter(c => c.visible).map(dump)});
        return dump(pane);
    };

    const cases = (GLib.getenv('CU_CASE') || 'tile').split(',');
    for (const c of cases) {
        // Every case starts idle.
        if (cast.session)
            cast.stop();
        cast.dismissError();
        await mock('SetConnectBehaviour', '(s)', ['stream']);
        await sleep(600);
        try {
            switch (c) {
            case 'full':
                await openDetail();
                await sleep(1200);
                log('stage', {w: global.stage.width, h: global.stage.height, box: extents(qsBox()),
                    views: global.stage.peek_stage_views().map(v => v.get_scale())});
                await shot('full', 0, 0, global.stage.width, global.stage.height);
                break;
            case 'tile':
                await openQS();
                await shotQS('tile');
                results.steps.push({c, toggle: extents(toggle), title: toggle.title});
                break;
            case 'searching':
                await mock('SetPreset', '(s)', ['none']);
                await openDetail();
                await sleep(600);
                await shotQS('menu-searching');
                results.steps.push({c, tree: dumpPane()});
                break;
            case 'empty':
                await mock('SetPreset', '(s)', ['none']);
                await openDetail();
                await sleep(10500);
                await shotQS('menu-empty');
                break;
            case 'devices':
                await mock('SetPreset', '(s)', ['room']);
                await openDetail();
                await sleep(1800);
                await shotQS('menu-devices');
                results.steps.push({c, tree: dumpPane()});
                break;
            case 'long':
                await mock('SetPreset', '(s)', ['long']);
                await openDetail();
                await sleep(1500);
                await shotQS('menu-long-names');
                pane._choose(device('Executive Boardroom — East Wing Presentation Display'));
                await sleep(700);
                await shotQS('sheet-long-names');
                break;
            case 'focus': {
                await mock('SetPreset', '(s)', ['room']);
                await openDetail();
                await sleep(1500);
                const rows = findAll(pane, a => a.has_style_class_name?.('luma-cast-device'));
                rows[1]?.grab_key_focus();
                await sleep(300);
                await shotQS('menu-keyboard-focus');
                break;
            }
            case 'sheet': {
                await mock('SetPreset', '(s)', ['room']);
                await openDetail();
                await sleep(1500);
                pane._choose(device('Conference Room B'));
                await sleep(800);
                await shotQS('sheet-entire-screen');
                const extend = optionRows().find(r => r._key === 'extend');
                click(extend);
                await sleep(400);
                await shotQS('sheet-separate-screen');
                // Remembered app: pretend the last cast shared Slides.
                pane._choices.source = 'app';
                pane._choices.appId = 'org.oracle.Slides.desktop';
                pane._syncSheet();
                await sleep(400);
                await shotQS('sheet-one-app');
                pane._choices.source = 'screen';
                pane._syncSheet();
                segments().find(s => s.accessible_name.endsWith('This computer'))?.emit('clicked', 1);
                await sleep(300);
                await shotQS('sheet-optimize-this-computer');
                results.steps.push({c, tree: dumpPane()});
                break;
            }
            case 'displays': {
                await mock('SetPreset', '(s)', ['room']);
                await openDetail();
                await sleep(1500);
                pane._choose(device('Living Room TV'));
                await sleep(800);
                await shotQS('sheet-multi-display');
                break;
            }
            case 'picker': {
                await launch(['Slides', 'Mail', 'Notes']);
                await mock('SetPreset', '(s)', ['room']);
                await openDetail();
                await sleep(1500);
                pane._choose(device('Conference Room B'));
                await sleep(600);
                click(optionRows().find(r => r._key === 'app'));
                await sleep(2200);
                const dialog = Main.layoutManager.modalDialogGroup.get_children().find(d => d.visible);
                if (dialog) {
                    await shotActor('app-picker', dialog.dialogLayout._dialog, 40);
                    dialog._cards?.[1]?.grab_key_focus();
                    await sleep(300);
                    await shotActor('app-picker-keyboard', dialog.dialogLayout._dialog, 40);
                    results.steps.push({c, cards: dialog._cards?.length});
                    dialog._confirm();
                    await sleep(1500);
                    await openDetail();
                    await shotQS('session-one-app');
                }
                break;
            }
            case 'code-enter':
            case 'code-show':
            case 'code-confirm': {
                const mode = c.split('-')[1];
                await mock('SetPreset', '(s)', ['room']);
                await mock('SetConnectBehaviour', '(s)', [mode]);
                await openDetail();
                await sleep(1200);
                cast.connectDevice(device('Living Room TV'), cast.choicesFor(device('Living Room TV')));
                await sleep(1800);
                const dialog = Main.layoutManager.modalDialogGroup.get_children().find(d => d.visible);
                if (!dialog) {
                    results.steps.push({c, error: 'no dialog'});
                    break;
                }
                if (mode === 'enter') {
                    dialog._entry.text = '48';
                    await sleep(300);
                    await shotActor('code-enter', dialog.dialogLayout._dialog, 40);
                    dialog._entry.text = '4821';
                    dialog._submit();
                    await sleep(1400);
                    await shotActor('code-rejected', dialog.dialogLayout._dialog, 40);
                    dialog._entry.text = '1234';
                    dialog._submit();
                    await sleep(1500);
                    results.steps.push({c, closed: !dialog.visible, state: cast.session?.state});
                } else {
                    await shotActor(`code-${mode}`, dialog.dialogLayout._dialog, 40);
                    dialog.close();
                    cast.stop();
                }
                await mock('SetConnectBehaviour', '(s)', ['stream']);
                await sleep(800);
                break;
            }
            case 'connecting': {
                await mock('SetPreset', '(s)', ['room']);
                await mock('SetConnectBehaviour', '(s)', ['hold']);
                await openDetail();
                await sleep(1200);
                cast.connectDevice(device('Conference Room B'), cast.choicesFor(device('Conference Room B')));
                await sleep(900);
                await shotQS('session-connecting');
                await closeQS();
                await sleep(600);
                const ind = Main.panel.statusArea.cast;
                await shotActor('indicator-connecting', ind.container.get_parent(), 24);
                cast.stop();
                await mock('SetConnectBehaviour', '(s)', ['stream']);
                await sleep(600);
                break;
            }
            case 'casting': {
                await mock('SetPreset', '(s)', ['room']);
                await openDetail();
                await sleep(1200);
                cast.connectDevice(device('Conference Room B'), cast.choicesFor(device('Conference Room B')));
                await sleep(1500);
                await closeQS();
                await openDetail();
                await shotQS('session-quick-settings');
                toggle.menu.close();
                await sleep(500);
                await shotQS('tile-casting');
                await closeQS();
                await sleep(600);
                const ind = Main.panel.statusArea.cast;
                const island = ind.container.get_parent()?.get_parent() ?? ind.container;
                await shotActor('indicator', island, 24);
                const shelf = Main.layoutManager.uiGroup.get_children().find(a => a.name === 'lumaShelf');
                if (shelf)
                    await shotActor('shelf-casting', shelf, 12);
                ind.menu.open();
                await sleep(900);
                await shotActor('indicator-menu', ind.menu._boxPointer, 28);
                results.steps.push({c, indicator: extents(ind), state: cast.session?.state, tree: dumpPane()});
                ind.menu.close();
                await sleep(400);
                break;
            }
            case 'reconnecting': {
                await mock('SetPreset', '(s)', ['room']);
                await openDetail();
                await sleep(1200);
                cast.connectDevice(device('Living Room TV'), cast.choicesFor(device('Living Room TV')));
                await sleep(1500);
                await mock('SetState', '(s)', ['reconnecting']);
                await sleep(700);
                await closeQS();
                await openDetail();
                await shotQS('session-reconnecting');
                await closeQS();
                await sleep(600);
                const ind = Main.panel.statusArea.cast;
                await shotActor('indicator-reconnecting', ind.container.get_parent(), 24);
                results.steps.push({c, state: cast.session?.state, tree: dumpPane()});
                cast.stop();
                await sleep(600);
                break;
            }
            case 'error': {
                await mock('SetPreset', '(s)', ['room']);
                await mock('SetConnectBehaviour', '(s)', ['fail:no-answer']);
                await openDetail();
                await sleep(1200);
                pane._choose(device('Conference Room B'));
                await sleep(500);
                findAll(pane, a => a.has_style_class_name?.('luma-cast-primary'))[0]?.emit('clicked', 1);
                await sleep(1600);
                const ind = Main.panel.statusArea.cast;
                await sleep(600);
                await shotActor('indicator-failed', ind.container.get_parent(), 24);
                ind.menu.open();
                await sleep(900);
                await shotActor('indicator-menu-failed', ind.menu._boxPointer, 28);
                ind.menu.close();
                await sleep(400);
                // Opening Quick Settings after a failure explains it there.
                cast.connectDevice(device('Conference Room B'), cast.choicesFor(device('Conference Room B')));
                await sleep(1800);
                await openDetail();
                await shotQS('error-no-answer');
                await closeQS();
                cast.dismissError();
                for (const code of ['wifi-direct-busy', 'receiver-busy', 'encoder-failed']) {
                    await mock('SetConnectBehaviour', '(s)', [`fail:${code}`]);
                    cast.connectDevice(device('Lobby Projector'), cast.choicesFor(device('Lobby Projector')));
                    await sleep(1800);
                    await openDetail();
                    await shotQS(`error-${code}`);
                    await closeQS();
                    cast.dismissError();
                    await sleep(500);
                }
                await mock('SetConnectBehaviour', '(s)', ['stream']);
                break;
            }
            case 'curtain': {
                await mock('SetPreset', '(s)', ['room']);
                await openDetail();
                await sleep(1000);
                cast.connectDevice(device('Conference Room B'), cast.choicesFor(device('Conference Room B')));
                await sleep(1500);
                await closeQS();
                const ModalDialog = await import('resource:///org/gnome/shell/ui/modalDialog.js');
                const dialog = new ModalDialog.ModalDialog({styleClass: 'prompt-dialog'});
                dialog.contentLayout.add_child(new St.Label({text: 'Authentication required'}));
                dialog.contentLayout.add_child(new St.PasswordEntry({style_class: 'prompt-dialog-password-entry'}));
                dialog.addButton({label: 'Cancel', action: () => dialog.close()});
                const t0 = GLib.get_monotonic_time();
                dialog.open();
                const heldAtOpen = Boolean(dialog.get_effect('luma-cast-curtain-hold'));
                await sleep(20);
                const heldAt20 = Boolean(dialog.get_effect('luma-cast-curtain-hold'));
                let released = -1;
                for (let i = 0; i < 100; i++) {
                    if (!dialog.get_effect('luma-cast-curtain-hold')) {
                        released = (GLib.get_monotonic_time() - t0) / 1000;
                        break;
                    }
                    await sleep(10);
                }
                await sleep(400);
                await shot('curtain-prompt-shown', 0, 0, global.stage.width, global.stage.height);
                dialog.close();
                await sleep(600);
                const [logLines] = (await mock('CurtainLog')).deepUnpack();
                results.steps.push({c, heldAtOpen, heldAt20, releasedMs: released, curtainLog: logLines,
                    curtainActive: cast.curtainActive});
                cast.stop();
                await sleep(500);
                break;
            }
            case 'banner': {
                const notify = (title, body, urgency = 1) => new Promise(resolve => {
                    const proc = Gio.Subprocess.new(['gdbus', 'call', '--session', '--dest', 'org.freedesktop.Notifications',
                        '--object-path', '/org/freedesktop/Notifications', '--method', 'org.freedesktop.Notifications.Notify',
                        'Messages', '0', 'mail-unread-symbolic', title, body, '[]', `{"urgency": <byte ${urgency}>}`, '5000'],
                    Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE);
                    proc.wait_async(null, () => resolve());
                });
                const tray = Main.messageTray;
                const drawnCards = () => tray._cards.length;
                await notify('Before casting', 'This banner is allowed.');
                await sleep(1500);
                const before = drawnCards();
                await shot('banner-before-casting', 0, 0, global.stage.width, global.stage.height);
                for (const e of [...tray._cards]) tray._restCard(e, false, false);
                await sleep(600);
                await mock('SetPreset', '(s)', ['room']);
                await openDetail();
                await sleep(1000);
                cast.connectDevice(device('Conference Room B'), cast.choicesFor(device('Conference Room B')));
                await sleep(1500);
                await closeQS();
                await notify('While casting', 'Must not be drawn on the captured monitor.');
                await notify('Critical while casting', 'Must not be drawn on the captured monitor either.', 2);
                await sleep(1500);
                const during = drawnCards();
                await shot('banner-while-casting', 0, 0, global.stage.width, global.stage.height);
                results.steps.push({c, cardsBefore: before, cardsWhileCasting: during,
                    captured: cast.isMonitorCaptured(0), silencing: cast.silencing,
                    trayVisible: tray.visible, queue: tray.queueCount});
                cast.stop();
                await sleep(1500);
                results.steps.push({c: 'banner-after', cardsAfterStop: drawnCards()});
                break;
            }
            default:
                log('unknown', {c});
            }
        } catch (e) {
            results.steps.push({c, error: `${e}\n${e.stack}`});
            log('error', {c, e: `${e}`});
        }
        save();
        await closeQS();
    }
    save();
}
