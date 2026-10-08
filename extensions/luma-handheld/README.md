# Luma Handheld

This GNOME Shell extension supplies capability-specific interaction policy for
the shared Luma desktop. Only the mobile capability overlay enables it, and its
touch edge regions activate only while the primary display is portrait.

The extension observes GNOME Shell's stage-level captured touch stream at the
bottom, right, and top physical edges without consuming application events.
This avoids gesture-arbitration losses over fullscreen apps, the dock, and the
OSK, and requires no invisible actors over the dock, panel, or applications.
On handhelds, the wallpaper view is a real Home screen
with a four-column, touch-sized icon grid sourced from the same installed-app
catalog as the desktop. This automatically includes the same Luma,
freedesktop, and compatible Android launchers without maintaining a second
mobile app list. A short bottom swipe minimizes normal windows and opens Home.
A second upward dock gesture raises a separate rounded All Apps sheet: the
dock favorites remain at its top, followed by search and a vertically scrolling
four-column catalog. One-finger motion scrolls that catalog; a downward pull
first returns it to the top and then moves the whole sheet, which closes with
the same animated transition as Back. Home is a dedicated desktop sibling in
Shell's window stack above the selected wallpaper group and below every
application window; it is not a window, duplicated wallpaper, or open/close
overlay, and paints no replacement background color. All Apps remembers
whether Home or an application launched it; Back and downward dismissal return
to that origin. Going Home minimizes
normal windows across every workspace. Launch requests
temporarily suppress automatic Home recovery until the requested window takes
focus or the request definitively stops. Holding the bottom gesture opens
GNOME Shell's compositor-native running-window picker. Handheld posture keeps
that picker on one workspace and disables workspace swiping. The downstream
Shell layout arranges GNOME's existing compositor-owned `WindowPreview` actors
as a portrait card carousel: the focused card is centered, adjacent cards peek
at the edges, a horizontal one-finger pan browses, tapping resumes through the
native selection path, and an upward pan sends the native close request. It
does not clone, capture, or independently render application windows. Physical
FP6 testing rejected both an extension-owned live-clone deck and a lightweight
identity-card overlay because each introduced multi-second touch stalls; those
failed architectures remain removed. The existing private handheld session
interface exposes a non-sensitive
`ShowAppSwitcher` navigation action for hardware controls and physical
acceptance testing; it invokes the same native picker and exposes no window
titles or content. The continuous bottom gesture owns the touch sequence until
release: releasing before 420 ms commits Home, while remaining down past 420 ms
opens Activity View only when at least one application still exists. Closing
the final card exits the empty picker directly to Home. The same stage gesture
accepts starts across the measured dock and bottom safe area, while an
upward-dominant direction gate leaves horizontal dock scrolling untouched. It
also arbitrates touches over the native OSK, so the physical bottom edge remains
system navigation instead of typing through a gesture. Handheld posture also
uses exactly one workspace and disables the normal and overview workspace
swipe trackers. The user's original dynamic-workspace and workspace-count
settings are restored in landscape/docked posture, so desktop behavior is
unchanged. Top-left and
top-right swipes open notifications/calendar and Quick Settings respectively.
A right-edge inward swipe emits the conventional application Back accelerator.
Content-free session diagnostics count recognition, Home, Activity, cancel,
top, and right outcomes without recording coordinates, window identity, or
user content.

While the portrait policy is active it disables only GNOME's stock
bottom-edge OSK reveal recognizer, reserving that edge for Home/Overview while
leaving focus-triggered text input unchanged. The extension explicitly loads
its handheld chrome sheet so a live package update cannot be defeated by
Shell's one-time automatic stylesheet discovery. That sheet provides a compact
white, camera-cutout-safe panel; a full-width inset pill dock with overflow
fades; and same-frame keyboard-key pressed feedback. A native horizontal pan
gesture drives the dock's existing ScrollView, so touch swipes reveal clipped
favorites without maintaining a second dock implementation. Desktop chrome is
untouched because the extension is enabled only by the mobile capability
overlay.

The production keyboard is a Luma-owned Shell surface on Mutter's native input
path. It uses tall phone-sized keys, GNOME's bundled Shift/Delete/Enter
symbols, a real `?123`/`ABC` layer switch, same-frame character previews, and
normalized physical touch coordinates for the local AOSP LatinIME decoder.
A dedicated bottom safe-area band keeps typing above the screen edge: its left
control opens keyboard/input-method settings, its center owns Home/app-switcher
gestures, and its right control hides the keyboard. Shell first feeds key
values through Mutter's current input method. Some toolkits accept Mutter
virtual-key input without exposing an IBus text focus; for those clients, the
same local engine observes the current word and touch coordinates over its
private session interface while Mutter remains the only text-commit authority.
The Luma candidate row consumes that state, and Space applies only the bounded
correction operation returned by the engine. A stopped or restarting decoder
never blocks ordinary typing. Password, PIN, private, terminal, URL, email,
phone, and numeric purposes bypass both paths. The runtime does not require
Android, Java, Qt, KDE/KWin, or another compositor.

The mobile overlay defaults `org.gnome.desktop.session idle-delay` to 60
seconds. The extension owns a private session-bus display endpoint that sets
Mutter's panel power-save mode without suspending Fedora. The persistent
`luma-power-key-broker` is lifetime-bound to `luma-shell-session.target` and
reads only the seat-authorized `pmic_pwrkey` event node
and handles short releases through that endpoint. It remains available after
the output is powered down, unlike compositor key events, and is the sole short
press owner. Every press first reads Mutter's actual `PowerSaveMode`: wake
writes the panel on synchronously before reconciling Shell state, while sleep
uses an explicit lock-and-blank request. Neither path trusts cached posture or
sleep state. Because raw input does not count as compositor activity, wake also
defers idle-watch rearming for one configured timeout; otherwise Mutter's
already-expired private clock would immediately blank the panel again. Normal
touch or key activity during that grace period still resets Mutter's clock.
Logind's normal
power action remains inhibited for the graphical
session. A first press blanks the display, and a second wakes it while apps,
networking, notifications, and the Shell process continue running.
Phosh/Phoc retains its own native power-key inhibitor and display policy; the
Luma broker must not run in that session or race Phosh for the same PMIC key.

The right-edge Back gesture follows the focused application. Native Luma and
freedesktop applications receive the conventional Alt+Left navigation chord;
Waydroid applications receive one focused Escape event, which Android maps to
Back through its hardware-key input path. This keeps Android navigation inside
the same Luma gesture without displaying Android's launcher or system bar.

`luma-mobile-input-settings` exposes the same live idle-delay key and the
virtual-keyboard evaluation registry. Luma's native GNOME/Mutter plus LatinIME
path is the production candidate, with stock English (US) retained as rollback.
Installed candidates can expose a “Test temporarily” action;
Stevia and Squeekboard run as their real binaries in disposable nested Phoc
sessions, so closing a lab leaves Luma's production engine untouched.
Squeekboard is staged without its desktop autostart and system-alternatives
integration. Plasma Keyboard and
Maliit/Lomiri require an isolated KWin/Maliit lab, and candidates without their
native runtime remain visibly unavailable. While either the production OSK or
a lab OSK is visible, it owns the bottom edge and covers the dock. The
production keyboard's left safe-area control opens this settings surface.

It also maximizes normal application windows and keeps only the focused normal
window visible. Dialogs, the desktop, docks, and Shell surfaces are excluded.
No touch coordinates, key content, application content, or screenshots are
logged.
