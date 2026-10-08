# Live Extensions

Live Extensions are temporary, system-rendered representations of meaningful
ongoing activity. They are not permanent widgets and do not execute arbitrary
application UI in the shell.

Supported preview categories are call, media, timer, navigation, event,
transfer, recording, installation, and generic. An extension supplies identity,
category, title, optional subtitle/progress/expiry, privacy, and at most three
semantic actions. The shell supplies every visual.

The same extension may render as a desktop top-bar capsule, tablet action strip,
phone compact activity, expanded sheet, or redacted lock-screen item. Apps do
not publish separate device versions.

Existing MPRIS players, calendar services, calls, notifications, transfers,
timers, and installers can be adapted into the same model by trusted system
bridges. Generic third-party publication is portal mediated.

## First integrated provider

The shared Prairie Calendar application publishes at most one `event`
extension: the nearest event beginning within 24 hours. It includes a
timezone-aware start and expiry, private title/subtitle text, no arbitrary UI,
and no privileged action. The broker verifies the Calendar process identity,
rejects stale or overlong publications, removes the item when its owner exits,
and substitutes a generic label while the session is locked.

GNOME Shell is the first system host. It authenticates through its owned D-Bus
name and executable identity, renders the compact desktop capsule itself, and
opens the verified publishing application when activated. Portrait Luma Go
does not reuse the center-panel capsule because that space is reserved for the
camera cutout; the mobile activity surface remains a separately tracked visual
acceptance gate over the same broker data.

The broker retains systemd mount-namespace and device hardening. It verifies the
Shell's owned D-Bus name and immutable executable when `/proc/<pid>/exe` is
available. Fedora hides that link across a hardened mount namespace, so that
specific constrained path requires the exact kernel-reported process name,
command vector, and dedicated `org.gnome.Shell@user.service` cgroup together,
and explicitly rejects an application scope. No caller-supplied identity is
trusted.
