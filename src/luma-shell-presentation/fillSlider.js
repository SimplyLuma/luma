// SPDX-License-Identifier: GPL-2.0-or-later
// Rendering only. Slider retains its native pointer/touch grabs, keyboard,
// scroll handling, value limits and accessible range.
import Clutter from 'gi://Clutter';
import GObject from 'gi://GObject';
import {Slider} from 'resource:///org/gnome/shell/ui/slider.js';

export const FillSlider = GObject.registerClass({
    Properties: {
        // A live level (0..1) drawn along the foot of the track, e.g. the
        // sound an app is playing; 0 draws nothing.
        'meter': GObject.ParamSpec.double('meter', null, null,
            GObject.ParamFlags.READWRITE, 0, 1, 0),
    },
}, class LumaFillSlider extends Slider {
    get meter() {
        return this._meter ?? 0;
    }

    set meter(value) {
        value = Math.clamp(value, 0, 1);
        if (Math.abs(value - this.meter) < 0.004)
            return;
        this._meter = value;
        this.notify('meter');
        this.queue_repaint();
    }

    vfunc_repaint() {
        if (!this.has_style_class_name('lumaui-filled-slider')) {
            super.vfunc_repaint();
            return;
        }
        const cr = this.get_context();
        const [width, height] = this.get_surface_size();
        const radius = Math.min(12, height / 2, width / 2);
        cr.newSubPath();
        cr.arc(width-radius, radius, radius, -Math.PI/2, 0);
        cr.arc(width-radius, height-radius, radius, 0, Math.PI/2);
        cr.arc(radius, height-radius, radius, Math.PI/2, Math.PI);
        cr.arc(radius, radius, radius, Math.PI, Math.PI*1.5);
        cr.closePath();
        cr.clip();
        const light = this.has_style_class_name('lumaui-light-level');
        const ink = light ? [20/255, 22/255, 26/255] : [1, 1, 1];
        cr.setSourceRGBA(...ink, .055);
        cr.paint();
        const progress = Math.min(1, Math.max(0, this.value / Math.max(.001, this.maximum_value)));
        const filled = width * progress;
        const rtl = this.get_text_direction() === Clutter.TextDirection.RTL;
        // Muted (lumaui-muted-level): the volume stays where it is, quietly.
        const muted = this.has_style_class_name('lumaui-muted-level');
        cr.rectangle(rtl ? width-filled : 0, 0, filled, height);
        cr.setSourceRGBA(...ink, muted ? .06 : .13);
        cr.fill();
        if (this.meter > 0 && !muted) {
            // The rounded clip bends the bar's ends with the track.
            const thickness = Math.max(2, Math.round(height / 11));
            const level = width * this.meter;
            cr.rectangle(rtl ? width-level : 0, height-thickness, level, thickness);
            cr.setSourceRGBA(...ink, light ? .42 : .5);
            cr.fill();
        }
        cr.$dispose();
    }
});
