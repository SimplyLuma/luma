// SPDX-License-Identifier: GPL-2.0-or-later
// Exercise the callback shipped in the built Shell source, including a modal
// grab that reports the tray as the event source for an outside click.
// Native client-window routing is tested separately by notification-outside-native.js.
// Run: gjs -m notification-lip-outside-click.js path/to/lumaNotificationBeacon.js
import Gio from 'gi://Gio';

const [loaded, bytes] = Gio.File.new_for_path(ARGV[0]).load_contents(null);
if (!loaded) throw new Error('Cannot read the built notification lip source');
const source = new TextDecoder().decode(bytes);
// The actor owns the grab and captures redirected application events. A
// stage-only callback cannot observe a normal Wayland client's pointer input.
const marker = "this._outsideId = this.connect('captured-event', ";
const start = source.indexOf(marker);
const end = source.indexOf('\n        });', start);
if (start < 0 || end < 0) throw new Error('Grabbed actor outside-click callback is missing');
if (!source.includes('this._grab = Main.pushModal(this, {actionMode: Shell.ActionMode.POPUP});'))
    throw new Error('Client-window input must be redirected to the notification actor');
if (!source.includes('if (this._outsideId) this.disconnect(this._outsideId);') ||
    !source.includes('if (this._grab) Main.popModal(this._grab);') ||
    !source.includes('this._grab = null;'))
    throw new Error('Closing must disconnect the actor listener and release its modal grab');
const callback = source.slice(start + marker.length, end + '\n        }'.length);

const Clutter = {
    EventType: {BUTTON_PRESS: 1, TOUCH_BEGIN: 2, MOTION: 3},
    EVENT_PROPAGATE: 0,
    EVENT_STOP: 1,
};
const actor = {
    width: 400,
    height: 300,
    closed: 0,
    // A modal grab can claim the actor contains an outside event's source.
    contains: () => true,
    transform_stage_point: (x, y) => [true, x - 100, y - 100],
    close() { this.closed++; },
};
const onCaptured = new Function('Clutter', `return (${callback});`).call(actor, Clutter);
const event = (type, x, y) => ({
    type: () => type,
    get_source: () => actor,
    get_coords: () => [x, y],
});
function check(name, got, expected) {
    if (got !== expected) throw new Error(`${name}: expected ${expected}, got ${got}`);
}

check('inside press propagates', onCaptured(null, event(1, 120, 120)), 0);
check('inside press keeps tray open', actor.closed, 0);
check('outside press consumed', onCaptured(null, event(1, 800, 120)), 1);
check('outside press closes tray despite modal target', actor.closed, 1);
check('outside touch consumed', onCaptured(null, event(2, 120, 40)), 1);
check('outside touch closes tray', actor.closed, 2);
check('motion propagates', onCaptured(null, event(3, 800, 120)), 0);
check('motion keeps tray state', actor.closed, 2);
print('Notification tray outside click: 8/8 passed');
