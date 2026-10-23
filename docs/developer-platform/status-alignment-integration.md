# Mobile status alignment integration

Trim leading Unicode whitespace from the wall-clock label binding so the
single-digit hour padding does not move the visible time inward. Default
cutout-free clock placement to the left, retaining explicit left/right safe-area
placement from the layout manager. Use an 18px left floor while preserving larger
hardware insets. Add 1px battery end margin outside the lock-screen Presence
style to balance the visible status edges. Font styling is unchanged.

Patch 0048 targets the Mobile shell baseline through 0043. That baseline is not
yet integrated into public main. Keep the candidate unwired until its owning
package recipe is available; no unrelated baseline patches belong in this
change. Widget bounds and dock layout are separate changes.

Validation: zero-fuzz patch replay; native GTK3 preview checks for single- and
double-digit hours, minute updates, AM/PM and empty text. Rendered Mobile
preview showed near-symmetric visible edge insets and retained Home navigation.
The preview implementation is not the production clock binding. Full native
package builds, hardware cutouts, RTL and lock/unlock qualification remain
pending. Apply to the recorded baseline, build the shell and qualify these
states before updating composition pins.

This change does not establish typography parity between simulator rendering
and physical phones.

Pre-patch SHA256 values:

- src/top-panel.c: `d34da682cad341f3251f9a214b348ad9a27de06b0d16814894957a67e5396c76`
