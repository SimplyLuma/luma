// SPDX-License-Identifier: GPL-2.0-or-later
// Rendering only. Slider retains its native pointer/touch grabs, keyboard,
// scroll handling, value limits and accessible range.
import Clutter from 'gi://Clutter';
import GObject from 'gi://GObject';
import {Slider} from 'resource:///org/gnome/shell/ui/slider.js';

export const FillSlider = GObject.registerClass(
class LumaFillSlider extends Slider {
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
        cr.rectangle(rtl ? width-filled : 0, 0, filled, height);
        cr.setSourceRGBA(...ink, .13);
        cr.fill();
        cr.$dispose();
    }
});
