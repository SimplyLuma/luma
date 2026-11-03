// SPDX-License-Identifier: Apache-2.0
// TV posture: the pointer is out of the way. It hides the moment the remote,
// a pad or a keyboard is used (every one of them arrives as keys) and
// POINTER_IDLE_MS after it last moved; any pointer motion, click or scroll
// shows it again. Mutter's own cursor tracker does the hiding
// (inhibit_cursor_visibility), so no window draws a stray cursor and nothing
// fakes one. It exists only while the posture is TV. The rules are
// tvInputModel.js (pointerAction, POINTER_IDLE_MS).
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';

import {POINTER_IDLE_MS, pointerAction} from './tvInputModel.js';

const EVENT_NAMES = Object.fromEntries(Object.entries(Clutter.EventType).map(([name, value]) => [value, name]));

export class TvPointer {
    constructor() {
        this._tracker = global.backend.get_cursor_tracker();
        this._hidden = false;
        this._timer = 0;
        // Every event, a window's too, before anything handles it; never consumed.
        this._filter = Clutter.event_add_filter(global.stage, event => {
            // Motion Mutter makes up (a repick as the scene changes under a resting pointer) is no hand.
            if (event.get_flags() & Clutter.EventFlags.FLAG_SYNTHETIC) return Clutter.EVENT_PROPAGATE;
            const action = pointerAction(EVENT_NAMES[event.type()] ?? '');
            if (action === 'hide') this._hide();
            else if (action === 'show') this._show();
            return Clutter.EVENT_PROPAGATE;
        });
        this._arm();
    }

    destroy() {
        if (this._filter) Clutter.event_remove_filter(this._filter);
        this._filter = 0;
        if (this._timer) GLib.source_remove(this._timer);
        this._timer = 0;
        // Never left hidden outside TV posture.
        if (this._hidden) this._tracker.uninhibit_cursor_visibility();
        this._hidden = false;
    }

    _show() {
        if (this._hidden) this._tracker.uninhibit_cursor_visibility();
        this._hidden = false;
        this._arm();
    }

    _hide() {
        if (this._timer) GLib.source_remove(this._timer);
        this._timer = 0;
        if (!this._hidden) this._tracker.inhibit_cursor_visibility();
        this._hidden = true;
    }

    // Hidden once the pointer has rested POINTER_IDLE_MS.
    _arm() {
        if (this._timer) GLib.source_remove(this._timer);
        this._timer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, POINTER_IDLE_MS, () => {
            this._timer = 0;
            this._hide();
            return GLib.SOURCE_REMOVE;
        });
    }
}
