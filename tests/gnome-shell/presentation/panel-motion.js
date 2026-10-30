// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {panelProgress, panelSnap} from '../../../src/luma-shell-presentation/layout.js';
const source = fs.readFileSync(new URL('../../../src/luma-shell-presentation/handheldOptions.js', import.meta.url), 'utf8');
let now = 1000000, animations = true;
class Timeline {
    constructor(properties) {Object.assign(this, properties); this.handlers = {};}
    connect(name, callback) {this.handlers[name] = callback;}
    start() {this.running = true;}
    stop() {this.running = false;}
    get_progress() {return .5;}
    complete() {this.handlers.completed();}
}
class Mask {queue_repaint() {}}
const context = vm.createContext({panelProgress, panelSnap, PanelMask: Mask,
    global: {stage: {}}, GLib: {get_monotonic_time: () => now},
    St: {Settings: {get: () => ({enable_animations: animations})},
        ThemeContext: {get_for_stage: () => ({scale_factor: 1})}},
    Clutter: {Timeline, AnimationMode: {EASE_OUT_CUBIC: 1}}});
vm.runInContext(source.slice(source.indexOf('export class PanelReveal')).replace('export class', 'this.Controller = class'), context);
function fixture(start = 0, target = null) {
    const actor = {effects: new Set(), add_effect_with_name(_name, effect) {this.effects.add(effect);},
        remove_effect(effect) {this.effects.delete(effect);}, remove_clip() {}};
    const endpoints = [], content = {opacity: 190, translation_x: 4};
    const controller = new context.Controller(actor, {width: 360, height: 400,
        initialWidth: 60, initialHeight: 22, contents: [content], done: value => endpoints.push(value)});
    controller.begin(start, target);
    return {controller, actor, endpoints, content};
}
{
    const {controller: c, actor, endpoints, content} = fixture();
    assert.equal(c.ready, true, 'the gesture never waits for a stage readback or later preparation frame');
    now += 50000; c.update(189);
    assert.equal(c.progress, .5); assert.equal(c.mask.bounds[3], 211); assert.equal(c.mask.radius, 24);
    assert.equal(content.translation_x, 79); assert.ok(content.opacity > 0 && content.opacity < 190);
    now += 50000; c.update(75.6); assert.ok(Math.abs(c.progress - .2) < 1e-8);
    assert.ok(Math.abs(c.mask.bounds[3] - (22 + 75.6)) < 1e-8, 'the panel edge follows finger displacement without acceleration');
    now += 200000; c.release(); assert.equal(c.target, 0, 'a stationary reversed pull returns closed');
    c.timeline.complete(); assert.deepEqual(endpoints, [0]); assert.equal(actor.effects.size, 0);
    assert.deepEqual(content, {opacity: 190, translation_x: 4});
    c.cancel(); assert.deepEqual(endpoints, [0], 'teardown is idempotent');
}
{
    const {controller: c} = fixture();
    now += 20000; c.update(25); c.release();
    assert.equal(c.target, 1, 'a short fast opening flick opens despite a small distance');
    c.timeline.complete();
}
{
    const {controller: c, endpoints} = fixture(1);
    now += 100000; c.update(-264.6); assert.ok(Math.abs(c.progress - .3) < 1e-8);
    now += 200000; c.release(true); assert.equal(c.target, 1, 'cancelling dismissal restores the open page');
    c.timeline.complete(); assert.deepEqual(endpoints, [1]);
}
{
    animations = false;
    const {controller: c, actor, endpoints} = fixture(0, 1);
    assert.equal(c.timeline, null); assert.equal(actor.effects.size, 0); assert.deepEqual(endpoints, [1]);
}
console.log('PASS live panel preparation, rounded geometry, reversal, flick, cancel, reduced motion and cleanup');
