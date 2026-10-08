// SPDX-License-Identifier: GPL-2.0-or-later
// Actual lip methods with a strict allocation boundary. Native startup/D-Bus
// verification remains mandatory; this test cannot reproduce Clutter itself.
const Gio = imports.gi.Gio;
const root = ARGV[0];
if (!root) throw Error('usage: gjs allocation-behavior.js APPLIED_SOURCE');
const [, bytes] = Gio.File.new_for_path(`${root}/js/ui/lumaNotificationBeacon.js`).load_contents(null);
let source = new TextDecoder().decode(bytes).replace(/^import .*;\n/gm, '').replace(/^export /gm, '');
function assert(ok, message) { if (!ok) throw Error(message); }
let nextId = 0;
const pending = new Map();
const GLib = {PRIORITY_DEFAULT_IDLE: 200, SOURCE_REMOVE: false,
    idle_add: (_priority, fn) => { const id = ++nextId; pending.set(id, fn); return id; },
    source_remove: id => pending.delete(id)};
function flush() {
    let count = 0;
    while (pending.size) {
        assert(++count < 10, 'allocation updates must settle, not schedule forever');
        const [id, fn] = pending.entries().next().value;
        pending.delete(id); fn();
    }
}
const monitor = {x: 0, y: 0, width: 1440, height: 900, index: 0};
const Main = {layoutManager: {primaryMonitor: monitor},
    sessionMode: {hasNotifications: true, isLocked: false, isGreeter: false},
    messageTray: {getSources: () => []}};
const St = {Bin: class {}, Widget: class {},
    ThemeContext: {get_for_stage: () => ({scale_factor: 1})},
    Settings: {get: () => ({enable_animations: true})}};
const MessageTray = {NotificationLifecycleState: {ACTED: 1, DISMISSED: 2, EXPIRED: 3}};
const GObject = {registerClass: c => c};
const Clutter = {AnimationMode: {EASE_OUT_QUART: 0}};
const {Lip} = new Function('GObject', 'St', 'MessageTray', 'Main', 'GLib', 'Clutter', 'global', 'peekCast',
    source + '\nreturn {Lip: NotificationLip};')(GObject, St, MessageTray, Main, GLib, Clutter, {stage: {}}, () => null);
function group({allocated = true, occupied = true, edge = 'top', index = 0} = {}) {
    const g = {visible: true, placement: {edge, monitor: index}, reads: 0,
        has_allocation: () => allocated,
        row: {get_children: () => [{visible: occupied}]},
        get_transformed_position() { assert(allocated && occupied, 'must not force dormant/unallocated group layout'); this.reads++; return [600, 8]; },
        get_transformed_size() { assert(allocated && occupied, 'must not force dormant/unallocated group layout'); this.reads++; return [100, 48]; },
        connectObject(_signal, fn) { this.onAllocation = fn; }};
    return g;
}
function actor(groups, allocated = true, shelfAllocated = true) {
    pending.clear();
    Main.shelf = {_groups: groups, has_allocation: () => shelfAllocated,
        connectObject(...args) { this.onAllocation = args[1]; this.onLayout = args[3]; }};
    const a = Object.create(Lip.prototype);
    Object.assign(a, {_state: 'idle', _sources: new Map(), _notifications: new Set(),
        width: 44, height: 5, _hasInitialAllocation: allocated, _engaged: () => false,
        _positionSettings: {get_string: () => 'top-center'},
        has_allocation: () => allocated,
        set_size(w, h) { this.width = w; this.height = h; this.sizes = (this.sizes ?? 0) + 1; },
        set_position(x, y) { this.x = x; this.y = y; },
        ease() { this.eases = (this.eases ?? 0) + 1; },
        remove_transition() {}, get_transition: () => null, queue_relayout() {},
        _render() {}});
    return a;
}
const invalid = group({allocated: false});
let a = actor([invalid]); a._place();
assert(invalid.reads === 0 && a.y === 0, 'unallocated top group must not force layout');
const empty = group({occupied: false});
a = actor([empty]); a._place();
assert(empty.reads === 0 && a.y === 0, 'saved notifications-only group must reserve no band');
const top = group();
a = actor([top], true, false); a._place();
assert(top.reads === 0, 'unallocated Shelf ancestor must not force layout');
a = actor([top]); a._place();
assert(top.reads === 2 && a.y === 56, 'occupied allocated top band must still place the lip below it');
const wrongMonitor = group({index: 1}), bottom = group({edge: 'bottom'});
a = actor([wrongMonitor, bottom]); a._place();
assert(wrongMonitor.reads === 0 && bottom.reads === 0, 'only the primary top band affects lip placement');
a = actor([], false); a._place();
assert(a.sizes === 1 && !a.eases && a.width === 44 && a.height === 5,
    'first allocation must receive a concrete extent without easing');
a = actor([], true);
a.has_allocation = () => false; a._place();
assert(a.eases === 1 && !a.sizes, 'later invalidation must retain the normal morph after the first allocation');
const notifying = group();
a = actor([notifying]); a._queueSync(); flush();
let inAllocation = false, placements = 0;
a._place = () => { assert(!inAllocation, 'placement must not re-enter an allocation notification'); placements++; };
inAllocation = true;
notifying.onAllocation(); Main.shelf.onAllocation(); Main.shelf.onLayout();
assert(placements === 0 && pending.size === 1, 'allocation/layout updates must defer and coalesce');
inAllocation = false; flush();
assert(placements === 1 && pending.size === 0, 'one deferred placement must settle');
a._destroying = true; a._queuePlace();
assert(pending.size === 0, 'destroying lip must not schedule layout');
print('PASS: nine production allocation boundary cases (native startup still required)');
