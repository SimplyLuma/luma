// SPDX-License-Identifier: GPL-2.0-or-later
// The movable-islands model (ADR-044, js/ui/shelfArrangement.js): the default
// layout, normalisation, status fusion, the band solver, drop targets, moves,
// keyboard steps, announcements, struts and displays.
// Run: gjs -m shelf-arrangement.js path/to/shelfArrangement.js
import GLib from 'gi://GLib';
import System from 'system';
const A = await import(
    GLib.filename_to_uri(GLib.canonicalize_filename(ARGV[0], GLib.get_current_dir()), null));

let failures = 0, checks = 0;
const check = (name, actual, expected) => {
    checks++;
    const a = JSON.stringify(actual), e = JSON.stringify(expected);
    if (a !== e) {
        failures++;
        printerr(`FAIL ${name}: expected ${e}, got ${a}`);
    }
};
const ok = (name, value) => check(name, Boolean(value), true);

// ------------------------------------------------------------ vocabulary
// The dock's folders are an id of their own only where that patch is in.
const CORE_IDS = ['dock', 'live', 'live2', 'media', 'well', 'quick-options', 'clock', 'notifications'];
const HAS_FOLDERS = A.ISLAND_IDS.includes('folders');
check('ids', A.ISLAND_IDS.filter(id => id !== 'folders'), CORE_IDS);
check('the dock comes first', A.ISLAND_IDS[0], 'dock');
check('Studio island gap', A.ISLAND_GAP, 10);
check('group separation', A.GROUP_SEPARATION, 24);
check('hold', [A.HOLD_FEEDBACK_TIME, A.HOLD_TIME, A.DRAG_THRESHOLD], [150, 400, 8]);

// ------------------------------------------------------- default + v1
const def = A.defaultArrangement({edge: 'bottom'});
check('default is one bottom-centre group of every island', def,
    [{display: '', edge: 'bottom', anchor: 'center', position: 0.5, islands: [...A.ISLAND_IDS]}]);
check('default follows shelf-edge', A.defaultArrangement({edge: 'left'})[0].edge, 'left');
check('default ignores a bad edge', A.defaultArrangement({edge: 'middle'})[0].edge, 'bottom');
const luma = A.defaultArrangement({edge: 'bottom', groupLayout: 'luma'});
// Studio Desktop's luma preset (align: center): one centred row of the dock,
// the live tiles and the status island; notifications drop from the top lip.
check('luma preset is one centred row', [luma[0].anchor, luma[0].islands],
    ['center', HAS_FOLDERS
        ? ['dock', 'folders', 'live', 'live2', 'media', 'well', 'quick-options', 'clock']
        : ['dock', 'live', 'live2', 'media', 'well', 'quick-options', 'clock']]);
check('luma notifications arrive at the top lip', [luma.length, luma[1].edge, luma[1].anchor, luma[1].islands],
    [2, 'top', 'center', ['notifications']]);
const split = A.defaultArrangement({edge: 'top', groupLayout: 'split-ends', dockAnchor: 'start', actionsAnchor: 'end'});
check('split-ends: the dock (with its folders) at start', [split[0].anchor, split[0].islands],
    ['start', HAS_FOLDERS ? ['dock', 'folders'] : ['dock']]);
const spanning = A.defaultArrangement({edge: 'bottom', spanSplit: true});
check('a spanning shelf of separate islands: dock at the start, the rest at the end',
    spanning.map(g => [g.anchor, g.islands.length]), [['start', HAS_FOLDERS ? 2 : 1], ['end', 7]]);
check('split-ends: the rest at end', [split[1].anchor, split[1].islands.length], ['end', 7]);

// --------------------------------------------------------- normalisation
check('empty means default', A.normalizeArrangement([], {edge: 'bottom'}).isDefault, true);
check('non-array means default', A.normalizeArrangement(null).isDefault, true);
let n = A.normalizeArrangement([
    {display: '', edge: 'top', anchor: 'center', position: 0.5, islands: ['clock', 'bogus']},
    {display: '', edge: 'sideways', anchor: 'start', position: 0.5, islands: ['media']},
    {display: '', edge: 'bottom', anchor: 'nowhere', position: 7, islands: ['dock', 'clock', 'live']},
]);
check('unknown ids dropped, first placement wins', n.groups[0].islands[0], 'clock');
check('no unknown id survives', n.groups.flatMap(g => g.islands).includes('bogus'), false);
check('unknown edge discarded', n.groups.some(g => g.edge === 'sideways'), false);
check('bad anchor becomes centre', n.groups[1].anchor, 'center');
check('an anchored group keeps no position', n.groups[1].position, 0.5);
const freeOn = A.normalizeArrangement([{edge: 'top', anchor: 'free', position: 7, islands: ['media']}], {},
    A.DEFAULT_ORDER, {freePlacement: true});
check('position clamped (free placement on)', freeOn.groups[0].position, 1);
const freeOff = A.normalizeArrangement([{edge: 'top', anchor: 'free', position: 0.1, islands: ['media']},
    {edge: 'bottom', anchor: 'free', position: 0.9, islands: ['dock']},
    {edge: 'left', anchor: 'free', position: 0.55, islands: ['clock']}]);
check('free placement off: free groups go to the nearest anchor',
    freeOff.groups.slice(0, 3).map(g => g.anchor), ['start', 'end', 'center']);
check('nearest anchor', ['start', 'center', 'end'].map((_, i) => A.nearestAnchor([0.2, 0.6, 0.8][i])), ['start', 'center', 'end']);
check('duplicate dropped from the later group', n.groups[1].islands.slice(0, 2), A.ISLAND_IDS.slice(0, 2));
const all = n.groups.flatMap(g => g.islands).sort();
check('every island placed exactly once', all, [...A.ISLAND_IDS].sort());
n = A.normalizeArrangement([{display: '', edge: 'top', anchor: 'end', position: 0.5, islands: ['dock', 'quick-options']}]);
check('missing clock joins after quick options', n.groups[0].islands.indexOf('clock'), n.groups[0].islands.indexOf('quick-options') + 1);
check('missing well joins before quick options', n.groups[0].islands.indexOf('well'), n.groups[0].islands.indexOf('quick-options') - 1);
check('corrupt entries only → default', A.normalizeArrangement([{edge: 'x'}, 5, 'y']).isDefault, true);

// ---------------------------------------------------------------- fusion
check('today: well, qo, clock fused', A.fuseStatus(['dock', 'well', 'quick-options', 'clock']),
    [{id: 'dock'}, {fused: ['well', 'quick-options', 'clock']}]);
check('any order fuses', A.fuseStatus(['clock', 'quick-options', 'well']), [{fused: ['clock', 'quick-options', 'well']}]);
check('something between splits', A.fuseStatus(['well', 'quick-options', 'media', 'clock']),
    [{fused: ['well', 'quick-options']}, {id: 'media'}, {id: 'clock'}]);
check('well and clock alone do not fuse', A.fuseStatus(['well', 'clock']), [{id: 'well'}, {id: 'clock'}]);
check('quick options alone', A.fuseStatus(['quick-options']), [{id: 'quick-options'}]);

// ---------------------------------------------------------------- bands
const monitor = {index: 0, x: 0, y: 0, width: 2560, height: 1440};
const metrics = {inset: 14, thickness: 56, margin: 10};
const bottom = A.bandGeometry(monitor, 'bottom', metrics);
check('bottom band rect', bottom.rect, {x: 14, y: 1370, width: 2532, height: 56});
check('band depth = inset + thickness + margin', bottom.depth, 80);
check('bottom band reserves 80 along the whole edge', bottom.work, {x: 0, y: 1360, width: 2560, height: 80});
const left = A.bandGeometry(monitor, 'left', metrics, {bottom: 80});
check('left band ends inside the bottom band', left.along, [14, 1360]);
check('left band rect', left.rect, {x: 14, y: 14, width: 56, height: 1346});
const right = A.bandGeometry(monitor, 'right', metrics, {top: 80, bottom: 80});
check('right band between both horizontal bands', right.along, [80, 1360]);
check('right band x', right.rect.x, 2560 - 14 - 56);

// ---------------------------------------------------------------- solver
let s = A.solveBand(1000, [{anchor: 'center', length: 400}]);
check('centre group centred', s, [{start: 300, length: 400, clipped: false}]);
s = A.solveBand(1000, [{anchor: 'start', length: 200}, {anchor: 'end', length: 100}]);
check('start and end', s.map(r => r.start), [0, 900]);
s = A.solveBand(1000, [{anchor: 'center', length: 600}, {anchor: 'start', length: 200}]);
check('centre shifts away from a start group, 24 apart', s[0].start, 224);
check('start group stays put', s[1].start, 0);
s = A.solveBand(1000, [{anchor: 'center', length: 600}, {anchor: 'end', length: 200}]);
check('centre shifts away from an end group', s[0].start + 600, 1000 - 200 - 24);
s = A.solveBand(1000, [{anchor: 'free', position: 0.1, length: 100}]);
check('free at its position', s[0].start, 50);
s = A.solveBand(1000, [{anchor: 'free', position: 0.01, length: 100}]);
check('free clamped to the band', s[0].start, 0);
s = A.solveBand(1000, [{anchor: 'free', position: 0.6, length: 100}]);
check('free centre at 60%', s[0].start, 550);
s = A.solveBand(1000, [{anchor: 'center', length: 900, minLength: 200}, {anchor: 'end', length: 200}]);
// .119: a centred dock stays centred; it gives up length on both sides.
check('dock compresses when the band is short, staying centred', [s[0].start, s[0].length], [224, 552]);
s = A.solveBand(1920, [{anchor: 'start', length: 700}, {anchor: 'center', length: 308, minLength: 60},
    {anchor: 'end', length: 300}]);
check('a centred dock with wide neighbours keeps the centre line', s[1].start + s[1].length / 2, 960);
// The live family: one group, where its first member was.
{
    const n = A.normalizeArrangement([
        {edge: 'bottom', anchor: 'center', islands: ['dock', 'media']},
        {edge: 'top', anchor: 'end', islands: ['live', 'clock']},
    ], {}, A.DEFAULT_ORDER).groups;
    check('live family joined at the first member', n[0].islands.filter(id => A.LIVE_FAMILY.includes(id)), ['live', 'live2', 'media']);
    check('live family leaves the other group', n[1].islands.includes('live'), false);
    check('familyOf live', A.familyOf('media'), ['live', 'live2', 'media']);
}
// Free placement: exact, with soft snaps only.
{
    const mon = {index: 0, x: 0, y: 0, width: 2000, height: 1000, sharedEdges: [], closedEdges: []};
    const metrics = {inset: 14, thickness: 56, margin: 10};
    mon.bands = Object.fromEntries(A.EDGES.map(e => [e, A.bandGeometry(mon, e, metrics)]));
    const len = {horizontal: 200, vertical: 200};
    const at = x => A.resolveTarget([x, 40], len, [mon], [], ['media'], 1, {pointer: [x, 40], freePlacement: true});
    check('free: 30 px off centre stays free', at(1030).kind, 'free');
    check('free: exactly where dropped', Math.round(at(1030).position * mon.bands.top.length + 14), 1030);
    check('free: 8 px off centre snaps', at(1008).anchor, 'center');
    const groups = [{index: 0, monitor: 0, edge: 'top', rect: {x: 400, y: 14, width: 300, height: 56},
        islands: [{id: 'dock', rect: {x: 400, y: 14, width: 300, height: 56}}]}];
    const near = A.resolveTarget([700 + 9 + 100 + 4, 40], len, [mon], groups, ['media'], 1, {freePlacement: true});
    check('free: right beside another island attaches', near.kind, 'attach');
    const far = A.resolveTarget([700 + 60 + 100, 40], len, [mon], groups, ['media'], 1, {freePlacement: true});
    check('free: apart stays free', far.kind, 'free');
    // Snapping: attach, dead zone, apart.
    const snap = (x, previous = null) => A.resolveTarget([x, 40], len, [mon], groups, ['media'], 1, {previous});
    const attach = snap(700 + 10 + 100);
    check('attach within ATTACH_REACH', attach.kind, 'attach');
    check('dead zone keeps the attach', snap(700 + 40 + 100, attach).kind, 'attach');
    check('dead zone without an attach is apart', snap(700 + 40 + 100).kind, 'snap');
    check('beyond APART_REACH it is apart', snap(700 + 60 + 100, attach).kind, 'snap');
    // Hysteresis between zones.
    const z1 = snap(1500);
    check('nearest zone', z1.anchor, 'end');
    const mid = (A.zonePoints(mon.bands.top, 200).center + A.zonePoints(mon.bands.top, 200).end) / 2;
    check('hysteresis keeps the shown zone past the midpoint', snap(mid - 10, z1).anchor, 'end');
    check('and lets go when clearly nearer another', snap(mid - 40, z1).anchor, 'center');
}
s = A.solveBand(1000, [{anchor: 'center', length: 900, minLength: 200}, {anchor: 'end', length: 200}]);
check('compressed group still 24 from its neighbour', s[1].start - (s[0].start + s[0].length), 24);
s = A.solveBand(1000, [{anchor: 'center', length: 300, span: true}, {anchor: 'end', length: 200}]);
check('spanning group stretches between its neighbours', [s[0].start, s[0].length], [0, 776]);
s = A.solveBand(1000, [{anchor: 'center', length: 300, span: true}]);
check('spanning alone fills the band', [s[0].start, s[0].length], [0, 1000]);
s = A.solveBand(300, [{anchor: 'start', length: 200}, {anchor: 'end', length: 200}]);
check('overflow is clipped by the band', s[1].start + s[1].length <= 300, true);
s = A.solveBand(1000, [{anchor: 'free', position: 0.5, length: 100}, {anchor: 'center', length: 100}]);
check('two groups at the centre keep 24 apart', Math.abs(s[0].start - s[1].start) >= 124, true);

// ------------------------------------------------------------ zones
const zones = A.zonePoints(bottom, 400);
check('zone points for a 400 island', zones, {start: 214, center: 1280, end: 2346});
check('zone rect start', A.zoneRect(bottom, 'start'), {x: 14, y: 1370, width: 120, height: 56});
check('zone rect end', A.zoneRect(bottom, 'end'), {x: 2546 - 120, y: 1370, width: 120, height: 56});

// ------------------------------------------------------------ targets
const bands = m => Object.fromEntries(A.EDGES.map(e => [e, A.bandGeometry(m, e, metrics,
    {top: 0, bottom: 80})]));
const mon = {...monitor, sharedEdges: [], bands: bands(monitor)};
// One bottom-centre group: dock 1000..1400, clock 1409..1489.
const group = {index: 0, monitor: 0, edge: 'bottom', rect: {x: 1000, y: 1370, width: 489, height: 56},
    islands: [{id: 'dock', rect: {x: 1000, y: 1370, width: 400, height: 56}},
        {id: 'clock', rect: {x: 1409, y: 1370, width: 80, height: 56}}]};
const len = {horizontal: 80, vertical: 56};
let t = A.resolveTarget([1420, 1398], len, [mon], [group], ['clock']);
check('near the dock end: attach after the dock', [t.kind, t.index, t.neighbour, t.side], ['attach', 1, 'dock', 'after']);
t = A.resolveTarget([970, 1398], len, [mon], [group], ['clock']);
check('before the dock: attach before', [t.kind, t.index, t.side], ['attach', 0, 'before']);
t = A.resolveTarget([2500, 1398], len, [mon], [group], ['clock']);
check('bottom-right corner: snap end', [t.kind, t.edge, t.anchor], ['snap', 'bottom', 'end']);
t = A.resolveTarget([1280, 30], len, [mon], [group], ['clock']);
check('top centre: snap centre', [t.kind, t.edge, t.anchor], ['snap', 'top', 'center']);
t = A.resolveTarget([700, 1398], len, [mon], [group], ['clock'], 1, {freePlacement: true});
check('elsewhere on an edge, free placement on: free', [t.kind, t.edge], ['free', 'bottom']);
ok('free position fraction', Math.abs(t.position - (700 - 14) / 2532) < 1e-6);
t = A.resolveTarget([700, 1398], len, [mon], [group], ['clock']);
check('elsewhere on an edge, free placement off: the nearest zone', [t.kind, t.edge, t.anchor], ['snap', 'bottom', 'center']);
// The insertion index follows the pointer against the islands' midpoints
// in physical order, the carried island left out, for an end group too:
// [a 2000..2100] [b 2109..2209] [c 2218..2318], carried 'notifications'.
const endGroup = {index: 1, monitor: 0, edge: 'bottom', anchor: 'end', rect: {x: 2000, y: 1370, width: 427, height: 56},
    islands: [{id: 'a', rect: {x: 2000, y: 1370, width: 100, height: 56}},
        {id: 'b', rect: {x: 2109, y: 1370, width: 100, height: 56}},
        {id: 'notifications', rect: {x: 2218, y: 1370, width: 100, height: 56}},
        {id: 'c', rect: {x: 2327, y: 1370, width: 100, height: 56}}]};
for (const [x, index] of [[2010, 0], [2060, 1], [2104, 1], [2200, 2], [2330, 2], [2390, 3]]) {
    t = A.resolveTarget([x, 1398], len, [mon], [endGroup], ['notifications'], 1, {pointer: [x, 1398]});
    check(`end group: pointer at ${x} inserts at ${index}`, [t.kind, t.index], ['attach', index]);
}
// The status island's parts are items of their own: a part carried out of
// an end group goes back at any index, before the first as readily as after
// the last, and the target says where ("before"/"after" the neighbour).
const parts = {index: 2, monitor: 0, edge: 'bottom', anchor: 'end', rect: {x: 2100, y: 1370, width: 379, height: 56},
    islands: [{id: 'dock', rect: {x: 2100, y: 1370, width: 300, height: 56}},
        {id: 'quick-options', rect: {x: 2409, y: 1370, width: 70, height: 56}}]};
for (const [x, index, side, neighbour] of [[2110, 0, 'before', 'dock'], [2300, 1, 'after', 'dock'],
    [2485, 2, 'after', 'quick-options']]) {
    t = A.resolveTarget([x, 1398], {horizontal: 67, vertical: 56}, [mon], [parts], ['clock'], 1, {pointer: [x, 1398]});
    check(`a status part at ${x}: index ${index}, ${side} ${neighbour}`,
        [t.kind, t.index, t.side, t.neighbour, t.anchor], ['attach', index, side, neighbour, 'end']);
}
// Beside a group: the join band puts the island in it, the separate band
// gives it its own island on that side, on both sides alike.
for (const [x, kind, side] of [[2100 - 10, 'attach', 'before'], [2100 - 60, 'apart', 'before'],
    [2479 + 10, 'attach', 'after'], [2479 + 60, 'apart', 'after']]) {
    t = A.resolveTarget([x, 1398], {horizontal: 67, vertical: 56}, [mon], [parts], ['clock'], 1, {pointer: [x, 1398]});
    check(`beside a group at ${x}: ${kind} ${side}`, [t.kind, t.kind === 'apart' ? t.side : side], [kind, side]);
}

// A wide dock grabbed at its right end, aimed at the empty centre zone: the
// pointer is inside the placeholder, the dock's centre far to its left.
const topCentre = {monitor: 0, edge: 'top', anchor: 'center', rect: A.zoneRect(mon.bands.top, 'center')};
t = A.resolveTarget([1000, 42], {horizontal: 600, vertical: 56}, [mon], [], ['dock'], 1,
    {pointer: [1280, 40], zones: [topCentre]});
check('the pointer inside a zone placeholder wins', [t.kind, t.edge, t.anchor, t.byPointer], ['snap', 'top', 'center', true]);
t = A.resolveTarget([1270, 42], {horizontal: 600, vertical: 56}, [mon], [], ['dock'], 1,
    {pointer: [1570, 90], zones: [topCentre]});
check('or the island centre nearest the zone', [t.kind, t.edge, t.anchor], ['snap', 'top', 'center']);
t = A.resolveTarget([1200, 1398], len, [mon], [group], ['clock'], 1, {pointer: [1100, 1398]});
check('attach also by the pointer over a group', t.kind, 'attach');
t = A.resolveTarget([1200, 900], len, [mon], [group], ['clock']);
check('across the band from the dock: not attach', t.kind !== 'attach', true);
t = A.resolveTarget([1200, 1398], {horizontal: 489, vertical: 56}, [mon], [group], ['dock', 'clock']);
check('a group never attaches to itself', t.kind !== 'attach', true);
// Shared edge: the right edge of the left monitor touches the right monitor.
const m2 = {index: 1, x: 2560, y: 0, width: 1920, height: 1080};
check('shared edges', [A.sharedEdges(monitor, [monitor, m2]), A.sharedEdges(m2, [monitor, m2])], [['right'], ['left']]);
const monShared = {...mon, sharedEdges: ['right']};
t = A.resolveTarget([2550, 700], len, [monShared], [], ['clock']);
check('a shared edge offers no zone: falls to another edge', t.edge !== 'right', true);

// --------------------------------------------------------------- moves
const arr = A.defaultArrangement({edge: 'bottom'});
let moved = A.applyMove(arr, ['dock'], {kind: 'snap', edge: 'bottom', anchor: 'start'});
check('dock to the bottom-left corner', moved.map(g => [g.anchor, g.islands[0]]), [['center', A.ISLAND_IDS[1]], ['start', 'dock']]);
moved = A.applyMove(moved, ['dock'], {kind: 'attach', group: 0, index: 0});
check('dock back beside the group, first', moved.length, 1);
check('dock back in front', moved[0].islands[0], 'dock');
moved = A.applyMove(arr, ['clock'], {kind: 'snap', edge: 'bottom', anchor: 'end'});
check('clock split off to the bottom-right', moved[1], {display: '', edge: 'bottom', anchor: 'end', position: 0.5, islands: ['clock']});
moved = A.applyMove(moved, ['clock'], {kind: 'attach', group: 0, index: 1});
check('clock attached right of the dock', moved[0].islands.slice(0, 2), ['dock', 'clock']);
check('its old group is gone', moved.length, 1);
moved = A.applyMove(arr, ['media'], {kind: 'free', edge: 'top', position: 0.123456});
check('free placement is rounded', moved[1].position, 0.1235);
check('input unchanged', arr[0].islands.length, A.ISLAND_IDS.length);
const two = A.applyMove(arr, ['dock'], {kind: 'snap', edge: 'left', anchor: 'center'});
const merged = A.applyMove(two, ['dock'], {kind: 'attach', group: 0, index: A.ISLAND_IDS.length - 1});
check('group move by ids into another group', merged[0].islands.at(-1), 'dock');

// ------------------------------------------------------------ float ends
const flushBand = A.bandGeometry(monitor, 'bottom', {...metrics, gutter: 0});
check('outer edges that do not float reach the screen ends', flushBand.along, [0, 2560]);
check('floating outer edges keep the inset', A.bandGeometry(monitor, 'bottom', {...metrics, gutter: 14}).along, [14, 2546]);
const protruding = A.bandGeometry(monitor, 'bottom', {inset: 0, thickness: 56, margin: 10});
check('protruding: the band touches the edge, windows keep the padding', [protruding.rect.y, protruding.depth], [1384, 66]);

// ------------------------------------------------------- arrange visuals
check('zone at rest is faint', A.zoneWeight(10000), A.ZONE_REST);
ok('zone rest 20-25%', A.ZONE_REST >= 0.2 && A.ZONE_REST <= 0.25);
ok('zone weight rises smoothly as the pointer nears',
    A.zoneWeight(300) > A.zoneWeight(350) && A.zoneWeight(100) > A.zoneWeight(300) && A.zoneWeight(0) > A.zoneWeight(100));
check('the best zone is full', A.zoneWeight(500, {best: true}), 1);
check('distance to a rect', [A.distanceToRect([5, 5], {x: 0, y: 0, width: 10, height: 10}),
    A.distanceToRect([13, 14], {x: 0, y: 0, width: 10, height: 10})], [0, 5]);
check('the edge line leaves room for each placeholder',
    A.bandSegments(bottom, [{x: 100, y: 1370, width: 120, height: 56}, {x: 1000, y: 1370, width: 200, height: 56}], 12),
    [[14, 88], [232, 988], [1212, 2546]]);
ok('the edge line is faint far from the pointer', A.edgeGlow(100, 2000, 0) < A.EDGE_REST + 0.01);
ok('and brighter near it', A.edgeGlow(2000, 2000, 0) > 0.5);
ok('only while the pointer is near the band', A.edgeGlow(2000, 2000, 1000) < A.EDGE_REST + 0.01);

// ------------------------------------------------------------- keyboard
const groups0 = [group];
const targets = A.orderedTargets(mon, groups0, ['clock'], len);
ok('targets include bottom attach slots', targets.some(x => x.target.kind === 'attach' && x.edge === 'bottom'));
ok('centre zone covered by the group is not offered', !targets.some(x => x.target.kind === 'snap' && x.edge === 'bottom' && x.target.anchor === 'center'));
const here = {kind: 'attach', group: 0, index: 1, edge: 'bottom', monitor: 0};
let step = A.stepTarget(targets, {edge: 'bottom', along: 1449, fraction: 0.57, target: here}, 'left', mon);
check('left from after the dock → before the dock', [step.kind, step.index], ['attach', 0]);
step = A.stepTarget(targets, {edge: 'bottom', along: 1449, fraction: 0.57}, 'right', mon);
check('right → bottom-right corner', [step.kind, step.anchor], ['snap', 'end']);
step = A.stepTarget(targets, {edge: 'bottom', along: 100, fraction: 0.03}, 'up', mon);
check('up from the bottom-left corner → the left edge end', [step.edge, step.anchor], ['left', 'end']);
step = A.stepTarget(targets, {edge: 'bottom', along: 1280, fraction: 0.5}, 'up', mon);
check('up from the bottom centre → the top centre', [step.edge, step.anchor], ['top', 'center']);
step = A.stepTarget(targets, {edge: 'bottom', along: 1280, fraction: 0.5}, 'down', mon);
check('down from the bottom edge: nowhere', step, null);

// -------------------------------------------------------- announcements
const names = {dock: 'Dock', clock: 'Clock', media: 'Media'};
const attached = A.applyMove(arr, ['clock'], {kind: 'attach', group: 0, index: 1});
check('attached announcement', A.describePlacement(attached, 'clock', names), 'Clock, attached to the right of Dock, bottom edge');
const topLeft = A.applyMove(arr, ['media'], {kind: 'snap', edge: 'top', anchor: 'start'});
check('corner announcement', A.describePlacement(topLeft, 'media', names), 'Media, top edge, left corner');
const leftDock = A.applyMove(arr, ['dock'], {kind: 'snap', edge: 'left', anchor: 'center'});
check('side centre announcement', A.describePlacement(leftDock, 'dock', names), 'Dock, left edge, centre');

// ------------------------------------------------------------ work area
const occ = new Map([[0, new Set(['bottom', 'left'])]]);
const res = A.reservations([{...monitor, sharedEdges: []}], occ, metrics);
check('one strut per occupied edge', res.filter(r => r.band).map(r => r.edge).sort(), ['bottom', 'left']);
check('nothing on the empty edges', res.filter(r => !r.band), []);
check('left strut depth', res.find(r => r.edge === 'left').rect, {x: 0, y: 0, width: 80, height: 1440});
check('no struts on a monitor without groups', A.reservations([monitor], new Map(), metrics), []);
check('no strut on a shared edge', A.reservations([{...monitor, sharedEdges: ['right']}],
    new Map([[0, new Set(['right', 'bottom'])]]), metrics).some(r => r.edge === 'right'), false);
const sharedRes = A.reservations([{...monitor, sharedEdges: ['right', 'left']}],
    new Map([[0, new Set(['left', 'bottom'])]]), metrics, {reserveShared: true});
check('with Mutter reservations, a shared band is reserved there', sharedRes.filter(r => r.shared).map(r => [r.edge, r.depth]), [['left', 80]]);
check('an unoccupied shared edge gets nothing', sharedRes.some(r => r.edge === 'right'), false);

// ------------------------------------------------------------- displays
const mons = [{index: 0, id: 'eDP-1|BOE|0x1|', primary: true}, {index: 1, id: 'DP-2|DEL|0x2|X'}];
const pinned = [{display: '', edge: 'bottom', anchor: 'center', position: 0.5, islands: ['dock', 'clock']},
    {display: 'DP-2|DEL|0x2|X', edge: 'top', anchor: 'end', position: 0.5, islands: ['media']}];
let r = A.resolveDisplays(pinned, mons, 0);
check('pinned group on its display', r[1].monitor, 1);
r = A.resolveDisplays(pinned, [mons[0]], 0);
check('absent display: drawn on the primary, same edge and anchor', [r[1].monitor, r[1].edge, r[1].anchor, r[1].fallback], [0, 'top', 'end', true]);
const collide = [{display: '', edge: 'top', anchor: 'end', position: 0.5, islands: ['clock']},
    {display: 'DP-2|DEL|0x2|X', edge: 'top', anchor: 'end', position: 0.5, islands: ['media']}];
r = A.resolveDisplays(collide, [mons[0]], 0);
check('absent display into an occupied zone: appended to that group', [r.length, r[0].islands], [1, ['clock', 'media']]);
r = A.resolveDisplays([{display: '', edge: 'right', anchor: 'center', position: 0.5, islands: ['dock']}], mons, 0, new Map([[0, ['right']]]));
check('a saved edge now shared falls back to the bottom', r[0].edge, 'bottom');
check('primary follows', A.displayFor(0, mons, 0), '');
check('other display pinned by id', A.displayFor(1, mons, 0), 'DP-2|DEL|0x2|X');

print(`${checks - failures}/${checks} passed`);
if (failures)
    System.exit(1);
