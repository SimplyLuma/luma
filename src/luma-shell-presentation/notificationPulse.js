// SPDX-License-Identifier: GPL-2.0-or-later
// Development compatibility for the native notification pulse lifecycle fix.
import Clutter from 'gi://Clutter';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {peekNotificationLip} from 'resource:///org/gnome/shell/ui/lumaNotificationBeacon.js';

export function breathe() {
    this._glow.remove_all_transitions();
    this._glow.opacity = 0;
    const eligible = () => !this._destroying && !Main.sessionMode.isLocked &&
        !Main.sessionMode.isGreeter && this.mapped && this._glow.mapped &&
        this._state === 'pill' && this._fresh && !this.dnd;
    // A pending notification must not keep an otherwise idle display repainting.
    // Signal briefly, then retain a steady unread indicator.
    let remaining = 6;
    const pulse = up => {
        if (!eligible()) return;
        // Re-check every completion: animation settings or actor mapping may
        // change while a pulse is in flight. An unmapped ease can complete
        // synchronously, so unconditional recursion is unsafe.
        if (!St.Settings.get().enable_animations || remaining-- === 0) {
            this._glow.opacity = 128;
            return;
        }
        this._glow.ease({opacity: up ? 230 : 0, duration: 1300,
            mode: Clutter.AnimationMode.EASE_IN_OUT_SINE, onComplete: () => pulse(!up)});
    };
    pulse(true);
}

export function installNotificationPulseGuard() {
    const lip = peekNotificationLip();
    if (!lip) return () => {};
    const original = lip._breathe;
    lip._breathe = breathe;
    const mapped = lip._glow.connect('notify::mapped', () => lip._breathe());
    const settings = St.Settings.get();
    const animations = settings.connect('notify::enable-animations', () => lip._breathe());
    const session = Main.sessionMode.connect('updated', () => lip._breathe());
    lip._breathe();
    return () => {
        lip._glow.remove_all_transitions();
        lip._glow.disconnect(mapped);
        settings.disconnect(animations);
        Main.sessionMode.disconnect(session);
        if (lip._breathe === breathe) lip._breathe = original;
    };
}
