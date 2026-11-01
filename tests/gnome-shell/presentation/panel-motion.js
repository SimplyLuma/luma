// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {panelProgress, panelSnap} from '../../../src/luma-shell-presentation/layout.js';
const source = fs.readFileSync(new URL('../../../src/luma-shell-presentation/handheldOptions.js', import.meta.url), 'utf8');
let now = 1000000, animations = true, nextId = 1;
const paints = new Map(), idles = new Map();
function prepare() {
    for (const callback of [...paints.values()]) callback();
    for (const [id, callback] of [...idles]) { idles.delete(id); callback(); }
}
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
    global: {stage: {
        connect_after(_signal, callback) {const id = nextId++; paints.set(id, callback); return id;},
        disconnect(id) {paints.delete(id);},
    }}, GLib: {get_monotonic_time: () => now, PRIORITY_DEFAULT: 0, SOURCE_REMOVE: false,
        idle_add(_priority, callback) {const id = nextId++; idles.set(id, callback); return id;},
        source_remove(id) {idles.delete(id);}},
    St: {Settings: {get: () => ({enable_animations: animations})},
        ThemeContext: {get_for_stage: () => ({scale_factor: 1})}},
    Clutter: {Timeline, OffscreenRedirect: {ALWAYS: 2}, AnimationMode: {EASE_OUT_CUBIC: 1}}});
vm.runInContext(source.slice(source.indexOf('export class PanelReveal'), source.indexOf('export class StatusPulls')).replace('export class', 'this.Controller = class'), context);
function fixture(start = 0, target = null, warm = true, drawer = false) {
    const actor = {effects: new Set(), add_effect_with_name(_name, effect) {this.effects.add(effect);},
        remove_effect(effect) {this.effects.delete(effect);}, remove_clip() {}};
    const endpoints = [], content = {opacity: 190, translation_x: 4, translation_y: 3};
    let redirect = 1;
    Object.defineProperties(content, {
        get_offscreen_redirect: {value: () => redirect},
        set_offscreen_redirect: {value: value => {redirect = value;}},
    });
    const controller = new context.Controller(actor, {width: 360, height: 400,
        initialWidth: 60, initialHeight: 22, drawer, contents: [content], done: value => endpoints.push(value)});
    controller.begin(start, target);
    if (warm) prepare();
    return {controller, actor, endpoints, content};
}
{
    const {controller: c} = fixture(0, null, false);
    assert.equal(c.ready, false, 'a new opening warms its initial textures');
    assert.deepEqual(Array.from(c.mask.bounds), [0, 0, 0, 0]);
    now += 50000; c.update(189);
    prepare();
    assert.equal(c.ready, true);
    assert.equal(c.progress, .5, 'finger progress survives opening preparation');
    assert.equal(c.mask.bounds[3], 211);
    c.cancel();
}
{
    const {controller: c, content} = fixture(1, null, false);
    assert.equal(c.ready, true, 'dismissing an already-open page never warms from zero');
    assert.equal(paints.size, 0);
    assert.deepEqual(Array.from(c.mask.bounds), [0, 0, 360, 400]);
    assert.equal(content.opacity, 190);
    c.cancel();
    assert.equal(content.get_offscreen_redirect(), 1, 'offscreen policy restores after drag');
}
{
    const {controller: c} = fixture(.7, null, false);
    assert.equal(c.ready, true, 'taking over a partial reveal preserves its extent immediately');
    assert.equal(c.progress, .7);
    assert.ok(c.mask.bounds[2] > 0 && c.mask.bounds[3] > 0);
    c.cancel();
}
{
    const {controller: c, actor, endpoints, content} = fixture();
    assert.equal(c.ready, true, 'the prepared GPU layer follows the gesture without stage readback');
    now += 50000; c.update(189);
    assert.equal(c.progress, .5); assert.equal(c.mask.bounds[3], 211); assert.equal(c.mask.radius, 24);
    assert.equal(content.translation_x, 79); assert.ok(content.opacity > 0 && content.opacity < 190);
    now += 50000; c.update(75.6); assert.ok(Math.abs(c.progress - .2) < 1e-8);
    assert.ok(Math.abs(c.mask.bounds[3] - (22 + 75.6)) < 1e-8, 'the panel edge follows finger displacement without acceleration');
    now += 200000; c.release(); assert.equal(c.target, 0, 'a stationary reversed pull returns closed');
    c.timeline.complete(); assert.deepEqual(endpoints, [0]); assert.equal(actor.effects.size, 0);
    assert.deepEqual(content, {opacity: 190, translation_x: 4, translation_y: 3});
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
    const {controller: c, content} = fixture(0, null, true, true);
    now += 50000; c.update(180);
    assert.deepEqual(Array.from(c.mask.bounds), [0, 0, 360, 400], 'Mobile surface keeps its final full-width allocation');
    assert.equal(content.translation_x, 4); assert.equal(content.opacity, 190, 'content is readable before half pull');
    assert.ok(c.mask.coverage > .65 && c.mask.coverage < 1, 'backdrop comes forward with finger progress');
    assert.ok(content.translation_y < 3 && content.translation_y > -2, 'content settles softly into place');
    const forwardCoverage = c.mask.coverage, forwardY = content.translation_y;
    now += 50000; c.update(60);
    assert.deepEqual(Array.from(c.mask.bounds), [0, 0, 360, 400], 'reversal never clips or reallocates the page');
    assert.ok(c.mask.coverage < forwardCoverage); assert.ok(content.translation_y < forwardY);
    c.update(180); assert.equal(c.mask.coverage, forwardCoverage); assert.equal(content.translation_y, forwardY);
    c.cancel(); assert.equal(content.translation_y, 3);
}
{
    const {controller: c} = fixture(1, null, false, true);
    now += 50000; c.update(-140);
    assert.deepEqual(Array.from(c.mask.bounds), [0, 0, 360, 400], 'closing keeps full coverage bounds while fading with the finger');
    assert.equal(c.ready, true); c.cancel();
}
{
    animations = false;
    const {controller: c, actor, endpoints} = fixture(0, 1);
    assert.equal(c.timeline, null); assert.equal(actor.effects.size, 0); assert.deepEqual(endpoints, [1]);
}
assert.equal(paints.size, 0); assert.equal(idles.size, 0);
console.log('PASS live panel preparation, rounded geometry, reversal, flick, cancel, reduced motion and cleanup');
