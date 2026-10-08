// Run against the patched Sushi source tree. These are action/state tests with
// stubbed GI; they do not prove compositor geometry or physical key delivery.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const source = process.argv[2];
if (!source) throw new Error('Pass the patched Sushi source root');
class Widget {}
class Action {
    constructor({name}) { this.name = name; }
    connect(_signal, callback) { this.activate = callback; }
}
const Gtk = {Overlay: Widget, Grid: Widget, ApplicationWindow: Widget,
    DirectionType: {LEFT: 0, RIGHT: 1, UP: 2, DOWN: 3}};
const context = vm.createContext({imports: {gi: {Gtk, Gio: {SimpleAction: Action},
    GObject: {registerClass: (...args) => args.at(-1), ParamSpec: {boolean() {}}, ParamFlags: {READABLE: 1}},
    Gdk: {}, GLib: {}, Sushi: {}}, util: {constants: {}}, ui: {renderer: {}, utils: {}, mimeHandler: {}}}});
vm.runInContext(fs.readFileSync(path.join(source, 'src/ui/mainWindow.js'), 'utf8'), context);
const window = Object.create(context.MainWindow.prototype);
const actions = {}, accelerators = {}, calls = [];
window.application = {set_accels_for_action: (key, value) => accelerators[key] = Array.from(value), emitSelectionEvent() {}};
window.add_action = action => actions[action.name] = action;
window.destroy = () => calls.push('destroy');
window.set_resizable = value => calls.push(['resizable', value]);
window.maximize = () => calls.push('maximize');
window.unmaximize = () => calls.push('unmaximize');
window.resize = (...size) => calls.push(size);
window.get_size = () => [600, 400];
window._embed = {set_size_request: (...size) => calls.push(['content-request', ...size])};
window._resizeWindow = () => calls.push('restore-content-size');
window._fullView = false;
window._renderer = {notify() {}, togglePlayback: () => calls.push('playback')};
window._defineActions();
assert.deepEqual(accelerators['win.open'], ['Return', 'KP_Enter', '<Primary>o']);
assert.deepEqual(accelerators['win.playback'], ['k']);
assert.deepEqual(accelerators['win.quit'], ['space']);
actions.fullscreen.activate();
assert.equal(window._fullView, true);
assert(calls.includes('maximize'));
actions.escape.activate();
assert.equal(window._fullView, false);
assert(!calls.includes('destroy'));
assert(calls.includes('unmaximize'));
assert(calls.includes('restore-content-size'));
assert(calls.some(call => Array.isArray(call) && call[0] === 'content-request' && call[1] === -1 && call[2] === -1));
actions.escape.activate();
assert.equal(calls.at(-1), 'destroy');
actions.playback.activate();
assert.equal(calls.at(-1), 'playback');
window._renderer = {};
actions.playback.activate(); // Non-media preview is a safe no-op.
window.file = {};
window._hasHandler = false;
actions.open.activate(); // No handler must not call launch API.
window._hasHandler = true;
window._renderer.hideOpen = true;
actions.open.activate(); // Folder/archive suppression applies to keys too.
window._renderer.hideOpen = false;
window._opening = true;
actions.open.activate(); // Repeated activation cannot create multiple launches.
console.log('Quick View keyboard/work-area action tests passed');
