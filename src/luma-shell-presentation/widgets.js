// SPDX-License-Identifier: Apache-2.0
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import St from 'gi://St';
import Pango from 'gi://Pango';
import LumaWidgets from 'gi://LumaWidgets?version=1.0';

function label(text, style) {
    const actor = new St.Label({text, style_class: style, x_align: Clutter.ActorAlign.START});
    actor.clutter_text.ellipsize = Pango.EllipsizeMode.END;
    return actor;
}

export class HomeWidgets {
    constructor(presentation) {
        this.home = presentation;
        this._actors = new Map();
        this.model = LumaWidgets.Model.new('org.gnome.Shell.CalendarServer');
        this.stylesheet = Gio.File.new_for_uri('resource:///org/project_luma/widgets/widget-st.css');
        St.ThemeContext.get_for_stage(global.stage).get_theme().load_stylesheet(this.stylesheet);
        this._changed = this.model.connect('changed', () => this._render());
        this.snapshot = JSON.parse(this.model.dup_snapshot());
    }

    definitions() {
        return Object.entries(this.snapshot).map(([id, data]) => ({id,
            columns: data.columns, rows: data.rows, gap: data.gap, createActor: () => this._create(id)}));
    }

    _create(id) {
        const actor = new St.Button({style_class: `luma-converged-widget ${id}`,
            can_focus: true, reactive: true, accessible_name: this.snapshot[id].name});
        const content = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL,
            style_class: 'luma-home-widget-content', x_expand: true, y_expand: true});
        const heading = label('', 'luma-converged-widget-heading');
        const title = label('', 'luma-converged-widget-title');
        const detail = label('', 'luma-converged-widget-caption');
        const caption = label('', 'luma-converged-widget-caption');
        content.add_child(heading);
        content.add_child(title);
        content.add_child(new St.Widget({y_expand: true}));
        content.add_child(detail);
        content.add_child(caption);
        actor.set_child(content);
        const entry = {actor, heading, title, detail, caption};
        this._actors.set(id, entry);
        actor.connect('destroy', () => { if (this._actors.get(id) === entry) this._actors.delete(id); });
        actor.connect('clicked', () => {
            if (this.home._editor.editing) this.home._editor.select(id);
            else {
                const info = this.home._apps.find(app => app.get_id() === this.snapshot[id].desktop_id);
                if (info) this.home._launch(info);
            }
        });
        this._render();
        return actor;
    }

    _render() {
        this.snapshot = JSON.parse(this.model.dup_snapshot());
        for (const [id, entry] of this._actors) {
            const data = this.snapshot[id];
            entry.heading.text = data.title;
            entry.title.text = data.value;
            entry.detail.text = data.description;
            entry.caption.text = data.detail;
            entry.actor.accessible_name = [data.title, data.value, data.description, data.detail, data.unit].filter(Boolean).join(', ');
        }
    }

    destroy() {
        this.model.disconnect(this._changed);
        this.model.run_dispose();
        St.ThemeContext.get_for_stage(global.stage).get_theme().unload_stylesheet(this.stylesheet);
    }
}
