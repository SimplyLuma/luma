// SPDX-License-Identifier: GPL-2.0-or-later
// Shared helpers for the dock badge oracle scripts.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

export const OUT = GLib.getenv('ORACLE_OUT') ?? '/oracle/out';
export const wait = ms => new Promise(r => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
    r();
    return GLib.SOURCE_REMOVE;
}));

export async function test(method, ...args) {
    const sig = args.map(() => 's').join('');
    return Gio.DBus.session.call_sync('org.projectluma.Background1', '/org/projectluma/Background1',
        'org.luma.BadgeTest', method, args.length ? new GLib.Variant(`(${sig})`, args) : null,
        null, Gio.DBusCallFlags.NONE, -1, null);
}
export const setAgent = (app, values) => test('SetValues', app, JSON.stringify(values));
export const launcher = (desktopId, props) => test('Launcher', `application://${desktopId}`, JSON.stringify(props));

export const shellState = new Gio.Settings({schema_id: 'org.project_luma.shell-state'});

export function dashIcons() {
    const box = Main.overview.dash._box;
    return box.get_children().map(item => item.child).filter(c => c?.app);
}
export function iconFor(desktopId) {
    return dashIcons().find(c => c.app.get_id() === desktopId);
}

// Stage-space boxes: the artwork (St.Icon), the badge actor, and the pill
// inside its ring.
export function geometry(desktopId) {
    const icon = iconFor(desktopId);
    if (!icon)
        return null;
    const ext = a => {
        const [x, y] = a.get_transformed_position();
        const [w, h] = a.get_transformed_size();
        return {x: Math.round(x * 100) / 100, y: Math.round(y * 100) / 100, width: Math.round(w * 100) / 100, height: Math.round(h * 100) / 100};
    };
    const badge = icon._badge;
    const out = {desktopId, artwork: ext(icon.icon.icon), accessibleName: icon.accessible_name,
        badge: badge.visible ? {...ext(badge), kind: badge.badge?.kind, text: badge._label.text, style: badge.get_style()} : null};
    if (out.badge) {
        const node = badge.get_theme_node();
        const ring = node.get_border_width(0);
        out.badge.ring = ring;
        out.badge.pill = {x: out.badge.x + ring, y: out.badge.y + ring, width: out.badge.width - 2 * ring, height: out.badge.height - 2 * ring};
        const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor;
        // The reference 32 icon inside the 36 artwork: inset 2/36 of it.
        const inset = out.artwork.width * 2 / 36;
        const iconRight = out.artwork.x + out.artwork.width - inset;
        const iconTop = out.artwork.y + inset;
        out.measured = {
            scale,
            pillHeightLogical: out.badge.pill.height / scale,
            pillWidthLogical: out.badge.pill.width / scale,
            ringLogical: ring / scale,
            topOffsetLogical: (iconTop - out.badge.pill.y) / scale,
            rightOffsetLogical: (out.badge.pill.x + out.badge.pill.width - iconRight) / scale,
        };
    }
    return out;
}

export function writeJson(name, data) {
    GLib.file_set_contents(`${OUT}/${name}.json`, JSON.stringify(data, null, 1));
}

// A rectangle around the dock island in stage pixels, kept on the stage.
export function dockBox(pad = 24) {
    const dash = Main.overview.dash;
    const [x, y] = dash.get_transformed_position();
    const [w, h] = dash.get_transformed_size();
    const x1 = Math.max(0, Math.round(x - pad)), y1 = Math.max(0, Math.round(y - pad));
    const x2 = Math.min(global.stage.width, Math.round(x + w + pad)), y2 = Math.min(global.stage.height, Math.round(y + h + pad));
    return [x1, y1, x2 - x1, y2 - y1];
}

// A screenshot at the display's own resolution: in a scaled logical layout
// the stage content is `scale` times the logical size, so the region is too.
Gio._promisify(Shell.Screenshot.prototype, 'screenshot_stage_to_content');
Gio._promisify(Shell.Screenshot, 'composite_to_stream');
export async function deviceShot(name, x, y, width, height) {
    const shooter = new Shell.Screenshot();
    const [content, scale] = await shooter.screenshot_stage_to_content();
    const texture = content.get_texture();
    const file = Gio.File.new_for_path(`${OUT}/${name}.png`);
    const stream = file.replace(null, false, Gio.FileCreateFlags.NONE, null);
    await Shell.Screenshot.composite_to_stream(texture, Math.round(x * scale), Math.round(y * scale),
        Math.round(width * scale), Math.round(height * scale), scale, null, 0, 0, 1, stream);
    stream.close(null);
    return {scale, textureWidth: texture.get_width(), stageWidth: global.stage.width};
}
