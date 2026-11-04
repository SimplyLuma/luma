# GNOME Control Center downstream patches

Luma carries two narrow presentation patches against Fedora 44's
`gnome-control-center-50.4-1.fc44` source package. It does not rename schemas,
change stored values, or remove either choice.

The patch:

- places **AM / PM** before **24-hour** in Date & Time;
- places **Natural** before **Traditional** in both mouse and touchpad settings;
- updates the natural-scroll bindings so each visual choice still writes the
  behavior named on screen.

The second patch adds a **Gestures** group to Touchpad Settings. It documents
GNOME Shell's native three-finger swipe-up and horizontal workspace gestures;
it does not reimplement, intercept, or pretend to configure those gestures.

The source RPM SHA-256 is
`fc442778b5bcd623ad4ab075f32bdb5cb201e4f66d072b1b5e96f16a3fe2aeb0`.
GNOME Control Center remains GNOME Control Center and retains its upstream
license and identity. See `docs/research/component-ledger.md` for the full
credit and maintenance record.

Settings shared-native corrective candidate (2026-09-05): patches 0003–0004
preserve all upstream panels, schema/backend bindings, About, Help, shortcuts,
search, and deep links. The shell opts into libadwaita's shared preferences,
navigation, titlebar and sibling-island roles. SearchEntry remains visible;
native query filtering, Enter, Escape, type-ahead and Ctrl+F are retained.
The existing 550sp adaptive breakpoint remains; row height is a minimum and
translated labels wrap. This requires the matching libadwaita stack through
0030 (canonical release 40). The wrapper supports the same aarch64/x86_64
source and retains its native build cache with --noclean.

Validation uses the maintained source and settings-parity test harnesses;
the integrated candidate pins use the matching preview20260905 suffix. These are corrective
candidates: no pixel-parity, physical-device or full lifecycle acceptance is
claimed. No Settings capability has been removed.

Follow-up5 adds0005: center the real navigation GtkGrid within the shared row,
and let the shared application identity expose the same three menu actions via
GtkApplication's native menu model. No panel/backend/schema changes are made.
The native search field consumes the corrected Adw41 shared class selector.

`0014-luma-background-activity-page.patch` (ADR-033) adds **Settings › Apps ›
Background Activity**: every app with a background agent, its switch, state,
memory, when it last ran, what it is for, when it starts, and Stop Now, read
from and changed only through `luma-background`
(`org.projectluma.Background1`). Turning off an essential app asks first and
says what the person will miss. The new page file is Luma-authored and, like
the files it joins, GPL-2.0-or-later. Release `1.luma.15.preview20260915.1`.

`0015-luma-sound-new-devices-and-network-outputs.patch` (ADR-032) is the
audio-policy Sound panel patch, renumbered from `work/audio-policy`'s 0014 and
regenerated on top of 0014 Background Activity (its hunks applied unchanged):
the Output Device list leaves out outputs PipeWire's discovery modules create
on their own unless one is in use, and a New Output Devices group binds the
`org.projectluma.audio-devices` settings (hidden without luma-audio-policy).

`0016-luma-cast-settings-page.patch` (ADR-034) adds **Settings › Displays ›
Cast**: Silence Notifications, Sound and Allow Screens That Connect Directly
bound to `org.projectluma.cast` (shipped by luma-cast), and Remembered Screens
from `org.projectluma.Cast1` with pin and Forget. `gnome-control-center display
cast` opens it directly; the Shell's Cast Settings item uses that. Without
luma-cast the page says casting isn't available.

0014, 0015 and 0016 together were release `1.luma.16.preview20260915.1`, which
supersedes .15. `1.luma.17.preview20260916.1` drops the "Ask Before Using" row
from 0015: luma-audio-policy 1.luma.3 never asks about output devices. The render harness is `tests/gnome-control-center/cast-settings/`.

`0017-luma-about-names-luma.patch` (ADR-040) makes **Settings › About** name
Luma: the logo comes from os-release `LOGO` (the spec no longer passes
`-Ddistributor_logo`; with `LOGO=luma-logo` About asks the icon theme for
`luma-logo-text-dark`, `luma-logo-text`, `luma-logo-dark`, `luma-logo` at 192 px,
all shipped by luma-logos in hicolor), the Support GNOME / Donate group is gone,
and System Details drops the GNOME Version and Windowing System rows. The OS
name is os-release `PRETTY_NAME` exactly ("Luma (Prairie, Beta 0, Nightly
20260916)") in About, System Details › OS Name and the copied report; the build
is the OS Build row (`IMAGE_VERSION`, else `BUILD_ID`, hidden when neither is
set). Release `1.luma.18.preview20260916.1` added it with a luma-update
`BootedVersion` overlay; `1.luma.19.preview20260917.1` removes that overlay.

`0018-luma-device-name-static-slug.patch`: **About › Device Name** sets the
pretty hostname to what was typed and the static hostname to its lower-case
slug, at most 63 bytes ("Nick’s ThinkPad" -> `nicks-thinkpad`); a name with no
host name form keeps the current static hostname instead of `localhost`. The
spec's `%check` runs upstream's `test-hostname`, which gains that case. Release
`1.luma.19.preview20260917.1`.

`0019-luma-about-legacy-os-release.patch`: images from before release names
(os-release `ID=luma` without `LOGO` or `VERSION_CODENAME`, `PRETTY_NAME="Luma
1.0"`) get `luma-logo` as the logo, and the OS name switches from `PRETTY_NAME`
to luma-update's `BootedName` when it starts "Luma (". The same name switch
applies without `IMAGE_VERSION` (luma-release's own os-release, "Luma (Prairie,
Beta 0)", layered on an older image; release `1.luma.21.preview20260917.3`). An
image's own os-release (`VERSION_CODENAME` and `IMAGE_VERSION`) shows
`PRETTY_NAME` and makes no D-Bus call. Release `1.luma.20.preview20260917.2`
added the patch.

The About render harness is `tests/gnome-control-center/about-release-name/`:
`container-render.sh DIR` (DIR/rpms holds the candidate Settings and the
image's toolkit, icon theme and luma-logos) renders About and System Details in
light and dark at 1x and 2x against a nightly os-release and a real hostnamed,
checks the hicolor icon cache lists the `luma-logo` names, and sets Device Name
to "ThinkPad", a name over 63 bytes and "Nick’s ThinkPad", printing what
hostnamed stored.

`0021-luma-mouse-trackpoint-page.patch`: the **Mouse & Touchpad** tab and
page GNOME calls "Pointing Stick" are "TrackPoint", and the page opens with
"The small pointer control in the middle of your keyboard." "Pointing Stick
Acceleration" is "Acceleration", the info text names the TrackPoint, and the
panel's search keywords gain "TrackPoint" and "Pointing stick". The page keeps
its controls and still appears only when a pointing stick is present. The
spec's `%check` greps the compiled page and desktop file for the new strings.
Release `1.luma.23.preview20260918.1`.

`0022-luma-dash-allow-free-placement.patch`: "Allow free placement" in the Dash panel's Shape group (shell-state `shelf-free-placement`, shown only when the schema has it), and the "Quick controls on right" switch removed: the order of the clock and the quick controls is set by arranging the islands.

`0023-luma-dash-span-and-float-follow-islands.patch`: without islands the Dash is one full-length bar per edge (Shell .119), so Span full edge is insensitive and says so, Float outer edges stays available, and the preview draws the full bar. Since .26 the switch then shows On ("Always on when islands are off") without storing it; the stored choice returns with islands.

`0026-mouse-Scroll-Speed-for-the-mouse-and-the-touchpad.patch`: a **Scroll Speed** row in the Mouse section and in the touchpad's Scrolling section, styled like Pointer Speed (Slow…Fast) with a reset button that is available when the speed is not the default. It binds `org.projectluma.peripherals.mouse`/`.touchpad` `scroll-speed` (shipped by Mutter 0019); the slider runs over log2 of the stored multiple so the middle is exactly 1.0, and the rows hide when the schema is missing. `%check` requires both rows in the built UI and runs the three mapping tests in `panels/mouse/test-scroll-speed.c`.

`0066-settings-use-shared-device-identity.patch` removes the two local copies of
phone detection. Window navigation and page anatomy now ask
`luma_ui_mobile_form_factor()`, including the composed handheld identity.
The old checks selected desktop navigation on an ARM handheld and requested
396 pixels from a 360-pixel Wayland output, causing a compositor protocol error.
The regression checks handheld identity, desktop identity and the explicit
presentation override. Runtime validation uses shared toolkit commit
35dc1549c6870c84d14e21b4b53af39f3b7dda26. Candidate package release
1.luma.40.mobile20261002.1 is not promoted: production RPM, matching platform
package, composition and physical-device gates remain open. The package builder
also now copies the already-declared 0060–0065 patches and this patch.

`0067-settings-open-handheld-destination-list.patch` keeps a normal handheld
launch on the Settings destinations. The live application previously pushed
Wi-Fi immediately after constructing its list-first window. Explicit panel
requests continue through the existing command-line route; desktop startup
still opens Wi-Fi. The actual ARM/Wayland launch is checked at 360 × 800,
without a fixture model or presentation override.

`0068-settings-native-session-identity.patch` lets the default Luma-native
application start independently of `XDG_CURRENT_DESKTOP`; its native service
adapters own availability. Explicit `LUMA_SETTINGS_LEGACY=1` retains the
upstream GNOME-only gate. The main Settings desktop entry follows that scope;
panel desktop entries, backend schemas and permission boundaries stay intact.
Candidate `1.luma.40.mobile20261002.2` adds a packaged `%check` which runs the
actual binary's `--list` across seven session identities in both modes and
checks the main launcher's visibility in four fresh processes. The same check
is available at `tests/gnome-control-center/session-identity/check.py`.
This remains disposable preview validation; composition, cold boot, upgrade,
rollback and physical acceptance remain release gates.

`0069-settings-retain-decoded-artwork.patch` keeps a bounded per-view cache of
local decoded artwork through unrelated owner updates. Both Appearance and Shelf
previously replaced wallpaper previews with blank pictures on every refresh.
The cache key includes resolved path, size, modification time and inode; replaced
files and requested resolutions invalidate it. Superseded decodes may warm the
cache, never paint an obsolete widget. Native tests exercise twelve actual page
rebuilds on both pages before decode callbacks and verify replaced-file/size
invalidation. The original decoder fails the new page-refresh test; six focused
native checks pass with the fix. Fresh packaged/display acceptance remains open.

`0070-settings-dark-first-appearance.patch` presents Dark before Light and
uses the mode ID for selected/accessibility state. Actual native appearance
checks retain saved Light and exercise Dark-to-Light switching; the previous
Light-first source fails the new order assertion. Release42 includes0069 and
0070 and passed all fourteen Luma native package suites against platform91.
The first-profile dark preference belongs to packaged desktop defaults;
opening Settings does not overwrite a saved light selection.

`0080-settings-retain-live-display-apply-bar.patch` keeps the native display
transaction's Apply/Keep controls visible. The editor fold handler previously
hid this bar immediately because its display exemption covered only fixtures.
The live display adapter still owns temporary preview, persistent Keep, and
safe rollback. Regression tracking is in
`docs/operations/user-reported-regressions.md`.

`0090-lumaui-tv-opt-in-and-panel-set.patch` (TV Mode, docs/developer-platform/tv-mode.md):
Settings opts in to the TV home (`X-Luma-TV`, `X-Luma-TV-Tile=org.gnome.Settings-tv`,
`X-Luma-TV-Presentation=tv`; the wide tile is `tools/wide-tile.py` of the v4 Settings icon,
installed as `hicolor/scalable/apps/org.gnome.Settings-tv.svg`). In TV posture
(`luma_ui_tv_posture()`, followed live) the sidebar lists the TV set in the TV mockup's
order: Wi‑Fi, Bluetooth, Display, Sound, Appearance, Idle & Sleep, Apps, Remotes, a
separator, Accessibility, Account, About. Every other page stays and still opens by deep
link or search; nothing is removed outside TV posture. The drawn page names its parts for
LumaUI (`luma_ui_set_part`: header, back, hero, group, card, row), which only TV posture
draws differently (LumaUI's floating island); the page wash is not drawn on a TV. Joining
a secured network: the password field gets the TV keyboard's "Join" action
(`luma_ui_set_keyboard_action`) and Enter joins; a scan's redraw waits while the password
is being typed; the network owner reports the outcome (`joinDone`/`joinFailed`) and, in TV
posture only, a refused password stops the attempt (no shell prompt over the TV), reopens
the field with the password selected and says so in a toast, and a join says "Connected
to <name>". In TV posture a join saves the password in the NetworkManager connection
(psk-flags 0; system-wide where the session may modify system connections), never in a
user secret agent (the login keyring is locked under automatic sign-in and its prompt would
cover the TV); re-joining a saved network with a new password updates that profile
(autoconnect on) instead of adding another. OK on a saved secured network opens it in place
(Join with the saved password or a new one, Forget). Updates has no Settings page (About links to Depot), so the island leaves it out.
Requires luma-developer-platform with LumaUI API level 6.

`0091-lumaui-tv-pages.patch`: the TV-only pages and rows, each bound to the owner that
already keeps the value. Idle & Sleep (`tv-idle`): "Show the idle view after"
(Automatic / 5 / 10 / 20 minutes / Never) is shell-state `tv-idle-delay`, followed live by
LumaUI's `TvIdle`; "Turn the TV off after" (1 / 4 / 8 hours / Never) is
`org.gnome.desktop.session idle-delay`. Remotes (`tv-remotes`): the connected input device
from BlueZ as the hero (name, battery), "Pointer speed" (the mouse speed Mouse & Touchpad
shows, read 0–10) and "Pair a remote" into Bluetooth. Display gains Fit › Screen edges
(0–10 %, shell-state `tv-screen-edges`, applied live by gnome-shell 0326) in TV posture
only. Rows without an owner are hidden: Idle view (only Glance exists), Wake with the
remote, HDMI-CEC, Match the video's frame rate, HDR, Hold OK for options, Sound's Night
mode. Deep links `gnome-control-center tv-idle|tv-remotes` open the pages in any posture.

`0092-lumaui-tv-saved-network-actions.patch`: in TV posture, OK on a saved secured network
grows its row with its actions: Connect (with the saved password), Change password (the row
shows a password field, focused with the TV keyboard's Join; empty is refused) and Forget
(the destructive question, a TV sheet on Cancel). Outside TV posture nothing changes.

`0093-lumaui-tv-account-appearance-sound.patch`: TV posture only. Account opens `tv-account`,
the Luma account as Luma Connect keeps it (`org.projectluma.Connect1` GetState, followed by
StateChanged; adapter `tv-account`): signed in, the hero is the account's name and email with
Syncing and devices and Sign out opening Luma Connect; signed out or locked, Sign in opens Luma
Connect (`account_qr_hook` is where QR sign-in goes); no daemon, the hero says Connect is not
installed. Appearance names its accents and wallpapers as LumaUI chips (`chips`, `swatch`,
`chip`), lays the wallpapers out as 200-unit 16:9 chips 16 apart, and leaves out Light and Dark
(the TV is always dark; the mockup's page has none). Sound's hero is the output itself (icon,
name, volume) like every TV hero, with the outputs (when there is more than one) and Volume as
rows. Requires LumaUI API level 8 (page parts `chips`, `chip`, `swatch`).

`0094-lumaui-tv-about-board-facts.patch`: About on boards that hostnamed names no vendor for
(a Raspberry Pi, most Arm boards) shows the device tree's model line as Model, and an Arm
processor without a model name in /proc/cpuinfo is named from its core part number and count
("Cortex-A76 × 4"), so the page has the desktop's rows in the desktop's order. x86 and
anything that already reports both are unchanged.

`0095-lumaui-tv-wifi-nearby-in-place.patch`: a performance fix for every posture. Each Wi-Fi
scan report rebuilt the whole Wi-Fi page: every widget was destroyed and drawn again, the page
re-laid out, and the focused row (and, on a TV, the remote's place) lost, several times a
minute. The page now keeps a signature of everything it drew apart from the Nearby list; while
that holds, a report updates the list in place, keyed by network name: rows that show the same
thing are kept, changed rows are redrawn where they stand, new ones added, gone ones removed,
the order follows the scan and the header's count is updated. Anything else (Wi-Fi on/off, the
joined network, a join in progress or its outcome, a notice) still draws the page afresh, so
what is shown is unchanged. Test: `/settings/view/nearby-in-place` (kept rows are the same
widgets; the page reads exactly as one drawn afresh).


`0096-lumaui-tv-page-words-and-one-display-fixture.patch`: in TV posture, the pages the island
lists (Wi-Fi, Bluetooth, Display, Sound, Appearance, Apps, Accessibility, About) take the TV
mockup's title and one-line description in their header ("Display", "The picture on this TV.");
the page's live summary no longer replaces that line on a TV. Subpages and every other posture
keep their own words. Display: a fixture may describe its displays as the live reader does
(each with its logical rectangle), so a fixture can be one TV on HDMI; such a fixture is checked
and drawn by the live rules (any number of displays) instead of v70's built-in-plus-external pair.
The fixture About page reads its machine (`sys.model`, `sys.mem`, `sys.cpu`, `sys.gpu`, `sys.disk`)
from the fixture when it has them, else v70's ThinkPad. In TV posture a header without a top control
stacks its title and line as one with does (a wrapping line measured unconstrained made the head
taller than its words).

`0097-lumaui-one-image-decode-per-file.patch`: image and wallpaper pictures
share one running decode per file and size (pages rebuild on every owner
report, and each rebuild used to start another decode of the same image), and
a picture up to 1024 px of a file over 1 MiB decodes from the freedesktop
thumbnail, made once if missing. On a Raspberry Pi 5 five threads decoded the
6016x3384 wallpaper for a 240x135 card; Settings Wi-Fi key-to-frame p95
736 -> 300 ms.

`0098-lumaui-page-wash-300px-and-hidden-on-tv.patch`: the page wash was a
page-sized GtkDrawingArea, so every redraw rasterized and uploaded a cairo
surface of the whole page (on TV an empty one); it is now 300 px tall and
hidden on TV. Pi 5 4K Settings: cairo upload share 10% -> 1%, page-switch
key-to-frame p50 ~200 -> 169 ms.
