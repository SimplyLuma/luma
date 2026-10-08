# SPDX-License-Identifier: GPL-3.0-or-later

Name:           luma-phosh
Version:        0.55.0
Release:        1.luma.46%{?dist}
Summary:        Luma native handheld renderer based on Phosh
License:        GPL-3.0-or-later
URL:            https://gitlab.gnome.org/World/Phosh/phosh
Source0:        phosh-0254669ff329754471a468d2674341d4871a92b3.tar.gz
Source1:        libgnome-volume-control-d2442f455844e5292cb4a74ffc66ecc8d7595a9f.tar.gz
Source2:        libcall-ui-v0.1.5.tar.gz
Source3:        90-luma-phosh.conf
Patch0:         0001-luma-handheld-home-dock-search.patch
Patch1:         0002-luma-status-order-search-purpose.patch
Patch2:         0003-luma-quiet-session-handoff.patch
Patch3:         0004-luma-fold-quick-options-before-first-frame.patch
Patch4:         0005-luma-publish-first-complete-home-frame.patch
Patch5:         0006-luma-commit-initial-folded-drag-state.patch
Patch6:         0007-luma-presence-session-lock.patch
Patch7:         0008-luma-theme-native-lockscreen.patch
Patch8:         0009-luma-foreground-app-status-surface.patch
Patch9:         0010-luma-semantic-status-surfaces.patch
Patch10:        0011-luma-wallpaper-status-continuity.patch
Patch11:        0012-luma-notification-system.patch
Patch12:        0013-luma-responsive-quick-options-dismiss.patch
Patch13:        0014-luma-notification-quick-options-fixups.patch
Patch14:        0015-luma-notification-lifecycle.patch
Patch15:        0016-luma-shared-search-client.patch
Patch16:        0017-luma-physical-lock-carousel.patch
Patch17:        0018-luma-single-owner-touch-drawers.patch

ExclusiveArch:  aarch64

BuildRequires:  gcc
BuildRequires:  meson
BuildRequires:  pam-devel
BuildRequires:  pkgconfig(alsa)
BuildRequires:  pkgconfig(evince-document-3.0)
BuildRequires:  pkgconfig(evince-view-3.0)
BuildRequires:  pkgconfig(fribidi)
BuildRequires:  pkgconfig(gcr-3)
BuildRequires:  pkgconfig(gio-2.0)
BuildRequires:  pkgconfig(gio-unix-2.0)
BuildRequires:  pkgconfig(glib-2.0)
BuildRequires:  pkgconfig(gmobile)
BuildRequires:  pkgconfig(gnome-bluetooth-3.0)
BuildRequires:  pkgconfig(gnome-desktop-3.0)
BuildRequires:  pkgconfig(gobject-2.0)
BuildRequires:  pkgconfig(gsettings-desktop-schemas)
BuildRequires:  pkgconfig(gtk+-3.0)
BuildRequires:  pkgconfig(gtk+-wayland-3.0)
BuildRequires:  pkgconfig(gtk4)
BuildRequires:  pkgconfig(gudev-1.0)
BuildRequires:  pkgconfig(libadwaita-1)
BuildRequires:  pkgconfig(libcallaudio-0.1)
BuildRequires:  pkgconfig(libecal-2.0)
BuildRequires:  pkgconfig(libedataserver-1.2)
BuildRequires:  pkgconfig(libfeedback-0.0)
BuildRequires:  pkgconfig(libhandy-1)
BuildRequires:  pkgconfig(libnm)
BuildRequires:  pkgconfig(libpulse)
BuildRequires:  pkgconfig(libpulse-mainloop-glib)
BuildRequires:  pkgconfig(libsecret-1)
BuildRequires:  pkgconfig(libsystemd)
BuildRequires:  pkgconfig(libsoup-3.0)
BuildRequires:  pkgconfig(mm-glib)
BuildRequires:  pkgconfig(polkit-agent-1)
BuildRequires:  pkgconfig(qrcodegen)
BuildRequires:  pkgconfig(upower-glib)
BuildRequires:  pkgconfig(wayland-client)
BuildRequires:  pkgconfig(wayland-protocols)
BuildRequires:  systemd-rpm-macros

Requires:       luma-shell-state
Requires:       luma-search
Requires:       phosh = %{version}

%description
Builds the Project Luma handheld presentation from the exact Fedora Phosh
source release plus a bounded, reviewable downstream patch. Fedora's stock
Phosh remains installed as the rollback implementation.

%prep
%setup -q -n phosh-0254669ff329754471a468d2674341d4871a92b3 -a1 -a2
rm -rf subprojects/gvc subprojects/libcall-ui
mv libgnome-volume-control-d2442f455844e5292cb4a74ffc66ecc8d7595a9f subprojects/gvc
mv libcall-ui-v0.1.5 subprojects/libcall-ui
%autopatch -p1

%build
export CFLAGS="%{build_cflags}"
export CXXFLAGS="%{build_cxxflags}"
export LDFLAGS="%{build_ldflags}"
%{__meson} setup _build \
  --prefix=/opt/luma/phosh \
  --bindir=bin \
  --libdir=lib64 \
  --libexecdir=libexec \
  --datadir=share \
  -Dphoc_tests=disabled \
  -Dbindings-lib=true \
  -Dsearchd=false
%{__meson} compile -C _build %{?_smp_mflags}

%install
DESTDIR=%{buildroot} %{__meson} install -C _build
rm -f %{buildroot}/opt/luma/phosh/lib64/libphosh-0.45.a
install -D -m 0644 %{SOURCE3} \
  %{buildroot}%{_userunitdir}/mobi.phosh.Shell.service.d/90-luma-phosh.conf

%check
test -x %{buildroot}/opt/luma/phosh/libexec/phosh
test -x %{buildroot}/opt/luma/phosh/bin/phosh-session
test "$(%{buildroot}/opt/luma/phosh/libexec/phosh --version)" = \
  "Phosh %{version} - A Wayland shell for mobile devices"
! grep -Fq '"${COMPOSITOR}" -v -S' \
  %{buildroot}/opt/luma/phosh/bin/phosh-session
grep -Fq 'Search this machine' src/ui/app-grid.ui
grep -Fq 'org.projectluma.Search1' src/app-grid.c
grep -Fq 'G_DBUS_PROXY_FLAGS_DO_NOT_AUTO_START_AT_CONSTRUCTION' src/app-grid.c
grep -Fq 'luma-search-results' src/ui/app-grid.ui
grep -Fq 'on_search_suggestion_clicked' src/app-grid.c
grep -Fq 'luma-slider-label' src/stylesheet/common.css
grep -Fq 'max-columns">2' src/ui/quick-settings.ui
grep -Fq 'max-columns">3' src/ui/quick-settings.ui
grep -Fq 'PHOSH_NOTIFICATION_LIFECYCLE_WAITING' src/notifications/notification.h
grep -Fq 'phosh_notify_manager_mark_all_seen' src/notifications/notify-manager.c
grep -Fq 'luma_tiling_setting' src/ui/quick-settings.ui
grep -Fq 'search_background_tap' src/ui/app-grid.ui
grep -Fq '<property name="has-frame">0</property>' src/ui/app-grid.ui
grep -Fq '<property name="widget">PhoshTopPanel</property>' src/ui/top-panel.ui
grep -Fq 'PHOSH_TOP_PANEL_BG_MAX_OPACITY 1.0' src/top-panel-bg.c
test "$(grep -c 'id="luma_actions_row"' src/ui/settings.ui)" -eq 1
grep -Fq 'gtk_container_remove (GTK_CONTAINER (self->box_bottom_half)' src/settings.c
grep -Fq 'gtk_box_reorder_child (GTK_BOX (self->box_settings)' src/settings.c
grep -Fq 'luma_banner_top_inset' src/notifications/notification-banner.c
grep -Fq 'PHOSH_NOTIFICATION_URGENCY_CRITICAL' src/notifications/notify-manager.c
grep -Fq 'org.project_luma.shell-state' src/app-grid.c
test "$(grep -c 'self->state = PHOSH_TOP_PANEL_STATE_FOLDED' src/top-panel.c)" -ge 1
grep -Fq 'publish_startup_on_first_frame' src/shell.c
# Readiness must wait for every shell layer surface, not just the top panel:
# releasing Presence on the quickest surface exposes a bare desktop.
grep -Fq 'luma_await_surface' src/shell.c
grep -Fq 'phosh_background_manager_get_backgrounds' src/shell.c
# ... and must still have a bounded failure path rather than stranding the user.
grep -Fq 'LUMA_HANDOFF_DEADLINE_MS' src/shell.c
# The session lock keeps phosh's own widgets and unlock logic and is themed
# to the Luma aesthetic. If these turn into widget surgery again, the reviewer
# should know the decision was reversed deliberately.
# The Luma rules must live in the sheets style-manager.c loads by name. In
# common.css they were silently outranked and nothing on the lock was themed.
grep -Fq 'Luma lockscreen' src/stylesheet/adwaita-dark.css
grep -Fq 'Figtree' src/stylesheet/adwaita-dark.css
grep -Fq 'Luma lockscreen' src/stylesheet/adwaita-hc-light.css
# The passcode page must keep its clock and identity; losing them is what made
# the session lock read as a different product from the greeter.
grep -Fq 'luma_polish_unlock_page' src/lockscreen.c
grep -Fq 'lbl_unlock_clock' src/lockscreen.c
grep -Fq 'luma-unlock-clock' src/stylesheet/adwaita-dark.css
# Home and Presence must clear any foreground-app surface that remains active
# behind them, or the status area becomes an opaque band above the wallpaper.
grep -Fq 'phosh_top_panel_set_bar_surface (PHOSH_TOP_PANEL (priv->top_panel), NULL)' src/shell.c
grep -Fq 'state != PHOSH_HOME_STATE_FOLDED);' src/shell.c
! grep -Fq 'n_toplevels == 1);' src/shell.c
# The blank-padded 12h hour must be trimmed or the clock sits right of centre.
grep -Fq 'luma_trim_clock' src/lockscreen.c
! grep -Fq 'g_timeout_add_seconds_once (1, on_startup_finished' src/shell.c
grep -Fq '/run/luma-display/home-ready' src/shell.c
grep -Fq 'zphoc_draggable_layer_surface_v1_set_state (priv->drag_surface, drag_state)' src/drag-surface.c
# The four-dot passcode surface inside Phosh is deliberately not asserted here.
# Release 13 tried to hand-maintain a second Presence PIN page and was withdrawn
# after it caused GPU lockups; that parity returns through one shared component.
grep -Fq 'Swipe to unlock' src/ui/lockscreen.ui
grep -A2 -F 'GtkImage" id="lock-arrow"' src/ui/lockscreen.ui | \
  grep -Fq '<property name="visible">1</property>'
grep -Fq '#define PHOSH_TOP_PANEL_DRAG_THRESHOLD 0.18' src/top-panel.c
grep -Fq 'luma-lock-hint' src/stylesheet/common.css
# Foreground surface ownership follows the folded app state, never the hidden
# activation state of a toplevel behind Home.
! grep -Fq 'has_activated_toplevel' src/shell.c
grep -Fq 'TransientSurfaceChanged' src/shell.c
grep -Fq '#top-bar-bin.luma-surface-deep' src/stylesheet/common.css
grep -Fq '.phosh-home-bar.luma-surface-deep #powerbar' src/stylesheet/common.css
grep -Fq 'LUMA_BANNER_TIMEOUT_MS 8500' src/notifications/notification-banner.c
grep -Fq 'priv->notification_banners->len < 3' src/shell.c
grep -Fq 'STACK_CHILD_NOTIFICATIONS' src/settings.c
grep -Fq 'Nothing waiting. New alerts will collect here.' src/ui/settings.ui
grep -Fq 'font-family: "Figtree"' src/stylesheet/common.css
grep -Fq '<property name="animation-duration">280</property>' src/ui/lockscreen.ui
! grep -Fq 'luma_unlock_tap' src/lockscreen.c
! grep -Fq 'luma_unlock_tap' src/ui/lockscreen.ui
! grep -Fq 'settings_swipe' src/top-panel.c
! grep -Fq 'settings_swipe' src/ui/top-panel.ui
! grep -Fq 'brightness.swipe_gesture' src/lockscreen.c
! grep -Fq 'id="swipe_gesture"' src/ui/lockscreen.ui

%files
%license COPYING
/opt/luma/phosh/
%{_userunitdir}/mobi.phosh.Shell.service.d/90-luma-phosh.conf

%changelog
* Tue Sep 01 2026 Project Luma <maintainers@projectluma.org> - 0.55.0-1.luma.46
- Remove the capture-phase panel swipe and lockscreen brightness swipe that
  competed with the two continuously tracked physical drawer gestures.

* Tue Sep 01 2026 Project Luma <maintainers@projectluma.org> - 0.55.0-1.luma.45
- Give the reversible lock transition exclusively to its native vertical
  carousel and remove the competing whole-surface tap recognizer.

* Tue Sep 01 2026 Project Luma <maintainers@projectluma.org> - 0.55.0-1.luma.44
- Replace the app-only handheld Search result path with the shared Luma Search
  service while retaining Phosh's native Search presentation and gestures.

* Tue Sep 01 2026 Project Luma <maintainers@projectluma.org> - 0.55.0-1.luma.43
- Record the shared notification lifecycle in the native Phosh model, rest
  timed-out banners without deleting them, and mark Quick Options records seen.

* Tue Sep 01 2026 Project Luma <maintainers@projectluma.org> - 0.55.0-1.luma.42
- Put the existing notification collection before connectivity by moving the
  actual direct child and leave media in the trailing Quick Options section.
- Remove a duplicate GtkBuilder account row, retain urgent service-owned
  records during Clear all, and pause heads-up expiry for accessibility focus.

* Tue Sep 01 2026 Project Luma <maintainers@projectluma.org> - 0.55.0-1.luma.41
- Label the native reversible lock carousel truthfully as Swipe to unlock and
  restore its upward symbolic motion cue without changing authentication.
- Let a shorter intentional upward drag dismiss Quick Options while retaining
  its compositor-owned continuous motion and bottom-handle safety boundary.

* Tue Sep 01 2026 Project Luma <maintainers@projectluma.org> - 0.55.0-1.luma.40
- Add the native top-safe three-banner stack and preserve ordinary records when
  their heads-up timeout moves them into the Quick Options collection.
- Put Notifications before connectivity, keep the shade open for actions, and
  apply the shared Figtree notification tokens and compact empty state.

* Tue Sep 01 2026 Project Luma <maintainers@projectluma.org> - 0.55.0-1.luma.39
- Withhold foreground-app semantic surface classes whenever Home is unfolded,
  including the single-toplevel return-to-Home path.

* Tue Sep 01 2026 Project Luma <maintainers@projectluma.org> - 0.55.0-1.luma.38
- Let the shared wallpaper continue behind the status region on Home and the
  native Presence lockscreen even while an application remains active behind

* Mon Aug 31 2026 Project Luma <maintainers@projectluma.org> - 0.55.0-1.luma.37
- Paint named foreground surfaces in Phosh's own priority sheet, project them
  onto both reserved system edges, and follow sender-lifetime call overrides.

* Mon Aug 31 2026 Project Luma <maintainers@projectluma.org> - 0.55.0-1.luma.36
- Add opt-in debug-domain evidence for semantic status projection without
  logging application content or user data.

* Mon Aug 31 2026 Project Luma <maintainers@projectluma.org> - 0.55.0-1.luma.35
- Permit a sole managed application to project its bounded status surface when
  Phoc omits ACTIVATED and the SSH launch path leaves Home state stale.

* Mon Aug 31 2026 Project Luma <maintainers@projectluma.org> - 0.55.0-1.luma.34
- Resolve semantic status surfaces from the newest toplevel while Home is
  folded when Phoc omits the optional foreign-toplevel ACTIVATED state.

* Mon Aug 31 2026 Project Luma <maintainers@projectluma.org> - 0.55.0-1.luma.33
- Project the activated application's bounded semantic status-surface metadata
  onto native Phosh chrome so the reserved bar joins the application canvas.

* Mon Aug 31 2026 Project Luma <maintainers@projectluma.org> - 0.55.0-1.luma.32
- Join the native status surface to the foreground AppKit canvas using Phosh's
  real activated-toplevel state while preserving wallpaper transparency on Home.

* Mon Aug 31 2026 Project Luma <maintainers@projectluma.org> - 0.55.0-1.luma.31
- Make Phosh's native lock manager the one normal handheld entry surface at
  cold boot and after suspend. The fresh greetd-owned session now supplies its
  actual Wayland socket and starts Phosh locked instead of attaching an
  independently lingering user manager to the old Presence compositor.

* Mon Aug 31 2026 Project Luma <maintainers@projectluma.org> - 0.55.0-1.luma.24
- Trim the clock string before displaying it. GNOME renders 12 hour time with a
  blank-padded hour, so before 10 o'clock it carries a leading space; centring
  the label centred that space too and pushed the digits 41px right of centre
  on both the quiet and the passcode page. Measured, not guessed: the date,
  which has no padding, sat dead centre alongside it.
- Deny the keypad horizontal expansion rather than requesting a width. A size
  request is only a minimum and GTK3 has no maximum, so the grid had still been
  taking the full 686px page width and stretching the keys into wide ovals.
- Let the four dots take the slack between the date and the keypad and centre
  themselves in it.

* Mon Aug 31 2026 Project Luma <maintainers@projectluma.org> - 0.55.0-1.luma.23
- Drop the avatar and account name from the passcode page, leaving the clock,
  the date and four dots, so the keypad and the emergency call can share the
  space beneath evenly.
- Stop the clock drifting right of centre. Negative letter-spacing shortens the
  layout's reported width, including after the final glyph, so centring the box
  left the glyphs visibly off centre; the date, which had no tracking, sat dead
  centre and gave it away.
- Constrain the keypad to the greeter's 288px. GTK3 has no max-width, so the
  homogeneous grid was taking the whole page width and stretching the keys into
  wide ovals.
- Keep the page clear of the bottom edge so the emergency call is not clipped,
  and give the quiet page's unlock hint room beneath it.

* Mon Aug 31 2026 Project Luma <maintainers@projectluma.org> - 0.55.0-1.luma.22
- Take the lock's type scale, keypad, avatar plate, dots and emergency pill
  from the greeter's own stylesheet rather than approximating them. The quiet
  clock drops from 78px to the greeter's 46px, which is what made it look
  oversized; the keys become the greeter's dark translucent 88x64 pills instead
  of pale white ones; the avatar gets the greeter's plate colour and 27px
  initial.
- Keep phosh's status label instead of hiding it. It is what reports "Checking"
  and a wrong passcode, so hiding it threw away every authentication error.
  It now starts empty and sits under the keypad as the greeter's feedback line.

* Mon Aug 31 2026 Project Luma <maintainers@projectluma.org> - 0.55.0-1.luma.21
- Stop the lock page flattening its own typography. A Luma rule selecting bare
  labels inside the unlock box scored (0,1,1) and outranked every .luma-* class
  at (0,1,0), so the clock, the date and the account name all rendered at 13px
  and the page read as one flat size. The caption that rule was written for is
  hidden now, so it is removed and the identity rules are scoped under the page
  where nothing can outrank them again.
- Lay the page out from each widget's own margins rather than a uniform 12px
  spacing, centre the labels explicitly so the clock is not drawn off to one
  side, and pin the emergency call to the bottom edge like the greeter's.

* Mon Aug 31 2026 Project Luma <maintainers@projectluma.org> - 0.55.0-1.luma.20
- Put the Luma lockscreen rules in the stylesheets phosh actually loads.
  style-manager.c loads adwaita-dark.css or adwaita-hc-light.css by name, so
  rules appended to common.css were outranked and the lock kept upstream's
  84px clock while the added passcode clock fell back to an unstyled default -
  the "giant on one screen, tiny on the other" the owner reported.
- Replace the variable-length entry readout with the greeter's four fixed dots,
  submit automatically once the passcode is complete, drop the "Enter Passcode"
  caption and the submit button, and put an emergency call in its place. The
  entry still holds the credential and the unlock path is untouched.

* Mon Aug 31 2026 Project Luma <maintainers@projectluma.org> - 0.55.0-1.luma.19
- Bring the session lock to parity with the greeter: pin the keypad to its
  natural height so its keys are round rather than stretched into ovals, hide
  the on-screen keyboard toggle so both keypads have the same shape, restyle
  the submit button to match the keys, and add the clock, date, avatar and
  account name the greeter shows. The clock is driven from the wall clock
  callback that already updates the info page. Additions to phosh's existing
  page, not a replacement for it.

* Mon Aug 31 2026 Project Luma <maintainers@projectluma.org> - 0.55.0-1.luma.18
- Give the session lock the Luma aesthetic through the stylesheet and keep
  phosh's own widgets, gestures and unlock logic. Reverses 1.luma.17, which
  hosted a separate Luma surface inside the lockscreen: it read as an imitation
  of the greeter rather than the product, and put a second implementation of one
  screen in the tree. Phosh's lockscreen has no avatar, account name or fixed
  four-dot row, so it is not pixel identical to the greeter and a stylesheet
  cannot make it so; adding those widgets is a separate decision.

* Mon Aug 31 2026 Project Luma <maintainers@projectluma.org> - 0.55.0-1.luma.17
- Host the shared Luma Presence surface on the session lock, so unlocking a
  running session and unlocking before one exists look like the same phone.
  The component is compiled from the single copy in the Luma tree rather than
  duplicated in a patch. It only collects digits: on submit the lockscreen
  writes into the entry phosh's own unlock path reads and calls the existing
  unlock_submit vfunc, leaving the PAM exchange untouched. The emergency pill
  is hidden here because phosh's lockscreen has no dialler behind it.

* Mon Aug 31 2026 Project Luma <maintainers@projectluma.org> - 0.55.0-1.luma.16
- Publish handoff readiness only once the wallpaper, top panel and home bar
  have all been configured by the compositor and two frame-clock cycles have
  elapsed on the home surface. Waiting on the top panel alone released Presence
  while the wallpaper was undrawn and the drag surfaces had not committed their
  folded state, producing a blank desktop and a flash of quick options between
  Presence and Home. A five second deadline reveals anyway and warns, so a
  surface that never configures cannot strand the user on the greeter.

* Mon Aug 31 2026 Project Luma <maintainers@projectluma.org> - 0.55.0-1.luma.15
- Watch the whole lock surface for the unlock tap in the capture phase instead
  of box_info in the bubble phase. box_info is largely covered by the scrolled
  notification list and sits inside both HdyCarousel's and HdyDeck's swipe
  trackers, so a bubble gesture there never saw an uncontested tap even once
  the gesture was kept alive. A press that travels stays with the carousel, so
  slide-to-unlock is unaffected, and a press a notification consumes cancels
  the tap rather than unlocking.

* Mon Aug 31 2026 Project Luma <maintainers@projectluma.org> - 0.55.0-1.luma.14
- Bind the lockscreen tap gesture as a template child. GtkBuilder finalizes
  template objects nothing references, so binding only its callback left the
  gesture destroyed immediately after construction: the quiet surface said
  "Tap to unlock" while no tap handler existed, and only a swipe worked.
- Withdraw 1.luma.13: patch 0008 duplicated a Luma PIN page inside Phosh and
  drove repeated a6xx GMU resume timeouts and GPU hangcheck lockups on the
  Fairphone 6, freezing the device. Release 13 is skipped permanently and
  0008 is no longer applied. Presence parity returns through one shared
  component rather than a second hand-maintained lock surface.

* Mon Aug 31 2026 Project Luma <maintainers@projectluma.org> - 0.55.0-1.luma.13
- Reveal Home only after the startup shield is down and its first frame painted
- Make the native session lock follow the full Luma Presence interaction model

* Sun Aug 30 2026 Project Luma <maintainers@projectluma.org> - 0.55.0-1.luma.12
- Make the authenticated session lock use the Luma Presence quiet state

* Sun Aug 30 2026 Project Luma <maintainers@projectluma.org> - 0.55.0-1.luma.11
- Commit the actual folded drag-surface state in the first shell frame

* Sun Aug 30 2026 Project Luma <maintainers@projectluma.org> - 0.55.0-1.luma.10
- Publish Home readiness to the persistent Presence compositor
- Drop the rejected draggable-surface protocol request

* Sun Aug 30 2026 Project Luma <maintainers@projectluma.org> - 0.55.0-1.luma.9
- Commit the folded quick-options state before the drag surface's first frame

* Sun Aug 30 2026 Project Luma <maintainers@projectluma.org> - 0.55.0-1.luma.8
- Publish compositor readiness from the first complete shell frame

* Sun Aug 30 2026 Project Luma <maintainers@projectluma.org> - 0.55.0-1.luma.7
- Fold quick options before the authenticated shell's first visible frame

* Sun Aug 30 2026 Project Luma <maintainers@projectluma.org> - 0.55.0-1.luma.6
- Keep routine authenticated compositor startup out of the handoff VT

* Sun Aug 30 2026 Project Luma <maintainers@projectluma.org> - 0.55.0-1.luma.5
- Start the authenticated greetd session unlocked to avoid a second login gate

* Sun Aug 30 2026 Project Luma <maintainers@projectluma.org> - 0.55.0-1.luma.4
- Keep home and quick-options status indicators in identical fixed order
- Mark Prairie search as free-form input so the keyboard does not duplicate suggestions

* Sun Aug 30 2026 Project Luma <maintainers@projectluma.org> - 0.55.0-1.luma.3
- Normalize launcher artwork without double canvases or universal strokes
- Match the Luma status geometry and full-screen quick-options hierarchy
- Add single-surface search, outside/back dismissal, and native shade dismissal

* Sun Aug 30 2026 Project Luma <maintainers@projectluma.org> - 0.55.0-1.luma.2
- Add native folders, actionable Prairie search suggestions, compact dark
  quick settings, labeled sliders, and mobile focus/gesture refinements

* Sun Aug 30 2026 Project Luma <maintainers@projectluma.org> - 0.55.0-1.luma.1
- Add the native four-column Luma Home model and four-item dock
- Separate Prairie search from the ordinary application drawer
- Retain Fedora Phosh as the exact rollback renderer
