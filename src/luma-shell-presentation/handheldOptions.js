// SPDX-License-Identifier: Apache-2.0
// The existing Desktop Quick Options menu, with a handheld status anchor and
// bounded viewport. Controls, appearance, models and detail pages are unchanged.
import Clutter from 'gi://Clutter';
import GObject from 'gi://GObject';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {PopupAnimation} from 'resource:///org/gnome/shell/ui/boxpointer.js';

export class HandheldOptions {
    constructor(home) {
        this.home = home;
        this.menu = Main.panel.statusArea.quickSettings.menu;
        this.enabled = false;
        // Closing a native detail page while its popup is unmapped must not
        // leave a fade queued on the next presentation's main page.
        this._showStudioPage = this.menu._showStudioPage;
        this._showStudioPageOverride = (item, open) => {
            this._showStudioPage.call(this.menu, item, open);
            if (!open && !this.menu._activeMenu && !this.menu.isOpen)
                this._finishMainPageTransition();
        };
        this.menu._showStudioPage = this._showStudioPageOverride;
        this.openId = this.menu.connect('open-state-changed', (_menu, open) => {
            if (!open) return;
            if (!this.menu._activeMenu) this._finishMainPageTransition();
            if (this.enabled) this.resize();
        });
        if (!this.menu._activeMenu) this._finishMainPageTransition();
    }
    get isOpen() { return this.menu.isOpen; }
    setEnabled(enabled) {
        if (enabled === this.enabled) { if (enabled) this.resize(); return; }
        if (!enabled) { this._restore(); return; }
        const main = this.menu._mainPage;
        if (!main) return;
        this.menu.close(PopupAnimation.NONE);
        this._finishMainPageTransition();
        this.enabled = true;
        const parent = main.get_parent();
        this.original = {parent, index: parent.get_children().indexOf(main), style: this.menu.box.get_style()};
        parent.remove_child(main);
        this.scroll = new St.ScrollView({overlay_scrollbars: true,
            hscrollbar_policy: St.PolicyType.NEVER, vscrollbar_policy: St.PolicyType.AUTOMATIC,
            child: main});
        parent.insert_child_at_index(this.scroll, this.original.index);
        this.pageBinding = main.bind_property('visible', this.scroll, 'visible', GObject.BindingFlags.SYNC_CREATE);
        const pan = new Clutter.PanGesture({min_n_points: 1, max_n_points: 1, pan_axis: Clutter.PanAxis.Y});
        pan.set_begin_threshold(8);
        let start;
        pan.connect('may-recognize', () => this.scroll.vadjustment.upper > this.scroll.vadjustment.page_size);
        pan.connect('recognize', () => { start = this.scroll.vadjustment.value; });
        pan.connect('pan-update', gesture => {
            const adjustment = this.scroll.vadjustment;
            adjustment.value = Math.clamp(start - gesture.get_accumulated_delta().get_y(),
                adjustment.lower, Math.max(adjustment.lower, adjustment.upper - adjustment.page_size));
        });
        this.scroll.add_action(pan);
        this.resize();
    }
    resize() {
        if (!this.enabled) return;
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor) return;
        const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor;
        // Desktop's 316px content / 340px outer width, reduced only if needed
        // for a smaller screen. Keep every native style and material intact.
        const width = Math.max(160, Math.min(316, monitor.width / scale - 48));
        this.menu.box.set_style([this.original.style, `width: ${width}px; min-width: 0; max-width: ${width}px;`].filter(Boolean).join('; '));
        this.scroll.set_style(`max-height: ${Math.max(120, monitor.height / scale - 112)}px;`);
        this.menu._boxPointer.setPosition(this.home._statusRight, 1);
    }
    open() { this.setEnabled(this.home.mode !== 'desktop'); if (this.enabled) this.menu.open(); }
    close() { this.menu.close(); }
    _finishMainPageTransition() {
        const main = this.menu._mainPage;
        if (!main) return;
        main.remove_all_transitions();
        main.opacity = 255;
        main.translation_x = 0;
        main.show();
    }
    _restore() {
        if (!this.enabled) return;
        this.menu.close(PopupAnimation.NONE);
        // Returning from a detail page starts the native main-page fade. A
        // presentation change can unmap it midway through that animation.
        // Finish that page transition before returning it to its native parent.
        const main = this.menu._mainPage;
        this._finishMainPageTransition();
        this.pageBinding.unbind();
        this.scroll.set_child(null);
        this.original.parent.insert_child_at_index(main, this.original.index);
        this.scroll.destroy();
        this.menu.box.set_style(this.original.style);
        this.menu._boxPointer.setPosition(this.menu.sourceActor, .5);
        this.enabled = false;
    }
    destroy() {
        this._restore();
        this.menu.disconnect(this.openId);
        if (this.menu._showStudioPage === this._showStudioPageOverride)
            this.menu._showStudioPage = this._showStudioPage;
    }
}
