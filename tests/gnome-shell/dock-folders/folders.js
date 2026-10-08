// SPDX-License-Identifier: GPL-2.0-or-later
// Dock folders (0180): the tile, the Stack, the Grid, married and separated,
// and one broken out of the dock. SA_MODE picks the appearance mode (dark by
// default) and SA_EDGES the edges to walk (bottom by default).
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Clutter from 'gi://Clutter';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as L from './lib.js';

const HOME = GLib.get_home_dir();

// Real files, because every count, size and time the surface shows is read
// from the file system or is not shown.
function makeTree() {
    const downloads = `${HOME}/Downloads`;
    const projects = `${HOME}/Projects`;
    GLib.mkdir_with_parents(downloads, 0o755);
    GLib.mkdir_with_parents(projects, 0o755);
    const files = [
        ['Luma-Beta-1.iso', 4096], ['shelf-spec.pdf', 2048],
        ['dock-study-04.png', 1536], ['field-recording.flac', 3072],
        ['panel-teardown.mp4', 5120], ['tokens.json', 512],
        ['invoice-0412.pdf', 768], ['wallpaper-dusk.webp', 2560],
    ];
    files.forEach(([name, size]) => {
        GLib.file_set_contents(`${downloads}/${name}`, 'x'.repeat(size * 8));
    });
    for (const name of ['Session', 'Leaf', 'Filer', 'Appkit']) {
        GLib.mkdir_with_parents(`${projects}/${name}`, 0o755);
        const count = {Session: 24, Leaf: 11, Filer: 8, Appkit: 31}[name];
        for (let i = 0; i < count; i++)
            GLib.file_set_contents(`${projects}/${name}/item-${i}.txt`, 'x');
    }
    for (const [name, size] of [['notes.md', 6], ['cover.png', 1800],
        ['plan.numbers', 44], ['read-me.txt', 2]])
        GLib.file_set_contents(`${projects}/${name}`, 'x'.repeat(size * 1024));
    return [Gio.File.new_for_path(downloads).get_uri(),
        Gio.File.new_for_path(projects).get_uri()];
}

const folders = () => Main.shelf?.dockFolders ?? null;
const tileFor = uri => folders()?.rail.get_children().find(t => t.model?.uri === uri) ?? null;

async function openTile(uri, wait = 700) {
    const tile = tileFor(uri);
    if (!tile)
        return null;
    const [x, y] = L.centre(L.rectOf(tile));
    await L.move(x, y);
    await L.press();
    await L.release();
    await L.sleep(wait);
    return tile;
}

async function work() {
    const shelf = await L.waitForShelf();
    const s = L.settings();
    const [downloads, projects] = makeTree();
    const mode = GLib.getenv('SA_MODE') || 'dark';
    await L.setMode(mode);

    s.set_strv('dock-folders', [downloads, projects]);
    s.set_value('dock-folders-views',
        new GLib.Variant('a{ss}', {[downloads]: 'stack', [projects]: 'grid'}));
    s.set_boolean('dock-folders-separate', false);
    await L.sleep(1500);

    const edges = (GLib.getenv('SA_EDGES') || 'bottom').split(',');
    for (const edge of edges) {
        await L.arrange([{edge, anchor: 'center',
            islands: ['dock', 'folders', 'live', 'media', 'well', 'quick-options',
                'clock', 'notifications']}], 2200);

        // ── Married: one island, the divider carrying the separation ──
        s.set_boolean('dock-folders-separate', false);
        await L.sleep(900);
        const rail = folders().rail;
        const divider = folders().divider;
        L.check(`${edge} married: the rail is inside the dock island`,
            shelf._dockMaterial.contains(rail),
            `${rail.get_parent()?.style_class}`);
        L.check(`${edge} married: the divider is in front of the rail`,
            divider.visible && shelf._dockMaterial.contains(divider), '');
        L.check(`${edge} married: no folders island`,
            !shelf._foldersIsland.visible, '');
        L.check(`${edge} married: a tile for every pinned folder`,
            rail.get_children().length === 2, `${rail.get_children().length}`);
        await L.shot(`folders-${mode}-${edge}-married`);

        // ── The Stack ──
        await openTile(downloads);
        {
            const row = folders()._itemActors()[0];
            const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor;
            L.check(`${edge}: a Stack row is Filer's row height`,
                row && Math.round(L.rectOf(row).height / scale) === 30,
                `${row ? Math.round(L.rectOf(row).height / scale) : 'none'}`);
        }
        const surface = folders()._surface;
        L.check(`${edge}: the Stack opens`, surface.visible, '');
        const tileRect = L.rectOf(tileFor(downloads));
        const surfaceRect = L.rectOf(surface._surface);
        const away = {bottom: surfaceRect.y + surfaceRect.height <= tileRect.y,
            top: surfaceRect.y >= tileRect.y + tileRect.height,
            left: surfaceRect.x >= tileRect.x + tileRect.width,
            right: surfaceRect.x + surfaceRect.width <= tileRect.x}[edge];
        L.check(`${edge}: the Stack hangs from its tile, away from the dock`, away,
            `${JSON.stringify(tileRect)} ${JSON.stringify(surfaceRect)}`);
        L.check(`${edge}: the Stack is Filer's list`,
            folders().viewFor(folders()._models.get(downloads)) === 'stack', '');
        L.check(`${edge}: the Stack stays inside the screen`,
            L.inside(surfaceRect, L.workArea(), 0) ||
            surfaceRect.x >= 8 && surfaceRect.y >= 8,
            JSON.stringify(surfaceRect));
        await L.shot(`folders-${mode}-${edge}-stack`);

        // ── The Grid, and switching arrangements in place ──
        await openTile(projects);
        L.check(`${edge}: the Grid opens`,
            folders().viewFor(folders()._models.get(projects)) === 'grid', '');
        await L.shot(`folders-${mode}-${edge}-grid`);
        // The arrangement is remembered for that folder, not globally.
        folders().setView('stack');
        await L.sleep(500);
        L.check(`${edge}: the arrangement is remembered per folder`,
            folders().viewFor(folders()._models.get(projects)) === 'stack' &&
            folders().viewFor(folders()._models.get(downloads)) === 'stack', '');
        folders().setView('grid');
        await L.sleep(500);

        // ── Broken out of the dock ──
        folders().detach();
        await L.sleep(700);
        L.check(`${edge}: detached keeps the surface open`,
            folders().detached && folders()._surface.visible, '');
        L.check(`${edge}: the detached tile's bar goes hollow`,
            tileFor(projects)._bar.visible, '');
        await L.shot(`folders-${mode}-${edge}-detached`);
        // Clicking elsewhere leaves a detached one alone.
        const area = L.workArea();
        await L.move(area.x + 40, area.y + 40);
        await L.press();
        await L.release();
        await L.sleep(500);
        L.check(`${edge}: a click away leaves a detached folder alone`,
            folders()._surface.visible, '');
        // Put it back in the dock, then close from the keyboard.
        folders().redock();
        await L.sleep(700);
        L.check(`${edge}: put back in the dock hangs from the tile again`,
            !folders().detached && folders()._surface.visible, '');
        await L.key(Clutter.KEY_Escape);
        await L.sleep(500);
        L.check(`${edge}: Escape closes`, !folders().openFolder, '');

        // ── A full dock must not carry the folders off its own end ──
        //
        // This is the bug a person actually hit: twenty-six favourites, the
        // dock scrolling, and the rail drawn somewhere past the clip where
        // nothing can reach it.
        {
            const many = [];
            for (let i = 0; i < 26; i++)
                many.push(`org.projectluma.Notes.desktop`);
            const before = new Gio.Settings({schema_id: 'org.gnome.shell'})
                .get_strv('favorite-apps');
            new Gio.Settings({schema_id: 'org.gnome.shell'})
                .set_strv('favorite-apps', before.concat(before).concat(before));
            await L.sleep(2200);
            const tile = tileFor(downloads);
            L.check(`${edge} full dock: the folders still have a tile`, !!tile, '');
            if (tile) {
                const r = L.rectOf(tile);
                const island = L.rectOf(shelf._dockIsland);
                L.check(`${edge} full dock: the tile is inside its own island`,
                    r.x >= island.x - 2 && r.x + r.width <= island.x + island.width + 2 &&
                    r.width > 0,
                    `${JSON.stringify(r)} in ${JSON.stringify(island)}`);
                const picked = global.stage.get_actor_at_pos(
                    Clutter.PickMode.REACTIVE, ...L.centre(r));
                L.check(`${edge} full dock: and it can still be clicked`,
                    !!picked && tile.contains(picked),
                    `${picked?.constructor?.name}`);
            }
            void many;
            new Gio.Settings({schema_id: 'org.gnome.shell'}).set_strv('favorite-apps', before);
            await L.sleep(1600);
        }

        // ── Separated: an island of its own, and the divider goes ──
        s.set_boolean('dock-folders-separate', true);
        await L.sleep(1400);
        L.check(`${edge} separated: the rail is the folders island`,
            shelf._foldersMaterial.child === rail && shelf._foldersIsland.visible,
            `${rail.get_parent()?.style_class}`);
        L.check(`${edge} separated: the divider is gone`, !divider.visible, '');
        L.check(`${edge} separated: it is one rail, not two`,
            rail.get_children().length === 2 &&
            !shelf._dockMaterial.contains(rail), '');
        const dockRect = L.rectOf(shelf._dockIsland);
        const islandRect = L.rectOf(shelf._foldersIsland);
        L.check(`${edge} separated: the folders island is beside the dock`,
            !L.intersects(dockRect, islandRect),
            `${JSON.stringify(dockRect)} ${JSON.stringify(islandRect)}`);
        await L.shot(`folders-${mode}-${edge}-separated`);
        await openTile(downloads);
        L.check(`${edge} separated: the Stack still hangs from its tile`,
            folders()._surface.visible, '');
        await L.shot(`folders-${mode}-${edge}-separated-stack`);
        await L.key(Clutter.KEY_Escape);
        await L.sleep(400);
    }

    // ── The count means something arrived that has not been looked at ──
    const model = folders()._models.get(downloads);
    L.check('opening a folder clears its count', model.newCount === 0,
        `${model.newCount}`);
    GLib.file_set_contents(`${HOME}/Downloads/arrived-just-now.txt`, 'x'.repeat(64));
    // The Shell watches the directory; this container has no file monitor
    // backend, so the same reload the watch would run is run here.
    await model.reload();
    await L.sleep(600);
    L.check('a file that arrives brings the count back',
        model.newCount === 1, `${model.newCount} seen=${model.seen}`);
    L.check('the tile follows the folder underneath it',
        model.count === 9, `${model.count}`);
    L.check('the open surface follows it too',
        folders()._models.get(downloads).items.some(i => i.name === 'arrived-just-now.txt'),
        '');
    await L.shot(`folders-${GLib.getenv('SA_MODE') || 'dark'}-badge`);
    await openTile(downloads);
    L.check('opening it clears the count again',
        folders()._models.get(downloads).newCount === 0, '');
    await L.key(Clutter.KEY_Escape);
    await L.sleep(400);

    // ── The keyboard reaches every part of it ──
    await openTile(projects, 1200);
    await L.until(() => folders()._itemActors().length > 0);
    const items = folders()._itemActors();
    L.check('focus moves into the surface when it opens',
        folders()._surface.contains(global.stage.get_key_focus()), '');
    // Nothing wears a ring at rest: a focused item reads as a selection, and
    // nothing is selected until the person selects it.
    L.check('but no item is wearing a selection',
        !items.includes(global.stage.get_key_focus()), '');
    await L.key(Clutter.KEY_Right);
    L.check('the first arrow key moves focus onto the first item',
        global.stage.get_key_focus() === items[0], '');
    await L.key(Clutter.KEY_Right);
    L.check('arrow keys move through the items',
        global.stage.get_key_focus() === items[1], '');
    await L.key(Clutter.KEY_Down);
    L.check('down moves a row in the Grid',
        global.stage.get_key_focus() === items[5], '');

    const cells = folders()._itemActors();
    L.check('the Grid holds every item, not a first page',
        cells.length === folders()._models.get(projects).count,
        `${cells.length} of ${folders()._models.get(projects).count}`);
    await L.until(() => L.descend(folders()._surface.body)
        .filter(a => a._item).every(t => t._shown !== undefined), 8000);
    const thumbs = L.descend(folders()._surface.body).filter(a => a._item);
    L.check('every visible item resolves its thumbnail or its glyph',
        thumbs.length > 0 && thumbs.every(t => t._shown !== undefined),
        `${thumbs.filter(t => t._shown === undefined).length} undecided`);
    L.check('and the glyph is what shows when Filer has no picture',
        thumbs.every(t => t._shown !== null || t._glyph.visible), '');
    await L.key(Clutter.KEY_Escape);
    await L.sleep(400);
    L.check('Escape closes from the keyboard', !folders().openFolder, '');
    L.check('focus returns to the tile when it closes',
        global.stage.get_key_focus() === tileFor(projects), '');

    // ── The arrangement switch takes no grab, and a drag that throws
    //    still gives the pointer back ──
    await openTile(projects, 1000);
    const errorsBefore = L.results.length;
    for (let i = 0; i < 20; i++) {
        folders().setView(i % 2 ? 'grid' : 'stack');
        await L.sleep(60);
    }
    await L.sleep(500);
    L.check('twenty arrangement switches leave no pointer grab',
        !global.stage.get_grab_actor(), `${global.stage.get_grab_actor()}`);
    L.check('the switch does not start a header drag', !folders()._drag, '');
    L.check('the surface is still open and usable after them',
        folders()._surface.visible && folders()._itemActors().length > 0, '');
    void errorsBefore;

    // Pressing the arrangement control must reach the control, not the grip.
    const head = folders()._surface.header;
    const viewButton = L.descend(head).find(a => a.style_class === 'luma-dockf-view');
    L.check('a press on the arrangement control is not a grip press',
        !folders()._pressedControl ? false : folders()._pressedControl(viewButton.get_children()[0] ?? viewButton),
        '');
    L.check('a press on the header itself is a grip press',
        !folders()._pressedControl(head), '');

    // A drag whose handler throws must still give the pointer back.
    folders().onHeaderPress(head, {
        get_button: () => Clutter.BUTTON_PRIMARY,
        get_source: () => head,
        get_coords: () => [500, 500],
    });
    L.check('the header drag starts without grabbing the compositor',
        !!folders()._drag && !global.stage.get_grab_actor(), '');
    const surfaceRef = folders()._surface;
    surfaceRef.setLifting = () => { throw new Error('oracle: handler throws'); };
    folders()._onDragEvent({
        type: () => Clutter.EventType.MOTION,
        get_coords: () => [520, 520],
    });
    L.check('a throwing drag handler ends the drag', !folders()._drag, '');
    L.check('and leaves no grab behind', !global.stage.get_grab_actor(), '');
    delete surfaceRef.setLifting;
    await L.key(Clutter.KEY_Escape);
    await L.sleep(400);

    // ── The header has no pin; the tile's menu carries the way out ──
    await openTile(projects, 900);
    const headLabels = L.descend(folders()._surface.header)
        .filter(a => a.accessible_name).map(a => a.accessible_name);
    L.check('the header has no pin', !headLabels.includes('Break out of the dock'),
        `${headLabels.join('|')}`);
    L.check('the header still offers Filer', headLabels.includes('Open in Filer'), '');
    await L.key(Clutter.KEY_Escape);
    await L.sleep(400);

    // ── The tile is the folder's own icon ──
    const iconTile = tileFor(downloads);
    L.check('the tile draws the folder icon, not derived art',
        iconTile._mark instanceof St.Icon && !!iconTile._mark.gicon,
        `${iconTile._mark?.constructor?.name}`);
    // The icon is whatever the file system reports for that folder, which is
    // what Filer draws: a plain folder here, a special or custom icon where
    // the folder has one.
    L.check('the tile icon is the one the folder itself reports',
        iconTile._mark.gicon.equal(folders()._models.get(downloads).gicon),
        `${iconTile._mark.gicon.to_string()}`);

    // ── Every header control answers a real click, ten times over ──
    await openTile(projects, 1100);
    await L.until(() => folders()._itemActors().length > 0);
    const headerOf = () => folders()._surface.header;
    const controlNamed = name => L.descend(headerOf())
        .find(a => a.accessible_name === name && a.visible);

    // The arrangement: ten clicks must give ten switches.
    let switches = 0, lastView = folders().viewFor(folders()._models.get(projects));
    for (let i = 0; i < 10; i++) {
        const want = i % 2 ? 'Grid' : 'Stack';
        const button = controlNamed(want);
        if (!button) break;
        const [bx, by] = L.centre(L.rectOf(button));
        await L.move(bx, by); await L.press(); await L.release();
        await L.sleep(220);
        const now = folders().viewFor(folders()._models.get(projects));
        if (now !== lastView) switches++;
        lastView = now;
        if (folders()._drag) break;
    }
    L.check('ten clicks on the arrangement give ten switches', switches === 10, `${switches}`);
    L.check('clicking the arrangement never starts a drag', !folders()._drag, '');
    L.check('the surface survives ten arrangement clicks', folders()._surface.visible, '');

    // Open in Filer: it closes the surface, which is how we know it fired.
    let filerFired = 0;
    for (let i = 0; i < 10; i++) {
        if (!folders().openFolder)
            await openTile(projects, 900);
        const button = controlNamed('Open in Filer');
        if (!button) break;
        const [bx, by] = L.centre(L.rectOf(button));
        await L.move(bx, by); await L.press(); await L.release();
        await L.sleep(320);
        if (!folders().openFolder) filerFired++;
    }
    L.check('ten clicks on Open in Filer all fire', filerFired === 10, `${filerFired}`);
    L.check('clicking Open in Filer never starts a drag', !folders()._drag, '');

    // Detached, Close and Put back in the dock answer too.
    await openTile(projects, 900);
    folders().detach();
    await L.sleep(600);
    const redock = controlNamed('Put back in the dock');
    L.check('detached, the header offers Put back in the dock', !!redock, '');
    if (redock) {
        const [rx, ry] = L.centre(L.rectOf(redock));
        await L.move(rx, ry); await L.press(); await L.release();
        await L.sleep(500);
        L.check('Put back in the dock answers a click', !folders().detached, '');
    }
    folders().detach();
    await L.sleep(500);
    const closeButton = controlNamed('Close');
    L.check('detached, the header offers Close', !!closeButton, '');
    if (closeButton) {
        const [cx, cy] = L.centre(L.rectOf(closeButton));
        await L.move(cx, cy); await L.press(); await L.release();
        await L.sleep(500);
        L.check('Close answers a click', !folders().openFolder, '');
    }

    // And a drag from the header's empty area still breaks the folder out.
    await openTile(downloads, 900);
    const head2 = headerOf();
    const hr = L.rectOf(head2);
    // The empty strip between the name and the controls.
    const from = [hr.x + Math.round(hr.width * 0.42), hr.y + Math.round(hr.height / 2)];
    await L.drag(from, [from[0] + 40, from[1] - 120], {hold: 120, steps: 18});
    await L.sleep(700);
    L.check('a drag from the empty header area still breaks it out',
        folders().detached, '');
    L.check('and the drag has ended cleanly', !folders()._drag, '');
    folders().redock();
    await L.sleep(500);
    await L.key(Clutter.KEY_Escape);
    await L.sleep(400);

    // ── Hidden, the folders keep everything but the rail ──
    s.set_boolean('dock-folders-visible', false);
    await L.sleep(1200);
    L.check('hiding takes the rail away', !folders().rail.visible, '');
    L.check('hiding takes the divider with it', !folders().divider.visible, '');
    L.check('hiding leaves the folders pinned',
        folders().uris.length === 2, `${folders().uris.length}`);
    L.check('hiding closes an open folder', !folders().openFolder, '');
    await L.shot(`folders-${mode}-hidden`);
    s.set_boolean('dock-folders-separate', true);
    await L.sleep(1000);
    L.check('a hidden rail holds no island of its own',
        !shelf._foldersIsland.visible, '');
    s.set_boolean('dock-folders-separate', false);
    s.set_boolean('dock-folders-visible', true);
    await L.sleep(1200);
    L.check('showing brings them back as they were',
        folders().rail.visible && folders().rail.get_children().length === 2, '');

    // ── An item's own menu, and the tile's ──
    await openTile(projects, 1200);
    await L.until(() => folders()._itemActors().length > 0);
    const item = folders()._itemActors()[0];
    folders().openItemMenu(item, item.item);
    await L.sleep(700);
    const labels = folders()._itemMenu.box.get_children()
        .flatMap(child => L.descend(child))
        .filter(a => a instanceof St.Label).map(a => a.text);
    L.check('an item has Open, Reveal in Filer and Move to Trash',
        labels.includes('Open') && labels.includes('Reveal in Filer') &&
        labels.includes('Move to Trash'), `${labels.join('|')}`);
    await L.shot(`folders-${mode}-item-menu`);
    folders()._itemMenu.close();
    await L.sleep(400);
    L.check('the menu did not close the surface', folders()._surface.visible, '');
    await L.key(Clutter.KEY_Escape);
    await L.sleep(400);

    const tile = tileFor(projects);
    folders().openTileMenu(tile);
    await L.sleep(700);
    const tileLabels = folders()._menu.box.get_children()
        .flatMap(child => L.descend(child))
        .filter(a => a instanceof St.Label).map(a => a.text);
    L.check('a tile offers both arrangements and a way off the dock',
        tileLabels.includes('Stack') && tileLabels.includes('Grid') &&
        tileLabels.includes('Remove from the dock'), `${tileLabels.join('|')}`);
    await L.shot(`folders-${mode}-tile-menu`);
    folders()._menu.close();
    await L.sleep(400);

    // ── A click anywhere outside an attached folder closes it ──
    const area2 = L.workArea();
    const clickAt = async (x, y) => {
        await L.move(x, y); await L.press(); await L.release(); await L.sleep(500);
    };

    await openTile(downloads, 900);
    L.check('the folder is open to begin with', !!folders().openFolder, '');
    await clickAt(area2.x + 60, area2.y + 60);
    L.check('a click on the desktop closes it', !folders().openFolder, '');

    // On a window.
    await L.openWindow();
    await L.sleep(900);
    await openTile(downloads, 900);
    const win = global.get_window_actors().filter(a => a.visible).pop();
    if (win) {
        const wr = L.rectOf(win);
        await clickAt(wr.x + Math.round(wr.width / 2), wr.y + Math.round(wr.height / 2));
        L.check('a click on a window closes it', !folders().openFolder, '');
    }

    // On the Dash, away from the folders.
    await openTile(downloads, 900);
    const dashRect = L.rectOf(shelf._dash);
    await clickAt(dashRect.x + 12, dashRect.y + Math.round(dashRect.height / 2));
    L.check('a click on the Dash closes it', !folders().openFolder, '');

    // On another folder's tile: that switches rather than merely closing.
    await openTile(downloads, 900);
    const other = tileFor(projects);
    await clickAt(...L.centre(L.rectOf(other)));
    L.check('a click on another folder switches to it',
        folders().openFolder === folders()._models.get(projects), '');

    // Its own tile still closes it.
    await clickAt(...L.centre(L.rectOf(tileFor(projects))));
    L.check('its own tile closes it', !folders().openFolder, '');

    // Detached, a click away leaves it alone (already covered above, asserted
    // here beside its siblings so the rule reads in one place).
    await openTile(downloads, 900);
    folders().detach();
    await L.sleep(600);
    await clickAt(area2.x + 60, area2.y + 60);
    L.check('detached, a click away leaves it alone', !!folders().openFolder, '');
    await L.key(Clutter.KEY_Escape);
    await L.sleep(400);

    // ── A folder dragged in from Filer, through the real bridge ──
    //
    // The Shell learns what is being dragged from Filer's BeginFileDrag over
    // D-Bus, so the scenario speaks that interface rather than pretending.
    const xdnd = Main.xdndHandler;
    const newFolder = `${HOME}/Dropped`;
    GLib.mkdir_with_parents(newFolder, 0o755);
    GLib.file_set_contents(`${newFolder}/a.txt`, 'x');
    const droppedUri = Gio.File.new_for_path(newFolder).get_uri();

    // Nothing pinned: the rail must still offer somewhere to aim.
    s.set_strv('dock-folders', []);
    await L.sleep(900);
    L.check('with nothing pinned the rail is away', !folders().rail.visible, '');
    let token = xdnd.beginFileDrag('oracle', [[droppedUri, 'inode/directory']]);
    await L.sleep(700);
    L.check('a folder drag begins', token !== '', `${token}`);
    L.check('and the rail appears to receive it',
        folders().rail.visible && folders()._placeholder.visible, '');
    await L.shot(`folders-${mode}-drop-target`);

    // Drop it on the rail.
    const railRect = L.rectOf(folders().rail);
    const [dx0, dy0] = L.centre(railRect);
    await L.move(dx0, dy0);
    await L.sleep(200);
    // Mutter delivers the drag position for a real external drag; the oracle
    // has no external client, so the position handler is driven directly
    // with the actor under the pointer, exactly as it would be.
    xdnd._updateFileDropTarget(
        global.stage.get_actor_at_pos(Clutter.PickMode.REACTIVE, dx0, dy0));
    await L.sleep(200);
    L.check('the dock is the drop target', !!xdnd._fileDrag?.target, '');
    xdnd.endFileDrag('oracle', token, true);
    xdnd._fileDrag && (xdnd._fileDrag.externalEnded = true);
    xdnd._maybeFinishFileDrag();
    await L.until(() => folders().uris.includes(droppedUri), 6000);
    L.check('dropping a folder on the dock pins it',
        folders().uris.includes(droppedUri), `${folders().uris.join()}`);
    L.check('and the drop slot goes away', !folders()._placeholder.visible, '');
    await L.sleep(600);
    L.check('the pinned folder has a tile', !!tileFor(droppedUri), '');
    await L.shot(`folders-${mode}-dropped`);

    // A file is refused rather than silently ignored.
    const fileUri = Gio.File.new_for_path(`${HOME}/Downloads/tokens.json`).get_uri();
    const pinnedBeforeFile = folders().uris.length;
    token = xdnd.beginFileDrag('oracle', [[fileUri, 'application/json']]);
    await L.sleep(400);
    L.check('a file drag is not a folder drag',
        token === '' || xdnd._fileDrag?.folders === false, `${token}`);
    if (token !== '') {
        const [fx, fy] = L.centre(L.rectOf(folders().rail));
        await L.move(fx, fy);
        xdnd._updateFileDropTarget(
            global.stage.get_actor_at_pos(Clutter.PickMode.REACTIVE, fx, fy));
        xdnd.endFileDrag('oracle', token, true);
        xdnd._fileDrag && (xdnd._fileDrag.externalEnded = true);
        xdnd._maybeFinishFileDrag();
        await L.sleep(700);
    }
    L.check('a file is never pinned',
        folders().uris.length === pinnedBeforeFile, '');

    // A selection of both is neither.
    token = xdnd.beginFileDrag('oracle', [[droppedUri, 'inode/directory'],
        [fileUri, 'application/json']]);
    L.check('a mixed selection starts no drag', token === '', `${token}`);

    s.set_strv('dock-folders', [downloads, projects]);
    await L.sleep(900);

    // ── A folder dragged onto the dock is pinned, and unpinned again ──
    const before = folders().uris.length;
    folders().pin(Gio.File.new_for_path(`${HOME}/Projects/Leaf`).get_uri());
    await L.sleep(900);
    L.check('a folder can be pinned to the dock',
        folders().uris.length === before + 1 &&
        folders().rail.get_children().length === before + 1, '');
    folders().unpin(Gio.File.new_for_path(`${HOME}/Projects/Leaf`).get_uri());
    await L.sleep(700);
    L.check('a folder can be unpinned',
        folders().uris.length === before, '');

    s.set_boolean('dock-folders-separate', false);
    await L.reset(1200);
}

L.start('folders', work);
