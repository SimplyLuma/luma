// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source = fs.readFileSync(new URL('../../../src/luma-shell-presentation/presentation.js', import.meta.url), 'utf8');
const hooks = new Map();
const gesture = {set_begin_threshold(value) {this.threshold = value;}, connect(name, callback) {hooks.set(name, callback);}};
const stage = {add_action_full(name, phase, action) {this.action = action;}};
const Main = {actionMode: 1};
const p = {_gestureBar: {visible: true, get_transformed_position: () => [0,372], get_transformed_size: () => [360,28]},
    _tasks: {navigationEnabled: true}};
const context = vm.createContext({Main, Shell: {ActionMode: {NORMAL: 1}}, global: {stage},
    Clutter: {PanGesture: function () {return gesture;}, PanAxis: {Y: 1}, EventPhase: {CAPTURE: 1}}});
vm.runInContext(`this.install = function () {${source.slice(source.indexOf('        const homePan ='), source.indexOf("        this._connect(this._home, 'notify::visible'"))}}`, context);
context.install.call(p);
const start = (x,y,dx=0,dy=0) => hooks.get('may-recognize')({
    get_point_event: () => ({get_coords: () => [x,y]}),
    get_accumulated_delta_abs: () => ({get_x: () => dx, get_y: () => dy}),
});
assert.equal(stage.action, gesture, 'pan observes client input from the stage, without an overlay hit target');
assert.equal(gesture.threshold, 8, 'motion is required before a tap can be claimed');
for (const x of [0,60,180,300,359]) assert.equal(start(x,399),true);
for (const [x,y] of [[-1,399],[360,399],[180,371],[180,400]]) assert.equal(start(x,y),false);
assert.equal(start(180,330,0,-65),true,'eligibility follows initial position, not current finger position');
Main.actionMode=2;assert.equal(start(180,399),false,'modal panels and switcher own their own input');Main.actionMode=1;
p._tasks.navigationEnabled=false;assert.equal(start(180,399),false,'lock, greeter and Desktop cannot navigate');p._tasks.navigationEnabled=true;
p._gestureBar.visible=false;assert.equal(start(180,399),false);p._gestureBar.visible=true;
const visibility = source.slice(source.indexOf('    _syncGestureVisibility() {'),source.indexOf('    _setStatusWindow('));
const visibilityContext=vm.createContext({Main:{sessionMode:{},panel:{statusArea:{quickSettings:{menu:{isOpen:false}}}}},peekNotificationLip:()=>null});
vm.runInContext(`this.Fixture=class {${visibility}}`,visibilityContext);
const v=new visibilityContext.Fixture();v._gestureBar={};v.mode='mobile';v._syncGestureVisibility();
assert.equal(v._gestureBar.reactive,false,'Mobile leaves app buttons at the bottom reachable');
v.mode='tablet';v._syncGestureVisibility();assert.equal(v._gestureBar.reactive,true,'Tablet keeps its existing Home control');
assert.ok(source.includes('global.stage.remove_action(this._homePan)'), 'teardown removes the stage recognizer');
console.log('PASS bottom-edge start bounds, modal isolation, motion threshold, mobile pass-through and teardown');
