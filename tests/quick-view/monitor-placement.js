// Quick View placement oracle over mixed-scale monitor layouts.
// Run against the patched Sushi source: node monitor-placement.js SUSHI_ROOT
//
// The preview is sized by mainWindow.js and placed by the compositor. This
// models Luma's Mutter 50.4: a new Wayland window with a transient parent is
// centred horizontally over the parent, a third of the spare height from its
// top (src/core/place.c, meta_window_place), and Mutter patch 0013 clamps that
// position into the work area of the parent's monitor. Stock Mutter leaves
// the position alone, and the headless Shell oracle (tests/quick-view/oracle)
// showed its constraints do not pull such a window back on screen.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');

const source = fs.readFileSync(path.join(process.argv[2], 'src/ui/mainWindow.js'), 'utf8');
const between = (start, end) => source.split(start)[1].split(end)[0];

// Never position the window from the client: no move() and no absolute gravity games.
const klass = source.split('var MainWindow =')[1];
assert(!/\.move\s*\(/.test(klass), 'Quick View must never move its own window');
assert(/_presentAgain\(windowSize\)/.test(klass), 'a shown preview that changes size is presented again');
assert(/this\.hide\(\);[\s\S]*window_set_child_of_external\(this, this\._parentHandle\)[\s\S]*this\.show\(\);/
    .test(between('    _presentAgain(windowSize) {', '    _applyContentSize(')),
    'presenting again re-parents to Filer before the new map');

// A picture's natural size is its pixel size; the pane must report the size
// Quick View chose instead, or GTK 3 grows the window past the monitor.
const embedSource = between('const Embed = ', '\n});\n') + '\n})';
class Overlay {
    constructor(request) { this.request = request; }
    vfunc_get_preferred_width() { return [1, 1800]; }
    vfunc_get_preferred_height() { return [1, 1200]; }
    vfunc_get_preferred_height_for_width() { return [1, 1200]; }
    vfunc_get_preferred_width_for_height() { return [1, 1800]; }
    get_size_request() { return this.request; }
}
const Embed = vm.runInNewContext(embedSource, {GObject: {registerClass: k => k}, Gtk: {Overlay}});
const pane = new Embed([1080, 720]);
assert.deepEqual([...pane.vfunc_get_preferred_width()], [1, 1080]);
assert.deepEqual([...pane.vfunc_get_preferred_height()], [1, 720]);
assert.deepEqual([...pane.vfunc_get_preferred_height_for_width(1080)], [1, 720]);
assert.deepEqual([...new Embed([-1, -1]).vfunc_get_preferred_width()], [1, 1800]);

const helpers = vm.runInNewContext(
    `var PREVIEW_CHROME${between('var PREVIEW_CHROME', 'const ErrorBox =')}
     ({PREVIEW_CHROME, smallestMonitorArea, previewMaxSize})`, {});
const resizeBody = between('    _resizeWindow() {', '    _createRenderer() {').replace(/},?\s*$/, '');
const policy = {MAX_SIZE: 0, NAT_SIZE: 1, SCALED: 2, STRETCHED: 3};
const resize = vm.runInNewContext(`(function(){${resizeBody}})`, {
    Renderer: {ResizePolicy: policy},
    Utils: {getScaledSize: (s, max, up) => {
        const k = Math.min(max[0] / s[0], max[1] / s[1], up ? Infinity : 1);
        return s.map(n => Math.floor(n * k));
    }},
});

// Monitors in logical pixels, as Mutter and GDK report them. `scale` only
// documents the layout: logical geometry already accounts for it. The shelf
// reserves the bottom of the primary monitor.
const SHELF = 76;
function monitor(name, x, y, pixelWidth, pixelHeight, scale, primary = false) {
    const width = Math.round(pixelWidth / scale), height = Math.round(pixelHeight / scale);
    return {name, scale, geometry: {x, y, width, height},
        work: {x, y, width, height: height - (primary ? SHELF : 0)}};
}
const LAYOUTS = {
    // Nick's desk: LG 5120x1440 at 1x beside the X1 panel (1920x1200) at 1.25x.
    'ultrawide 1x + panel 1.25x': [
        monitor('DP-1', 1536, 0, 5120, 1440, 1, true),
        monitor('eDP-1', 0, 695, 1920, 1200, 1.25)],
    // The same pair with the panel as primary.
    'panel 1.25x primary + ultrawide 1x': [
        monitor('eDP-1', 0, 0, 1920, 1200, 1.25, true),
        monitor('DP-1', 1536, 0, 5120, 1440, 1)],
    // Office: 3440x1440 primary, panel at 1.25x to its right, a 2560x1440
    // monitor turned portrait on its left.
    'office three monitors': [
        monitor('DP-5', 1440, 1120, 3440, 1440, 1, true),
        monitor('eDP-1', 4880, 1762, 1920, 1200, 1.25),
        monitor('DP-1', 0, 0, 1440, 2560, 1)],
    // The panel above the ultrawide.
    'panel 1.25x above ultrawide 1x': [
        monitor('DP-1', 0, 960, 5120, 1440, 1, true),
        monitor('eDP-1', 1792, 0, 1920, 1200, 1.25)],
    // A 2x HiDPI panel next to a 1x monitor.
    'panel 2x + 1x monitor': [
        monitor('eDP-1', 0, 0, 2880, 1800, 2, true),
        monitor('HDMI-1', 1440, 0, 1920, 1080, 1)],
};

const FILES = {
    'photo 6000x4000': {mime: 'image/jpeg', nat: [6000, 4000], policy: policy.SCALED},
    'tall screenshot 824x1866': {mime: 'image/png', nat: [824, 1866], policy: policy.SCALED},
    'icon 64x64': {mime: 'image/png', nat: [64, 64], policy: policy.SCALED},
    'wide video 3840x1080': {mime: 'video/mp4', nat: [3840, 1080], policy: policy.SCALED},
    'text file': {mime: 'text/plain', nat: [2000, 4000], policy: policy.MAX_SIZE},
    'Markdown': {mime: 'text/markdown', nat: [2000, 4000], policy: policy.MAX_SIZE},
    'PDF': {mime: 'application/pdf', nat: [2000, 4000], policy: policy.MAX_SIZE},
    'song': {mime: 'audio/flac', nat: [460, 176], policy: policy.MAX_SIZE},
    'folder': {mime: 'inode/directory', nat: [480, 500], policy: policy.MAX_SIZE},
    'archive': {mime: 'application/gzip', nat: [900, 900], policy: policy.MAX_SIZE},
};

function previewSize(monitors, file) {
    let outer;
    const self = {
        _renderer: {get_preferred_width: () => [1, file.nat[0]], get_preferred_height: () => [1, file.nat[1]],
            resizePolicy: file.policy},
        _getMaxSize: () => helpers.previewMaxSize(helpers.smallestMonitorArea(monitors.map(m => m.geometry))),
        _lastWindowSize: [0, 0], _fullView: false,
        _fileInfo: {get_content_type: () => file.mime},
        get_mapped: () => false,
        _embed: {set_size_request() {}},
        resize: (...size) => { outer = size; },
        _applyContentSize(size) { this.resize(size[0] + 12, size[1] + 50); },
    };
    resize.call(self);
    return outer;
}

const overlap = (a, b) => Math.max(0, Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x)) *
    Math.max(0, Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y));
const trunc = n => Math.trunc(n);

const mostOverlap = (monitors, r) => monitors.reduce((best, m) => overlap(r, m.geometry) > overlap(r, best.geometry) ? m : best);

function placeOverParent(monitors, parent, [width, height]) {
    let x = parent.x + trunc(parent.width / 2) - trunc(width / 2);
    let y = parent.y + trunc((parent.height - height) / 3);
    const parentWork = mostOverlap(monitors, parent).work;
    x = Math.max(parentWork.x, Math.min(x, parentWork.x + parentWork.width - width));
    y = Math.max(parentWork.y, Math.min(y, parentWork.y + parentWork.height - height));
    const placed = {x, y, width, height};
    return {main: mostOverlap(monitors, placed), rect: placed};
}

function filerWindows(m) {
    const w = m.work;
    const sizes = [[1000, 700], [640, 420], [420, 320]];
    const out = [];
    for (const [fw, fh] of sizes) {
        const width = Math.min(fw, w.width), height = Math.min(fh, w.height);
        out.push({where: 'centre', x: w.x + trunc((w.width - width) / 2), y: w.y + trunc((w.height - height) / 2), width, height});
        out.push({where: 'top-left', x: w.x, y: w.y, width, height});
        out.push({where: 'top-centre', x: w.x + trunc((w.width - width) / 2), y: w.y, width, height});
        out.push({where: 'bottom-right', x: w.x + w.width - width, y: w.y + w.height - height, width, height});
    }
    return out;
}

let checks = 0;
for (const [layoutName, monitors] of Object.entries(LAYOUTS)) {
    for (const filerMonitor of monitors) {
        for (const filer of filerWindows(filerMonitor)) {
            for (const [fileName, file] of Object.entries(FILES)) {
                const size = previewSize(monitors, file);
                const label = `${layoutName}: Filer ${filer.width}x${filer.height} at ${filer.where} of ${filerMonitor.name}, ${fileName}`;
                // Fits every monitor's work area with room to spare, so it fits Filer's.
                for (const m of monitors) {
                    assert(size[0] <= m.work.width - 32 && size[1] <= m.work.height - 32,
                        `${label}: ${size.join('x')} does not fit ${m.name} work area ${m.work.width}x${m.work.height}`);
                }
                const {main, rect} = placeOverParent(monitors, filer, size);
                assert.equal(main.name, filerMonitor.name, `${label}: preview lands on ${main.name}`);
                const w = filerMonitor.work;
                assert(rect.x >= w.x && rect.y >= w.y && rect.x + rect.width <= w.x + w.width &&
                    rect.y + rect.height <= w.y + w.height, `${label}: preview leaves the work area ${JSON.stringify(rect)}`);
                // Centred on Filer unless the work area edge pushed it in.
                const centreX = rect.x + rect.width / 2, filerCentreX = filer.x + filer.width / 2;
                const pushedX = rect.x === w.x || rect.x + rect.width === w.x + w.width;
                assert(pushedX || Math.abs(centreX - filerCentreX) <= 1, `${label}: preview is not centred on Filer`);
                checks++;
            }
        }
    }
}

// Images keep their aspect ratio inside the smallest monitor's bounds.
const nick = LAYOUTS['ultrawide 1x + panel 1.25x'];
const photo = previewSize(nick, FILES['photo 6000x4000']);
assert.deepEqual(photo, [1092, 770], 'photo size on the ultrawide + 1.25x panel desk');
const tall = previewSize(nick, FILES['tall screenshot 824x1866']);
assert.deepEqual(tall, [412, 821], 'a tall screenshot is letterboxed to the panel height');
console.log(`Quick View placement: ${checks} Filer/monitor/file cases stay on Filer's monitor, centred and inside the work area`);
