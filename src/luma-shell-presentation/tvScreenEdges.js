// SPDX-License-Identifier: GPL-2.0-or-later
// TV posture: Screen edges (overscan). A TV that cuts off the picture's edges
// gets the whole picture back by drawing everything smaller about the centre:
// the shell-state key `tv-screen-edges` (0–10 %) scales the UI group, which
// holds the TV home and every window, live as the key changes. Nothing is
// moved or resized underneath, and leaving TV posture restores scale 1.
//
// Settings › Display › Fit › Screen edges writes the key (TV posture only).

export const MAX_EDGES = 10;

/** The scale for a share of the screen given back at the edges (0–10 %). */
export function edgeScale(percent) {
    const p = Math.max(0, Math.min(MAX_EDGES, Number(percent) || 0));
    return 1 - p / 100;
}

export class TvScreenEdges {
    constructor(settings, actor) {
        this._settings = settings;
        this._actor = actor;
        this._id = 0;
        const schema = settings?.settings_schema;
        this._available = !!schema?.has_key?.('tv-screen-edges');
    }

    enable() {
        if (!this._available || this._id) return;
        this._id = this._settings.connect('changed::tv-screen-edges', () => this._apply());
        this._apply();
    }

    _apply() {
        const scale = edgeScale(this._settings.get_uint('tv-screen-edges'));
        this._actor.set_pivot_point(0.5, 0.5);
        this._actor.set_scale(scale, scale);
    }

    destroy() {
        if (this._id) this._settings.disconnect(this._id);
        this._id = 0;
        if (this._available) this._actor.set_scale(1, 1);
    }
}
