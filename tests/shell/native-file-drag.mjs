import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const launches = [];
const warnings = [];
let serial = 0;
let fileType = 1;
let contentType = 'audio/mpeg';
let picked;
const app = {
    supports_uris: () => true,
    supports_files: () => true,
    get_supported_types: () => ['audio/mpeg', 'audio/ogg'],
    launch_uris_async: (uris, context, cancel, callback) => {
        launches.push(uris);
        queueMicrotask(() => callback(app, {}));
    },
    launch_uris_finish: () => true,
};
const actor = {
    _delegate: {getFileDropAppInfo: () => app},
    connect: () => 1, disconnect() {},
    get_parent: () => null,
    get_stage: () => true,
    contains: other => other === actor,
    add_style_pseudo_class() {},
    remove_style_pseudo_class() {},
};
const other = {get_parent: () => null};
const context = vm.createContext({
    Clutter: {PickMode: {REACTIVE: 0}},
    Gio: {
        Cancellable: class {cancel() {}},
        content_type_is_a: (type, mime) => type === mime,
        content_type_from_mime_type: mime => mime,
        FileType: {REGULAR: 1}, FileQueryInfoFlags: {NOFOLLOW_SYMLINKS: 1},
        File: {new_for_uri: () => ({
            query_info_async(attrs, flags, priority, cancellable, callback) {
                queueMicrotask(() => callback(this, {}));
            },
            query_info_finish: () => ({
                get_file_type: () => fileType,
                get_content_type: () => contentType,
            }),
        })},
    },
    GLib: {uuid_string_random: () => `token-${++serial}`,
        timeout_add_seconds: () => ++serial, source_remove() {}},
    Shell: {}, Signals: {EventEmitter: class {}}, DND: {},
    Main: {notifyError: (...args) => warnings.push(args)},
    global: {get_pointer: () => [50, 50],
        stage: {get_actor_at_pos: () => picked},
        window_group: {disconnectObject() {}},
        create_app_launch_context: () => ({})},
    _: s => s, console,
});
const source = fs.readFileSync(`${process.argv[2]}/js/ui/xdndHandler.js`, 'utf8')
    .replace(/^import .*;\n/gm, '').replace('export class XdndHandler', 'class XdndHandler');
vm.runInContext(`${source}\nglobalThis.Handler = XdndHandler;`, context);
function handler() {
    const h = Object.create(context.Handler.prototype);
    h._fileDrag = null; h._applicationDrag = null; h.emit = () => {};
    picked = actor; fileType = 1; contentType = 'audio/mpeg';
    return h;
}
const files = [['file:///tmp/song.mp3', 'audio/mpeg']];
const settle = () => new Promise(resolve => setImmediate(resolve));
let passed = 0;
async function test(name, fn) { await fn(); passed++; console.log(`PASS ${name}`); }
await test('empty, remote, directory, oversized selection rejected', () => {
    const h = handler();
    for (const input of [[], [['https://host/song.mp3','audio/mpeg']],
        [['file:///tmp','inode/directory']], Array(65).fill(files[0])])
        assert.equal(h.beginFileDrag(':1.9', input), '');
});
await test('hover and compositor leave cannot launch', () => {
    const h = handler(); h.beginFileDrag(':1.9', files);
    h._updateFileDropTarget(actor); h._onLeave(); assert.equal(launches.length, 0);
});
await test('foreign sender and stale token cannot commit', () => {
    const h = handler(); const token = h.beginFileDrag(':1.9', files);
    h._updateFileDropTarget(actor); h._onLeave();
    h.endFileDrag(':1.10', token, true); h.endFileDrag(':1.9', 'stale', true);
    assert.equal(launches.length, 0); assert.equal(h._fileDrag.sourceEnded, false);
});
await test('cancel wins and duplicate success cannot replay', async () => {
    const h = handler(); const token = h.beginFileDrag(':1.9', files);
    h._updateFileDropTarget(actor); h._onLeave(); h.endFileDrag(':1.9', token, false);
    h.endFileDrag(':1.9', token, true); await settle(); assert.equal(launches.length, 0);
});
await test('successful finish plus accepted target opens once', async () => {
    const h = handler(); const token = h.beginFileDrag(':1.9', files);
    h._updateFileDropTarget(actor); h._onLeave(); h.endFileDrag(':1.9', token, true);
    h.endFileDrag(':1.9', token, true); await settle();
    assert.deepEqual(Array.from(launches[0]), ['file:///tmp/song.mp3']);
    assert.equal(launches.length, 1);
});
await test('source finish before compositor leave also opens once', async () => {
    const h = handler(); const token = h.beginFileDrag(':1.9', files);
    h._updateFileDropTarget(actor); h.endFileDrag(':1.9', token, true);
    assert.equal(launches.length, 1); h._onLeave(); await settle(); assert.equal(launches.length, 2);
});
await test('leaving icon before finish does not open stale target', async () => {
    const h = handler(); const token = h.beginFileDrag(':1.9', files);
    h._updateFileDropTarget(actor); h._onLeave(); picked = other;
    h.endFileDrag(':1.9', token, true); await settle(); assert.equal(launches.length, 2);
});
await test('unsupported MIME is not a target', () => {
    const h = handler(); h.beginFileDrag(':1.9', [['file:///tmp/image.png','image/png']]);
    h._updateFileDropTarget(actor); assert.equal(h._fileDrag.target, null);
});
await test('mixed supported/unsupported selection is rejected as a unit', () => {
    const h = handler();
    assert.equal(h._fileDragMatchesApp(app, [...files, ['file:///tmp/x','image/png']]), false);
});
await test('replaced directory or symlink fails actual file recheck', async () => {
    const h = handler(); fileType = 2; await h._openDroppedFiles(app, files);
    assert.equal(launches.length, 2); assert.equal(warnings.length, 1);
});
await test('actual MIME changing after hover prevents Open', async () => {
    const h = handler(); contentType = 'image/png'; await h._openDroppedFiles(app, files);
    assert.equal(launches.length, 2);
});
await test('new drag invalidates earlier transaction', () => {
    const h = handler(); const old = h.beginFileDrag(':1.9', files);
    const current = h.beginFileDrag(':1.9', files); h.endFileDrag(':1.9', old, true);
    assert.equal(h._fileDrag.token, current); assert.equal(h._fileDrag.sourceEnded, false);
});
console.log(`${passed}/12 behavior checks passed against candidate XdndHandler`);
