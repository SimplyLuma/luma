// Screen sharing, a recording screencast and the screenshot UI's screencast, as
// the shelf shows them: which island holds each indicator, and crops.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';

const log = (m, o) => console.log(`[share] ${m} ${JSON.stringify(o ?? {})}`);
const sleep = ms => new Promise(r => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => { r(); return GLib.SOURCE_REMOVE; }));
const rect = a => { const [x, y] = a.get_transformed_position(); const [w, h] = a.get_transformed_size(); return [x, y, w, h].map(Math.round); };
function call(name, path, iface, method, params, type) {
    return new Promise((resolve, reject) => Gio.DBus.session.call(name, path, iface, method, params,
        type ? new GLib.VariantType(type) : null, 0, 10000, null,
        (c, r) => { try { resolve(c.call_finish(r)); } catch (e) { reject(e); } }));
}

export default async function ({Main, shot}) {
    await sleep(2500);
    const mon = Main.layoutManager.primaryMonitor;
    const islands = () => Main.shelf._group.get_children().filter(c => c.visible).map(c => ({
        island: c.style_class.split(' ').find(x => x.startsWith('luma-shelf-') && x !== 'luma-shelf-island'),
        rect: rect(c)}));
    const where = actor => {
        for (let p = actor; p; p = p.get_parent()) {
            if (p.has_style_class_name?.('luma-shelf-island') && !p.has_style_class_name('luma-shelf-root'))
                return p.style_class.split(' ').find(x => x.startsWith('luma-shelf-') && x !== 'luma-shelf-island');
        }
        return actor.get_stage() ? 'not in an island' : 'not on stage';
    };
    const status = () => {
        const area = Main.panel.statusArea;
        const quick = area.quickSettings;
        return {
            islands: islands(),
            screenSharing: {visible: area.screenSharing?.visible, mapped: area.screenSharing?.mapped, in: area.screenSharing && where(area.screenSharing), accessible: area.screenSharing?.accessible_name},
            screenRecording: {visible: area.screenRecording?.visible, mapped: area.screenRecording?.mapped, in: area.screenRecording && where(area.screenRecording)},
            remoteAccess: {visible: quick._remoteAccess?._indicator?.visible, mapped: quick._remoteAccess?._indicator?.mapped,
                in: quick._remoteAccess?._indicator && where(quick._remoteAccess._indicator)},
            wellChildren: Main.shelf._well.get_children().filter(c => c.visible).map(c => [c.style_class, c.child?.style_class, c.child?.accessible_name]),
            recordedMark: Main.shelf._recordingIndicator ? {visible: Main.shelf._recordingIndicator.visible,
                mapped: Main.shelf._recordingIndicator.mapped, in: where(Main.shelf._recordingIndicator)} : null,
            statusName: quick.accessible_name,
        };
    };
    const crop = async name => {
        const k = Math.max(1, ...global.stage.peek_stage_views().map(v => v.get_scale())) /
            St.ThemeContext.get_for_stage(global.stage).scale_factor;
        const strip = 130;
        await shot(name, mon.x * k, (mon.y + mon.height - strip) * k, mon.width * k, strip * k);
    };
    global.backend.get_remote_access_controller()?.connect('new-handle', (_c, handle) =>
        log('handle', {isRecording: handle.is_recording}));
    log('idle', status());
    await crop('idle');

    const sessions = [];
    const share = async recording => {
        const [path] = (await call('org.gnome.Mutter.ScreenCast', '/org/gnome/Mutter/ScreenCast',
            'org.gnome.Mutter.ScreenCast', 'CreateSession',
            new GLib.Variant('(a{sv})', [{'is-recording': new GLib.Variant('b', recording)}]), '(o)')).deepUnpack();
        const connector = global.backend.get_monitor_manager().get_monitor_for_connector
            ? (await call('org.gnome.Mutter.DisplayConfig', '/org/gnome/Mutter/DisplayConfig',
                'org.gnome.Mutter.DisplayConfig', 'GetCurrentState', null)).deepUnpack()[1][0][0][0] : 'Meta-0';
        await call('org.gnome.Mutter.ScreenCast', path, 'org.gnome.Mutter.ScreenCast.Session', 'RecordMonitor',
            new GLib.Variant('(sa{sv})', [connector, {}]), '(o)');
        await call('org.gnome.Mutter.ScreenCast', path, 'org.gnome.Mutter.ScreenCast.Session', 'Start', null);
        sessions.push(path);
        return path;
    };
    try {
        await share(false);
        await sleep(1500);
        log('sharing', status());
        await crop('sharing');
    } catch (e) {
        log('error', {step: 'share', e: `${e}`});
    }
    try {
        await share(true);
        await sleep(1500);
        log('sharing-and-recording', status());
        await crop('sharing-and-recording');
    } catch (e) {
        log('error', {step: 'record', e: `${e}`});
    }
    // Mutter here reports every D-Bus session as not recording, so GNOME's own
    // recording mark is shown the way its applet shows it.
    const applet = Main.panel.statusArea.quickSettings._remoteAccess;
    if (applet?._indicator) {
        applet._indicator.visible = true;
        await sleep(800);
        log('recorded-mark-shown-as-gnome-shows-it', status());
    }
    // The screenshot UI's own screencast.
    try {
        const ui = Main.screenshotUI;
        ui._setScreencastInProgress?.(true);
        if (!ui.screencast_in_progress) {
            ui._screencastInProgress = true;
            ui.notify('screencast-in-progress');
        }
        await sleep(1500);
        log('screenshot-ui-screencast', status());
        await crop('screenshot-ui-screencast');
    } catch (e) {
        log('error', {step: 'screenshot-ui', e: `${e}`});
    }
    // Stop sharing from the indicator, as a click does.
    try {
        const sharing = Main.panel.statusArea.screenSharing;
        sharing?._stopSharing();
        if (applet?._indicator)
            applet._indicator.visible = false;
        await sleep(3000);
        log('after-stop', status());
        await crop('after-stop');
    } catch (e) {
        log('error', {step: 'stop', e: `${e}`});
    }
}
