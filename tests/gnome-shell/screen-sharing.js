// SPDX-License-Identifier: GPL-2.0-or-later
// What the packaged screen sharing picker must be (js/ui/lumaShare.js).
// Run: gjs -m screen-sharing.js path/to/lumaShare.js path/to/luma-share.css
//
// The module cannot be imported on its own -- it pulls in main.js and the
// whole shell -- so this reads the packaged source. That is the point: these
// are the promises the picker makes, checked against what actually shipped
// inside the gresource rather than against the tree it was built from.
import GLib from 'gi://GLib';
import System from 'system';

const read = path => new TextDecoder().decode(GLib.file_get_contents(path)[1]);
const source = read(ARGV[0]);
const css = read(ARGV[1]);

let failures = 0;
const check = (name, ok, detail = '') => {
    if (!ok) {
        failures++;
        printerr(`FAIL ${name} ${detail}`);
    }
};
const has = (name, text, where = source) => check(name, where.includes(text), text);
const hasnt = (name, text, where = source) =>
    check(name, !where.includes(text), text);

// ── The owner's interim answers ───────────────────────────────────────────
// Each is one constant. If one of these fails, the shipped behaviour is not
// the behaviour the handoff asked for, whoever changed it.
has('the pill replaces the Well mark',
    'KEEP_WELL_MARK_WHILE_SHARING = false');
has('the pill stays for the whole share', 'PILL_VISIBLE_MS = 0');
has('no choose-on-screen mode', 'OFFER_CHOOSE_ON_SCREEN = false');

// ── Nothing streams before Share, and nothing else is offered ─────────────
// The picker must not be able to start, stop or read a stream: the backend
// does that, after Share. Any Mutter ScreenCast call in here would mean the
// Shell could stream on its own.
hasnt('the picker never calls Mutter ScreenCast', 'org.gnome.Mutter.ScreenCast');
hasnt('the picker never records a monitor itself', 'RecordMonitor');
hasnt('the picker never records a window itself', 'RecordWindow');

// Only the portal backend may reach the picker.
has('only the portal backend may ask', 'org.freedesktop.impl.portal.desktop.luma');
has('the caller is checked by name owner', 'GetNameOwner');
has('a stranger is refused', 'ACCESS_DENIED');

// The answer names the choice and nothing else.
has('windows are returned by id', "new GLib.Variant('at', windows)");
has('screens are returned by connector', "new GLib.Variant('as', monitors)");

// ── The requester comes from the system ───────────────────────────────────
has('the icon comes from the app system', 'Shell.AppSystem.get_default()');
has('a site is named only from a trusted origin', "options['trusted-origin']");

// ── The card, as decided ──────────────────────────────────────────────────
has('600 wide', 'CARD_WIDTH = 600');
has('16 from the screen edges', 'SCREEN_INSET = 16');
has('56 requester icon', 'APP_ICON_SIZE = 56');
has('26 badge overlapping by 7', 'BADGE_OVERLAP = 7');
has('3 columns of windows', 'WINDOW_COLUMNS = 3');
has('2 columns of screens', 'SCREEN_COLUMNS = 2');
has('the source fills 92% of its tile', 'TILE_FILL = 0.92');
has('16:10 tiles', 'TILE_ASPECT = 10 / 16');
has('the same 14px scrim blur as the prompt', 'SCRIM_BLUR = 14');

// ── Reuse, not a second copy ──────────────────────────────────────────────
has('windows use the dock preview renderer',
    "import {PreviewThumbnail} from './lumaDockWindows.js'");
has('the edge and pill use Capture-hidden actors',
    "import {CaptureHidden, ShelfSurface} from './lumaCapture.js'");
has('the pill uses the one shelf placement helper',
    "import {placeShelfBar} from './lumaShelfSurface.js'");
has('monitors are named once, with Cast',
    "import {listDisplays} from './lumaCast.js'");
has('notifications follow Cast’s own setting', "'org.projectluma.cast'");
has('a screen preview clones the window group',
    'source: global.window_group');

// ── Keyboard and screen reader ────────────────────────────────────────────
has('previews are radios, or checkboxes for several',
    'multiple ? Atk.Role.CHECK_BOX : Atk.Role.RADIO_BUTTON');
has('the tabs are a tab list', 'Atk.Role.PAGE_TAB_LIST');
has('the options are switches', 'Atk.Role.TOGGLE_BUTTON');
has('Escape cancels', 'Clutter.KEY_Escape');
has('Space selects', 'Clutter.KEY_space');
has('arrows move the choice', 'Clutter.KEY_Right');
// Double-click shares by clicking the chosen tile again, with no event API:
// a button-press override is exactly what stops firing silently once St.Button
// handles clicks as gestures. The keyboard never reaches the click path, so
// Space selects and never shares.
hasnt('no button-press override', 'vfunc_button_press_event');
has('a pointer click goes through the click path',
    "tile.connect('chosen', () => this._clicked(tile))");
has('a second click shares only a single choice',
    'const again = !this._multiple && tile.chosen;');
has('Space selects', `case Clutter.KEY_KP_Space:
            this._choose(tile);`);

// ── What the evidence harness caught (tests/gnome-shell/screen-sharing) ──
// Each of these shipped in an earlier build of this patch and failed in a
// real headless session.
// The edges draw their violet in a child: a CaptureHidden actor's own
// background or border is painted in its paint node and reaches every
// screen stream; only children are held back.
has('the edges draw their violet in a child', "this.add_child(edgeFace('luma-share-screen-edge'))");
has('the window ring is a child too', "this.add_child(edgeFace('luma-share-window-edge'))");
hasnt('no edge is styled on the CaptureHidden actor itself',
    "super._init({style_class: 'luma-share-screen-edge'");
// layout.js rejects unknown chrome parameters; this one threw in
// SessionStarted, so a shared screen got neither edge nor pill.
hasnt('no input-region parameter, which this Shell rejects', 'affectsInputRegion');
// The title is balanced and centred, as the authentication prompt's is.
has('the title is balanced', 'this._balance(this._title, width);');
// Tiles are sized once on the stage; sizing earlier asked St for theme
// nodes it could not have and flooded the log with St-CRITICALs.
has('tiles are sized after they are on the stage', `row.add_child(tile);
            // Sized once it is on the stage`);

// ── The theme ─────────────────────────────────────────────────────────────
has('violet is the sharing colour', '#7d5cf0', css);
hasnt('sharing is never Capture’s red', '#d4595c', css);
has('the card follows the appearance mode',
    '.luma-share-card.luma-surface-dark', css);
has('the window edge is 2.5 violet with a 7 halo',
    'inset 0 0 0 2.5px #7d5cf0', css);
has('the screen edge is 3 violet inside the monitor', 'border: 3px solid #7d5cf0', css);
has('the chosen ring is violet',
    '.luma-share-tile-frame.luma-share-tile-chosen', css);
has('a 40 pill', '.luma-share-pill { height: 40px; }', css);
has('equal buttons at 42', 'height: 42px', css);

if (failures > 0) {
    printerr(`${failures} screen sharing check(s) failed`);
    System.exit(1);
}
print('screen sharing picker: PASS');
