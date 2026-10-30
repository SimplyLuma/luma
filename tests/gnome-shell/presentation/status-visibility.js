// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source = fs.readFileSync(new URL('../../../src/luma-shell-presentation/presentation.js', import.meta.url), 'utf8');
const pending = new Map();
let serial = 0;
const Main = {sessionMode: {isLocked: false, isGreeter: false}, screenShield: {}, panel: {
    visible: false, hide() {this.visible = false;},
}};
const context = vm.createContext({Main, global: {get_window_actors: () => []}, GLib: {
    PRIORITY_DEFAULT_IDLE: 200, SOURCE_REMOVE: false,
    idle_add(_priority, callback) {pending.set(++serial, callback); return serial;},
}});
const methods = source.slice(source.indexOf('    _restoreMobileLockPanel()'), source.indexOf('    _syncGestureVisibility()'));
const hooks = source.slice(source.indexOf("        this._connect(Main.sessionMode, 'updated'"), source.indexOf("        this._connect(Main.overview, 'showing'"));
vm.runInContext(`this.Fixture = class { ${methods}\n install() { ${hooks} } }`, context);
function flush() {
    const callbacks = [...pending.values()]; pending.clear(); callbacks.forEach(callback => callback());
}
function fixture(mode = 'mobile') {
    const p = new context.Fixture();
    Object.assign(p, {mode, _status: {visible: false}, _home: {visible: false, hide() {this.visible = false;}},
        _windows: new Map(), _handheldOptions: {close() {}}, _editor: {setEditing() {}, syncVisibility() {}},
        _tasks: {setMode() {}}, _syncGestureVisibility() {},
        _loanStatus() {this.loaned = true;}, _releaseStatus() {this.loaned = false;},
        hooks: new Map(), _connect(object, name, callback) {this.hooks.set(object === Main.sessionMode ? `session:${name}` : `shield:${name}`, callback);},
    });
    p.install(); return p;
}
{
    const p = fixture();
    Main.sessionMode.isLocked = true;
    p.hooks.get('session:updated')();
    assert.equal(p._status.visible, false);
    // The final shield transition can follow session-mode listeners. There
    // need not be another session update after other listeners finish.
    Main.sessionMode.isLocked = false;
    p.hooks.get('shield:locked-changed')();
    p.hooks.get('shield:active-changed')();
    assert.equal(pending.size, 1, 'transition signals coalesce without a polling timer');
    flush();
    assert.equal(p._status.visible, true, 'final unlock restores the clock/status parent');
    assert.equal(p.loaned, true, 'Quick Options actors return to the handheld status area');
    p._queueVisibilitySync(); p._destroyed = true; p._status.visible = false;
    flush(); assert.equal(p._status.visible, false, 'queued work cannot revive a destroyed presentation');
}
{
    Main.panel.visible = true;
    Main.sessionMode.isLocked = true;
    const p = fixture('tablet'); p.hooks.get('session:updated')(); flush();
    assert.equal(Main.panel.visible, true, 'Mobile lock panel suppression excludes Tablet');
    Main.sessionMode.isLocked = false;
    p.hooks.get('shield:locked-changed')(); flush();
    assert.equal(p._status.visible, true);
    const desktop = fixture('desktop'); desktop._syncVisibility();
    assert.equal(desktop._status.visible, false);
    assert.equal(Main.panel.visible, true);
}
console.log('PASS status restoration after final unlock, coalescing, teardown and mode isolation');
