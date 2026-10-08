# Screen sharing evidence harness (Shell patch 0149, luma-portal)

A headless Luma session with the real portal stack: PipeWire, WirePlumber,
xdg-desktop-portal, luma-portal and the Shell. `client.py` is a host
application asking `org.freedesktop.portal.ScreenCast` to share, exactly as a
browser does, so every request goes frontend -> luma-portal -> the Shell's
picker -> Mutter. `probe.js` drives the Shell with a virtual keyboard and
pointer and records what a person and a screen reader see; `measure.py` turns
the screenshots and the frames the application received into checks.

What it shows (evidence items in the handoff):

- **2** the picker from a portal request, light and dark, 100% and 200%, with
  a playing window whose preview changes between two shots 700 ms apart while
  still windows' previews do not; a resolved requester (Viola) and an
  unresolved host caller.
- **3** Windows and Screens, and a request that allows only screens (no
  Windows/Screens control).
- **4** keyboard only: arrows move the choice, Space selects and never shares,
  Enter shares, Control+Tab switches tabs, Escape cancels and the application
  is told so. A pointer double-click shares; one click only chooses.
- **6** the violet ring following a moved and resized window; the screen edge
  on screen; the pill's Stop ending the share in the Shell and the session in
  the application; and, from the frames the application received, no violet
  edge and no pill in a shared screen or window. A click through the screen
  edge reaches the window beneath it.

Run inside a Fedora container with the Shell and luma-portal RPMs, Figtree,
pipewire, wireplumber, xdg-desktop-portal, pipewire-gstreamer,
gstreamer1-plugins-base/-good, python3-gobject and python3-pillow, with this
directory at `/oracle/t`:

    bash /oracle/t/all.sh /oracle/out/ev

`ORACLE_OVERLAY=dir` loads `dir/js/ui` and `dir/data/theme` over the installed
Shell, to try a revised module on built RPMs without a build.

`MODE=exp` is a diagnostic, not part of `all.sh`: during a screen share it puts
squares in top chrome -- a CaptureHidden with its own background, one whose
background is a child, a plain widget and a bare shelf surface -- and counts
where the Shell's paints go. It is how the harness showed that a
CaptureHidden actor's own background and border reach every screen stream
(Clutter paints the actor's own node before `paint()`, which is all
CaptureHidden overrides), while its children are held back.
