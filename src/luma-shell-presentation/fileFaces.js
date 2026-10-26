// SPDX-License-Identifier: GPL-2.0-or-later
// LumaUI file faces. Metadata and thumbnail policy remain owned by Filer's model.
import Cairo from 'cairo';
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GObject from 'gi://GObject';
import Pango from 'gi://Pango';
import St from 'gi://St';

const KINDS = {pdf: ['PDF', '#d75c40'], doc: ['DOC', '#487acc'], docx: ['DOC', '#487acc'],
    odt: ['DOC', '#487acc'], xls: ['XLS', '#3b9d69'], xlsx: ['XLS', '#3b9d69'], ods: ['XLS', '#3b9d69'],
    key: ['KEY', '#c07827'], pptx: ['PPT', '#c07827'], odp: ['PPT', '#c07827'], iso: ['ISO', '#7661b5']};
export function fileKind(item) {
    if (item.isDirectory) return 'folder';
    const mime = item.contentType ?? '';
    if (mime.startsWith('image/')) return 'photo';
    if (mime.startsWith('video/')) return 'video';
    if (mime.startsWith('audio/')) return 'audio';
    return 'page';
}
function rgb(hex) { return [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255); }
function round(cr, x, y, w, h, r) {
    cr.newSubPath(); cr.arc(x + w - r, y + r, r, -Math.PI / 2, 0);
    cr.arc(x + w - r, y + h - r, r, 0, Math.PI / 2);
    cr.arc(x + r, y + h - r, r, Math.PI / 2, Math.PI);
    cr.arc(x + r, y + r, r, Math.PI, Math.PI * 1.5); cr.closePath();
}
export const FileFace = GObject.registerClass({GTypeName: 'LumaUIFileFace'},
class FileFace extends St.Widget {
    _init(item, {miniature = false} = {}) {
        const kind = fileKind(item);
        const width = miniature ? (kind === 'photo' ? 19 : 17) : kind === 'page' ? 62 : kind === 'folder' ? 88 : 96;
        const height = miniature ? (kind === 'photo' ? 21 : 22) : kind === 'page' ? 78 : kind === 'folder' ? 76 : 72;
        const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor;
        super._init({style_class: `lumaui-file-face lumaui-file-${kind}${miniature ? ' miniature' : ''}`,
            width: width * scale, height: height * scale, x_align: Clutter.ActorAlign.CENTER, y_align: Clutter.ActorAlign.END});
        this._item = item;
        this._shown = undefined;
        this.kind = kind;
        if (kind === 'page') {
            const drawing = new St.DrawingArea({x_expand: true, y_expand: true});
            this.add_child(drawing);
            drawing.connect('repaint', area => {
                const cr = area.get_context();
                const [w, h] = area.get_surface_size();
                cr.scale(w / 62, h / 78);
                cr.setSourceRGB(.96, .95, .92); round(cr, 0, 0, 62, 78, 7); cr.fill();
                cr.setSourceRGB(.84, .83, .79);
                cr.moveTo(49, 0); cr.lineTo(62, 13); cr.lineTo(49, 13); cr.closePath(); cr.fill();
                const ext = item.name.split('.').at(-1).toLowerCase();
                const [label, color] = KINDS[ext] ?? ['FILE', '#7661b5'];
                cr.setSourceRGB(...rgb(color)); round(cr, 8, 9, 31, 15, 3); cr.fill();
                if (!miniature) {
                    cr.setSourceRGB(1, 1, 1); cr.selectFontFace('Sans', Cairo.FontSlant.NORMAL, Cairo.FontWeight.BOLD);
                    cr.setFontSize(9); cr.moveTo(11, 20); cr.showText(label);
                }
                cr.setSourceRGB(.82, .81, .77);
                for (let i = 0; i < 3; i++) { round(cr, 8, 31 + i * 8, i === 2 ? 32 : 46, 4, 1.5); cr.fill(); }
                cr.$dispose();
            });
            this.layout_manager = new Clutter.BinLayout();
        } else if (kind === 'folder') {
            const icon = new St.Icon({gicon: item.gicon ?? new Gio.ThemedIcon({name: 'folder'}), icon_size: miniature ? 20 : 80,
                x_align: Clutter.ActorAlign.CENTER, y_align: Clutter.ActorAlign.END});
            this.layout_manager = new Clutter.BinLayout(); this.add_child(icon);
            if (!miniature && Number.isInteger(item.childCount) && item.childCount >= 0) {
                const count = new St.Label({text: String(item.childCount), style_class: 'lumaui-face-count',
                    x_align: Clutter.ActorAlign.END, y_align: Clutter.ActorAlign.END});
                this.add_child(count);
            }
        } else {
            this.layout_manager = new Clutter.BinLayout();
            this._picture = new St.Widget({style_class: 'lumaui-face-picture', x_expand: true, y_expand: true});
            this.add_child(this._picture);
            this._fallback = new St.Icon({gicon: item.gicon ?? new Gio.ThemedIcon({name: 'image-x-generic-symbolic'}),
                icon_size: miniature ? 14 : 32, x_align: Clutter.ActorAlign.CENTER, y_align: Clutter.ActorAlign.CENTER});
            this.add_child(this._fallback);
            if (!miniature && kind !== 'photo') {
                const mark = new St.Icon({icon_name: kind === 'video' ? 'lumaui-play-symbolic' : 'lumaui-music-symbolic',
                    icon_size: 14, style_class: 'lumaui-face-media', x_align: Clutter.ActorAlign.END, y_align: Clutter.ActorAlign.END});
                this.add_child(mark);
            }
        }
    }
    showThumbnail(path) {
        this._shown = path;
        if (!this._picture) return;
        const uri = Gio.File.new_for_path(path).get_uri();
        this._picture.set_style(`background-image: url("${uri}"); background-size: cover;`);
        this._fallback.hide();
    }
});

export function makeFileCard(item, metadata, activate) {
    const card = new St.Button({style_class: 'lumaui-file-card', can_focus: true, accessible_name: `${item.name}, ${metadata}`});
    card.item = item;
    const box = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL, x_align: Clutter.ActorAlign.CENTER});
    const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor;
    const faceSlot = new St.Bin({height: 86 * scale, x_expand: true, y_align: Clutter.ActorAlign.END});
    const face = new FileFace(item);
    faceSlot.child = face;
    // The native thumbnail queue finds this leaf without a second cache or worker.
    faceSlot._item = item; faceSlot._shown = undefined;
    faceSlot.showThumbnail = path => { faceSlot._shown = path; face.showThumbnail(path); };
    box.add_child(faceSlot);
    const name = new St.Label({text: item.name, style_class: 'lumaui-file-name', x_align: Clutter.ActorAlign.CENTER});
    name.clutter_text.single_line_mode = true; name.clutter_text.ellipsize = Pango.EllipsizeMode.END;
    name.set_width(108 * scale);
    box.add_child(name);
    box.add_child(new St.Label({text: metadata, style_class: 'lumaui-file-meta', x_align: Clutter.ActorAlign.CENTER}));
    card.child = box;
    card.connect('clicked', activate);
    const sync = () => {
        const pressed = card.has_style_pseudo_class('active');
        face.ease({translation_y: pressed ? -1 : card.hover ? -4 : 0, scale_x: pressed ? .97 : 1,
            scale_y: pressed ? .97 : 1, duration: St.Settings.get().enable_animations ? 140 : 0,
            mode: Clutter.AnimationMode.EASE_OUT_QUAD});
    };
    card.connect('notify::hover', sync); card.connect('notify::pseudo-class', sync);
    return card;
}
