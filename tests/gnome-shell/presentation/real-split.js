// SPDX-License-Identifier: Apache-2.0
// Real bundled apps in the server harness's isolated HOME/DBus, never a device.
import Gio from 'gi://Gio';
import GioUnix from 'gi://GioUnix';
import GLib from 'gi://GLib';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
export async function testRealSplit(p, check, sleep, shot, source, platform) {
    const infos = new Map();
    for (const name of ['Photos', 'Notes']) {
        const path = `${GLib.get_user_data_dir()}/applications/org.projectluma.${name}.desktop`;
        const env = `env PYTHONPATH=${source}/src/luma-platform/appkit:${source}/src/prairie-core GI_TYPELIB_PATH=${platform}/ui:${platform}/semantics:${platform}/appearance LD_LIBRARY_PATH=${platform}/ui:${platform}/semantics:${platform}/appearance LUMA_PHOTOS_STYLE_PATH=${source}/src/prairie-core/style/photos.css LUMA_NOTES_STYLE_PATH=${source}/src/prairie-core/style/notes.css`;
        GLib.file_set_contents(path, `[Desktop Entry]\nType=Application\nName=${name}\nIcon=application-x-executable\nExec=${env} python3 -m prairie_apps.${name.toLowerCase()}\n`);
        infos.set(name, GioUnix.DesktopAppInfo.new_from_filename(path));
    }
    const mapped = async name => {
        for (let n = 0; n < 100; n++) {
            const window = global.get_window_actors().map(actor => actor.meta_window)
                .find(window => window.get_gtk_application_id() === `org.projectluma.${name}`);
            if (window) return window;
            await sleep(100);
        }
        throw new Error(`Real ${name} did not map`);
    };
    infos.get('Photos').launch([], global.create_app_launch_context(0, -1));
    const photos = await mapped('Photos');
    await sleep(100);
    check('real Photos opens fullscreen', photos.is_fullscreen());
    check('launch real Notes directly into a new split', p._tasks.launchAt(infos.get('Notes'), 'right', photos));
    const notes = await mapped('Notes');
    const fits = () => {
        const m = Main.layoutManager.primaryMonitor, l = photos.get_frame_rect(), r = notes.get_frame_rect();
        return p._tasks.active?.left === photos && p._tasks.active?.right === notes &&
            l.x === m.x && l.y === m.y && l.width === Math.round(m.width/2) && l.height === m.height &&
            r.x === l.x+l.width && r.y === m.y && r.x+r.width === m.x+m.width && r.height === m.height;
    };
    for (let n = 0; n < 60 && !fits(); n++) await sleep(50);
    const rect = w => { const r = w.get_frame_rect(); return {x:r.x,y:r.y,width:r.width,height:r.height,min:w.get_min_size(),max:w.get_max_size(),id:p._tasks._appId(w),fullscreen:w.is_fullscreen()}; };
    check('new Photos/Notes split settles without opening a switcher', fits(), JSON.stringify({photos:rect(photos), notes:rect(notes),
        pending:p._tasks.pending?.id, active:Boolean(p._tasks.active), mode:p.mode}));
    await sleep(400);
    check('real client configure commits keep exact adjoining halves', fits());
    const actorsMatch = [photos, notes].every(window => {
        const [x,y] = window.get_compositor_private().get_transformed_position();
        const buffer = window.get_buffer_rect();
        return Math.abs(x-buffer.x) < 1 && Math.abs(y-buffer.y) < 1;
    });
    check('real Photos and Notes actors match compositor buffer positions', actorsMatch);
    await shot('real-photos-notes-first-split');
    p._tasks.swap(); await sleep(400);
    p._tasks.swap(); await sleep(400);
    check('real app pair remains exact after both swaps', fits());
    const displayCall = (method, parameters = null) => new Promise((resolve, reject) => {
        Gio.DBus.session.call('org.gnome.Mutter.DisplayConfig', '/org/gnome/Mutter/DisplayConfig',
            'org.gnome.Mutter.DisplayConfig', method, parameters, null, Gio.DBusCallFlags.NONE, 5000, null,
            (bus, result) => { try { resolve(bus.call_finish(result).deep_unpack()); } catch (error) { reject(error); } });
    });
    const rotate = async transform => {
        const [serial, monitors] = await displayCall('GetCurrentState');
        const current = monitors[0][1].find(item => item[6]['is-current'].deep_unpack())[0];
        await displayCall('ApplyMonitorsConfig', new GLib.Variant('(uua(iiduba(ssa{sv}))a{sv})',
            [serial, 1, [[0,0,1,transform,true,[[monitors[0][0][0],current,{}]]]], {}]));
        await sleep(700);
    };
    const pair = p._tasks.active;
    await rotate(1);
    check('real Photos/Notes pair survives portrait rotation', p._tasks.active === pair && fits());
    await shot('real-photos-notes-portrait');
    await rotate(0);
    check('real Photos/Notes pair survives rotation back', p._tasks.active === pair && fits());

    photos.delete(global.get_current_time()); notes.delete(global.get_current_time());
    await sleep(300); p.showHome();
}
