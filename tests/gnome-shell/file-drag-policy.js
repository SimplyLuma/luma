// SPDX-License-Identifier: GPL-2.0-or-later
// Files dragged out of the Shell (lumaFileDrag.js, Mutter's MetaDataDrag):
// the rules in lumaFileDragModel.js, and the wiring that makes every file in
// a dock folder's surface draggable as if from Filer.
// Run: gjs -m file-drag-policy.js js/ui/lumaFileDragModel.js js/ui/lumaFileDrag.js \
//        js/ui/lumaDockFolders.js js/ui/lumaPresentation/launcherFolders.js js/ui/xdndHandler.js
const args = globalThis.ARGV;
const GLib = (await import('gi://GLib')).default;
const read = path => new TextDecoder().decode(GLib.file_get_contents(path)[1]);
const absolute = path => GLib.path_is_absolute(path) ? path
    : GLib.build_filenamev([GLib.get_current_dir(), path]);

let failures = 0;
function check(value, message) {
    if (value) {
        print(`ok - ${message}`);
    } else {
        print(`FAIL - ${message}`);
        failures++;
    }
}

const Model = await import(`file://${absolute(args[0])}`);
const drag = read(args[1]);
const folders = read(args[2]);
const panel = read(args[3]);
const xdnd = read(args[4]);

// The data: what Filer offers, in Filer's order.
check(JSON.stringify(Model.MIME_TYPES) === JSON.stringify([
    'text/plain;charset=utf-8', 'text/uri-list',
    'application/vnd.portal.filetransfer', 'application/vnd.portal.files']),
'a file drag offers what Filer offers, in its order');
check(Model.uriList(['file:///a%20b', 'file:///c']) === 'file:///a%20b\r\nfile:///c\r\n',
    'the URI list is one URI per line, CRLF');
check(Model.plainText(['/a b', '/c']) === '/a b\n/c', 'the text form is paths, one per line');
check(Model.portalKey('k1') === 'k1\0', 'the portal key ends with its NUL, as GTK writes it');
const offered = [...drag.matchAll(/drag\.(?:set_content|offer)\(Model\.(MIME_\w+)/g)].map(m => m[1]).slice(0, 4);
check(JSON.stringify(offered) === JSON.stringify(['MIME_TEXT', 'MIME_URI_LIST', 'MIME_PORTAL', 'MIME_PORTAL_OLD']),
    'the drag offers the types in that order');

// The actions: Filer's modifiers; a plain drop copies.
check(Model.actionForModifiers(0) === Model.Action.COPY, 'no modifier copies');
check(Model.actionForModifiers(1 << 0) === Model.Action.MOVE, 'Shift moves');
check(Model.actionForModifiers(1 << 2) === Model.Action.COPY, 'Control copies');
check(/set_actions\(Action\.COPY \| Action\.MOVE, Action\.COPY\)/.test(drag),
    'clients may copy or move, and get copy with no modifier held');

// The gesture: a click is never a drag.
check(!Model.travelled(5, 5, 8) && Model.travelled(6, 6, 8),
    'a press becomes a drag only past the threshold');
check(Model.DRAG_THRESHOLD === 8 && Model.LONG_PRESS_MS >= 300,
    'pointer threshold is GTK\'s; touch waits for a long press');
check(/actor\.fake_release\?\.\(\)/.test(drag),
    'a press that became a drag is no longer a click');
check(JSON.stringify(Model.hotspot(15, 30, 10, 10, 20, 40)) === '[0.25,0.5]' &&
    JSON.stringify(Model.hotspot(0, 0, 10, 10, 0, 0)) === '[0.5,0.5]',
'the drag image keeps the spot the pointer took hold of');

// The selection.
const items = ['a', 'b', 'c', 'd'].map(uri => ({uri}));
check(Model.dragSet(items[1], items, new Set(['a', 'c'])).length === 1,
    'an item outside the selection drags alone');
check(Model.dragSet(items[2], items, new Set(['c', 'a'])).map(i => i.uri).join() === 'a,c',
    'an item in the selection drags the selection, in the order shown');
check(Model.rangeBetween(items, 'd', items[1]).join() === 'b,c,d',
    'Shift selects from the anchor to the item');

// The image never hides what is under it.
check(/Shell\.util_set_hidden_from_pick\(this\.actor, true\)/.test(drag),
    'the drag image is hidden from pick');

// Every file in a dock folder's surface can be dragged.
check((folders.match(/this\._hookItemDrag\((row|cell), item\)/g) ?? []).length === 2,
    'Stack rows and Grid cells are drag sources');
check(/this\._controller\._hookItemDrag\?\.\(card, item\)/.test(panel),
    'the desktop folder panel\'s cards are drag sources');
check(/if \(item\.app \|\| !item\.file\?\.get_path\?\.\(\)\)\s*return;/.test(folders),
    'applications and non-local items are not file drags');
check(/Shell\.util_set_hidden_from_pick\(this\._surface, true\)/.test(folders) &&
    /Shell\.util_set_hidden_from_pick\(this\._surface, false\)/.test(folders),
'the surface lets the pointer through during a drag and takes it back after');
check(/this\._controller\.selectClick\?\.\(item\)/.test(panel) && /_clickItem\(item\)/.test(folders),
    'a modified click selects instead of opening');

// The Shell's own targets.
const dirAt = xdnd.indexOf('getFileDropDirectory');
const folderAt = xdnd.indexOf('getFolderDropTarget?.()');
check(dirAt > 0 && folderAt > dirAt,
    'a dock folder takes files before the dock offers to pin them');
check(/getFileDropDirectory\(\)\s*\{\s*return this\.model\.isApplications \? null/.test(folders),
    'the Applications folder takes no files');
check(/'org\.gnome\.Nautilus\.FileOperations2'/.test(xdnd) && /'MoveURIs' : 'CopyURIs'/.test(xdnd),
    'files dropped on a folder are copied or moved by Filer\'s own file operations');

if (failures)
    throw new Error(`file drag policy: ${failures} failed`);
print('file drag policy: ok');
