// SPDX-License-Identifier: Apache-2.0
// Development delivery only; production starts Presentation from native Main.
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {start} from './preview-adapter.js';

export default class PresentationPreview extends Extension {
    enable() {
        const begin = () => {
            this._preview = start(this.path);
        };
        if (Main.layoutManager._startingUp) {
            this._startup = Main.layoutManager.connect('startup-complete', () => {
                Main.layoutManager.disconnect(this._startup);
                this._startup = 0;
                begin();
            });
        } else {
            begin();
        }
    }

    disable() {
        if (this._startup) Main.layoutManager.disconnect(this._startup);
        this._startup = 0;
        this._preview?.stop();
        this._preview = null;
    }
}
