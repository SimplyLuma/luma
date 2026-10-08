# Shared native Charlie package completeness

Base source: `361d547019800eec23ed039522a3df08a4724739`, the retained
`v71/charlie` branch in `LumaApps/charlie-native-v71.bundle`. This branch retains
the native Apache-2.0 Python/GTK application; the remote main branch is the older
Flutter application and is not its runtime provider.

The normal package is architecture independent. Its canonical application and
desktop identities are `org.projectluma.Charlie` and
`org.projectluma.Charlie.desktop`. It requires Python3/GObject, GTK4.22,
libadwaita1.9, Luma Developer Platform, WebKitGTK6.0, Secret Service, Evolution Data
Server and GNOME Online Accounts. The running development phone has the required
GI namespaces and WebKitGTK2.52.5/EDS3.60.2/GOA3.58.1. Charlie is not installed,
so rollback of a later test install is removal of this package; user-created mail
data must be retained independently of package removal.

The source application imports its shared `window.py`, but the original Meson
install list omitted it. It also omitted the optional v71 fixture module used by
the existing development preview. Both now belong to normal package install.
The added installed-root import check uses actual GI/runtime modules, verifies
every module resolves inside the installed package tree and runs outside source
cwd. It does not initialize an account, send a message or start a mail agent.

Baseline evidence: the old static `test_contract.py` has15 failures/errors out
of28 cases on the unmodified v71 source. Those tests inspect former monolithic
classes and implementation strings. The old XTest shortcut harness also names
former window fields. These are preexisting package-check migration gates, not
evidence that normal checks have passed. Preserve their failures separately and
update genuine behavior tests to the shared v71 window before acceptance; do not
skip failed checks or claim a normal package was qualified from source imports.

Pending: server-only noarch package build, installed-root import regression
red/green, maintained backend and current native UI checks, physical empty-account
window rendering, clean installed launch and package removal/rollback. The same
source package serves desktop and mobile. No phone mutation was made by this
source change.


The canonical icon is the approved v71 `luma-v4-mail.svg`, copied without byte
changes to the normal `org.projectluma.Charlie` icon provider. Its SHA-256 is
`0bd4e8a2750f599cf686c79e78410d6f8a735b8226d9289312f90b88d797f209`.
All eight hicolor raster sizes are generated from this one SVG with the normal
librsvg package tool. Old raster exports are removed so theme resolution cannot
select older artwork at a particular size. No application identity is changed.

The original 28 source contracts had 15 failures/errors against the retained
v71 source: UI assertions still searched the former monolithic application
classes and obsolete pane measures. Package checks now keep the identity,
security, backend and privacy boundaries and exercise the actual installed
shared window for responsive list/detail presentation, sender alignment,
phone overflow actions, editable search/model updates, avatar geometry,
provider chooser and resident task behavior. The real XTest shortcut regression
uses the current native quick-reply field and mailbox selection. No authentication,
account creation, mail sync or sending occurs in these fixture checks.

The current phone's installed Developer Platform is older than this v71 source.
Development acceptance therefore uses the already authorized shared `c4f018914`
preview toolkit environment, exactly as the other preview apps do. This does
not claim clean composition with the older system toolkit; a normal toolkit
provider release remains the separate composition gate. The normal Charlie
RPM still owns its application, data, license, icons and backend. Package checks
must pass without skipping `%check` before it becomes an install candidate.

`tools/mobile-preview.py` is the source-owned development launch profile. It
validates the existing bundle's c4 ARM toolkit pin and reuses the shared
`run.py` environment entries. It then executes the normal
`/usr/bin/org.projectluma.Charlie`; only AppKit is added to `PYTHONPATH`, so Charlie
comes from the installed RPM. The original preview manifest and payload remain
untouched. A temporary user desktop may call this profile with
`DBusActivatable=false`, retaining the same desktop ID and normal package icon.
Its previous user desktop file or absence must be preserved in Charlie's audit
namespace for rollback. The helper is not installed as the normal system app
launcher and does not change account or background-service preferences.

The first normal ARM package check at `5139772409dee014a605b92898dc4f6f65b12e42`
passed all96 unit tests, metadata validation and all6 installed module imports,
then failed the XTest harness's obsolete `GtkEditable`-only reply selector.
A separate native diagnostic showed a correctly mapped phone detail view and
the actual c4 toolkit `GtkTextView` reply field. The shortcut harness now types
through XTest into that real field and reads its native `GtkTextBuffer`; the
strict no-archive/no-trash assertions remain. It focuses the retained open
thread's real mailbox row on return, without requiring a persistent selected-row
highlight that the phone presentation deliberately omits. Configuration/cache
are isolated with the fixture mailbox. This failure is preserved as test
migration evidence; full normal package acceptance remains pending rerun.

The same native diagnostic confirmed a500×828 GDK surface with490×818 window
content because of client-side decoration shadows. Responsive checks now wait
for the actual requested GDK surface size, then assert the real content width
reported to the shared adaptive owner. Phone/sidebar expectations use that
available content width. No production breakpoint or window geometry is changed.

Normal ARM checks at `1668c733a4ef69191416fa6d09a7b655f4724e37` passed all96
unit tests, installed imports and the real XTest safety regression. The installed
responsive test then exposed a production phone-to-desktop transition failure:
`ToastHost` inherits the shared native `GtkOverlay` and has no `remove()` API.
Charlie now releases that host's main child with `set_child(None)` and removes
an island's child through its existing box API. The same mailbox/conversation
widgets remain owned across both directions; the real resize test asserts their
parent and host-child identities at each actual native width. No mail backend,
account or toolkit source is changed. Full normal checks remain required.

The next normal ARM run at `e2ae4c23d082d14c5e52b873c38581e57a01ee6a`
passed the genuine shortcut check and native content ownership at360,500,1024
and1440px, then exposed a desktop-to-phone minimum-size trap. The diagnostic
showed a726px current desktop minimum: GTK clamped the requested500px surface
to726px and retained that default even after the sidebar folded and the minimum
fell to446px. Charlie now declares its existing1060px navigation visibility
rule through a real `AdwBreakpoint`. The normal360×420 minimum can therefore
govern the next configure, and the same shared width watcher updates the actual
list/detail presentation. The installed regression returns1440→500→360 on the
same open window and retains strict native surface and ownership assertions.
No toolkit source, forced fixture presentation, or mail behavior is changed.
Native breakpoint minimum-size behavior follows the upstream contract:
https://gnome.pages.gitlab.gnome.org/libadwaita/doc/1.9/class.BreakpointBin.html

The normal run at `110158925e6e87fede4d08fc4efb379b7f964635` passed the full
native resize/ownership sequence including1440→500→360. Its next search check
referenced the retained `BarSearch` item's hidden normal-row field. A separate
unchanged-product GTK diagnostic found the real mapped/focused search field
owned by `ActionCenter.open_search()`, and then exposed a user-facing defect:
filtering called `show_bar()`, closing search and replacing the focused field.
Charlie now retains the active native search field during model updates and
rebuilds the normal action row on the toolkit's public search-close callback.
The installed regression selects the actual mapped editable, verifies real
model filtering/clearing, and asserts that both keep the same mapped field and
native focus before explicit close restores the normal phone controls. The
diagnostic's108 exploratory assertions are not normal package acceptance;
complete unskipped package checks remain required for the new source.
