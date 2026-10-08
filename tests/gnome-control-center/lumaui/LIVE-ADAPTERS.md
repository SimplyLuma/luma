# Settings live adapter ownership

This is an implementation map, not a completion claim. Fixture helpers never
open these services. Missing production operations must fail explicitly;
changing a fixture field is never a production action.

| Page family | Real owner / existing upstream reference | Status |
| --- | --- | --- |
| Wi-Fi / Network / hotspot | libnm, GNOME rfkill; panels/network | Async metadata adapter and all four private-bus lifecycle/service checks PASS; production view/actions/secrets/QR/backups open |
| Bluetooth | BlueZ / GNOME Bluetooth / rfkill; panels/bluetooth | Open |
| Appearance | LumaSurfacePolicy and GNOME background/interface preferences | Fixture policy bound; live writes/backups open |
| Shelf | org.project_luma.shell-state and native Shelf | Open; hiding/icon-size contract requested from S |
| Windows/workspaces | GNOME interface/mutter/wm/app-switcher; panels/multitasking | Open; native automatic tiling/gap/edge owner requested |
| Notifications | GNOME desktop notifications and Luma notification service | Open; banner-duration and preview contract needed |
| Search | GNOME search providers, Luma search and Tracker | Native folder adapter and private memory-store RMW/backup tests PASS; chooser roundtrip/cancel/late response PASS, immediate-opening cancellation critical remains. Production-window, provider/order/Ari integration open |
| Displays / Night Light | Mutter DisplayConfig and GNOME color service; panels/display | Fixture display draft/UI builds; real serial/apply/rollback open |
| Sound | GvcMixerControl and Luma AudioDevices1; panels/sound | Read-only Gvc adapter builds; isolated cancellation/error tests PASS. Successful real service/view, peaks and writes open; retain native output policy |
| Power | UPower, power profiles, GNOME power settings; panels/power | Native property contracts, async device/history and schema adapter checks PASS privately; partial fixture controls compiled. Live view/actions and diagram paint open |
| Mouse / keyboard | GNOME peripherals/input/keybinding owners | Partial fixture controls compiled; native schema catalogs implemented, live views/actions open |
| Accessibility | GNOME50.4 universal-access schemas and org.gnome.Orca.Service.ShowPreferences | Six partial fixture routes/12 native desktop+phone smokes and scoped control regressions PASS. Native read-only catalog implemented; bounded no-autostart Orca action private-bus success/cancellation/timeout/error tests PASS. Production view/write binding and exact Flash paint open |
| Printers | CUPS and existing printer helper; panels/printers | Open; no real print/delete in tests |
| Apps / defaults / media | GAppInfo, package owner, portal PermissionStore; panels/applications | Async registry/default reader builds and private registry tests PASS; partial Apps fixture list/18details has38 desktop/phone native smokes PASS. Live views, association edits, package sizes/permissions, launch/cache/uninstall open |
| Accounts | GOA and existing providers; panels/online-accounts | Open; real sign-in/account changes stay for Nick |
| Privacy/security | PermissionStore, GNOME privacy/lock/location, firmware/security service | Eight fixture routes/scoped controls compiled; both-width callback regression PASS. Native lock/privacy/location catalog construction/types/read snapshots PASS with isolated memory settings. Production views/PermissionStore/firmware/Bolt/housekeeping actions open; no sample security facts on real data |

0055 connects the actual CcScreenPage route to native Screen lock controls.
It reads SCREEN_LOCK/SESSION_IDLE/NOTIFICATIONS catalogs without loading a
fixture, preserves arbitrary existing durations, observes external changes and
uses scoped durable-backed edits. Production Back follows navigation.pop.
Private memory settings tests exercise desktop/phone menus, native key edits,
unchanged unrelated keys, orphan controls and pending-write root teardown;
the actual upstream page class and Back route also PASS. The outer CcWindow
and other Privacy pages remain unported; this is not visual acceptance.
| Wellbeing | GNOME wellbeing/break managers and history | Open |
| Sharing / remote / SSH | Native GNOME sharing/remote/login services and authorization helpers | Open; no development-time system mutation |
| Region/date/users | Locale/timedate/accounts services and upstream helpers | Open; preserve privilege/authentication boundaries |
| About | Actual OS/hardware metadata and owning-app launch APIs | Open |
| Luma account | Native Luma account/sync owner | Open |

Existing user-store edits need scoped read-modify-write and a first-kind
backup. A documented additive app-local backup store is authorized, but none
has been created against Nick's data. Read-only service access is authorized.
Preferences with no owning-component effect must never pretend an action
works. Overseer §5 exceptions are recorded for Nick; tests use private buses
and temporary stores only. No preview is installed before four conform PASS.

The folder adapter uses upstream Tracker3/Tracker `index-recursive-directories`
and `index-single-directories`. GTK FileDialog is the chooser route upstream
Settings already uses and the shared C SaveSheet uses for Other location
(`src/luma-platform/ui/luma-save-sheet.c`). The explicit binding removes fixture folder identities,
reads real settings and follows external changes. Fixture mode refuses a live
binding. Adding uses recursive indexing; disabling removes the selected folder
from either list, retaining unrelated entries and their original spelling.

Each first edited key is saved as an immutable 0600 GVariant backup containing
its schema, key and original value. The adapter synchronizes the new file and
directory, then reads the setting again. Production callers must supply the
contained Settings directory `~/.local/share/luma/settings/backups`. Failed,
corrupt or cancelled backups prevent writes. No backend starts automatically
in fixture mode. The production CcWindow is still the legacy window: the new
binding is draft wiring, not a completed live page. Its tests use a noninstalled
schema, memory settings and temporary backups; those tests passed in the isolated native runs.

Folder updates stage only their edited keys in a fresh delayed GSettings object
with the same injected backend. Disabling removes the selected folder from
both keys in one apply; failed staging is reverted before any backend writes.
The view's settings object stays in its original mode. Existing backups must
be regular private user-owned files and are synchronized before reuse. Pending
tests check paired-key notification consistency and refusal of nonprivate files.

Native read-only observers reuse CcObjectStorage's async proxy factory with
DO_NOT_AUTO_START. Contracts use the installed Color and UPower.PowerProfiles
identities and UPower's documented display-device path. Typed validation rejects
missing fields, nonfinite percentages/times and duplicate profile IDs. UPower
BatteryLevel remains explicit metadata: an approximate level must not be shown
as an exact percentage. Async device enumeration and one-day charge history now
reuse the native proxy factory. Calls refuse fixture mode, never auto-start the
daemon, use bounded timeouts and reject owner changes. History retains actual
timestamps, gaps and states; invalid values or order fail explicitly. Private-bus
positive/error/cancellation tests passed in the private native suite. Live rendering remains
open; this is not a completed Power or Night Light page.

The read-only preference observer snapshots requested values and their native
writable flags, coalesces external changes, and cancels pending publication on
stop/destruction. Explicit native catalogs follow the installed Night Light,
mouse/touchpad, input-source, notifications, workspace and Power panels. Native schema
factories reject every fixture marker, including an empty one; memory-only
injected objects remain available to tests. Schema/key/type/path failures are
reported, not defaulted to sample settings. These adapters built and their isolated tests passed;
their production view binding is still open. Power observations do not copy the
legacy panel's on-open timeout normalization writes: opening a page must not
silently rewrite a zero timeout or its sleep action.

Live folder binding is limited to its implemented page and rejects even an
empty fixture environment marker. Until another page's live adapter is wired,
navigation from this binding fails explicitly rather than rendering that page's
sample data. This guard is diagnostic and is not an inventory completion.

The native application reader snapshots visible GAppInfo entries and the six
upstream default content types on a worker. It refuses every fixture marker,
uses cancellation, and performs no launch, association update or uninstall.
Missing defaults/IDs stay absent; package/data sizes are not fabricated.
Its tests redirect all XDG data/config roots into a temporary registry and
assert that reading preserves its association file. These tests passed in the isolated native suite.

0052 adds an explicit asynchronous native preference edit API, separate from
read-only observer startup. Only selected keys can be changed; types/ranges,
writable flags, fixture isolation and canonical backup location are checked.
A durable immutable first-write record retains the schema/path/key, effective
value and original override presence. Each edit uses a fresh scoped transaction
on the same backend; unrelated keys and immediate observer mode are preserved.
Seven private preference cases and the retained search edit case PASS, including
corrupt/nonprivate/symlink backups and actual non-memory backend refusal.
This is tested adapter infrastructure, not completed production page wiring.

Privacy owner study: GNOME50.4 uses PermissionStore devices/camera and devices/microphone, and location/location (not a geolocation table). Lookup returns `(a{sas}v)`; SetPermission updates a single app. The port must preserve unrelated entries, opaque data and location timestamps rather than copying upstream full-dictionary rewrites that discard unexpected entries. GNOME diagnostics only exposes report-technical-problems as a boolean; the v70 ask/auto distinction needs an owning crash-report consumer contract. Bolt AuthMode changes retain polkit authorization; fwupd supplies actual security attributes. No such system operation is invoked by fixture tests.

0054 implements native PermissionStore reads and scoped existing-app edits.
Seven isolated private-bus cases PASS: immutable reads, unrelated edits,
location metadata, selected-record conflict, lost owner, fixture/cancellation
boundaries, bounded reads, native refusal and corrupt recovery. First-write
backups use the contained canonical app store; no real permission is edited.
Writes use the pinned unique owner and create=false. The native API has no
atomic compare/set; a competing edit after the final read remains possible.
After dispatch, cancellation cannot imply no edit; callers must refresh on
errors/timeouts. PermissionStore Changed subscriptions and live page binding
remain open, so this is not a completed Privacy or Apps page.
