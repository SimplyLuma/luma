# Luma notification ownership and lifecycle

**Status:** production integration contract, 2026-09-01

Applications publish through `org.freedesktop.Notifications`. GNOME Shell owns
the desktop record list, policy, D-Bus outcomes, heads-up presenter, waiting
indicator, and collection. Phosh owns the equivalent handheld boundary and
places its existing collection at the top of Quick Options. Neither renderer
adds a second bus API, polling helper, per-user CSS, or Android-owned surface.

Prairie Messages publishes directly from its existing event-driven native SMS
daemon. It uses `org.projectluma.Messages` as the desktop-entry and icon
identity, replaces the existing notification for a conversation, opens the
same responsive application at `sms:<address>` for body/Open/Reply activation,
and marks the authoritative native store read before asking the notification
server to close a Mark read action. Message text is never placed in process
arguments or logs. The exact `prairie-core-apps-0.1.0-1.luma.54.fc44.noarch`
package is pinned once for desktop and consumed by the FP6 composition path.

Both native renderers preserve verified source identity, notification and
replacement ID, sanitized summary/body, icon/image, urgency, actions,
timestamp, transient/resident hints, and protocol close reason. The shared
semantic lifecycle is `Queued → Arriving → Waiting → Seen → Acted | Dismissed
| Expired | Replaced`.

Heads-up timeout is presentation-only. GNOME Shell keeps the notification
unacknowledged and moves it to Waiting in its message list; Phosh destroys only
the banner layer surface and retains the same `PhoshNotification` in
`PhoshNotifyManager`. Mapping the desktop collection or opening Quick Options
marks unresolved records Seen without removing them. Explicitly
transient records may expire with reason 1. Swipe, close, and Clear all use
reason 2; producer withdrawal uses reason 3. Replacement mutates the existing
record and does not create a second banner or reorder the collection.

Desktop placement is bottom-right on the focused monitor's live work area with
a 14px inset. Its native presenter holds a bounded backlog and renders at most
three independent interactive cards with hover/focus-aware timers. Handheld
placement begins at the 54px safe/status boundary with
12px side insets, an 8px gap, newest first, and at most three banners. The
presenter derives a larger inset from Phosh's live usable area when necessary.
Opening Quick Options destroys only those banner surfaces; the same records
appear once in the collection before connectivity. Acting on a card keeps the
shade open. Clear all leaves critical service-owned call, alarm, and operation
records in place. Incoming calls and alarms remain specialized native
Phone/Clock surfaces.

## Honest gates

- Package compilation requires the canonical Fedora x86_64/AArch64 builders.
- Physical FP6 touch arbitration, haptics, suspend/wake, lock privacy,
  call/alarm behavior, large text, and screen-reader traversal remain open.
- GNOME's date-menu message list is the current desktop resting tray. A wider
  shelf-native title preview remains gated on the first-party shelf.
- The GNOME source patch now implements three simultaneous interactive cards,
  but compositor behavior, card ordering, focus traversal, shelf collision,
  fullscreen suppression, and multi-monitor placement remain native VM/device
  acceptance gates until the `.42` RPM is built and exercised.
- The two native shells currently preserve the same protocol semantics, but a
  persisted unresolved-record handoff across a GNOME/Phosh session or posture
  transition has not been demonstrated. One cross-renderer inbox therefore
  remains a release gate rather than a completed claim.
- The required model, close-reason, grouping, privacy, rate-limit, desktop
  interaction, mobile gesture, appearance, scaling, and accessibility suites
  still need native Fedora/VM/device harnesses; the repository tests currently
  protect source ownership, tokens, package pins, Messages protocol wiring,
  patch application, XML integrity, and deterministic action/update/close
  publishers.
