# Browser energy defaults

Two settings, shipped to every Chromium-family browser on the machine that
reads enterprise policy — Chromium, Chrome, Edge and Brave.

- `BatterySaverModeAvailability: 2` turns the browser's own Energy Saver on
  whenever the machine is unplugged, rather than waiting for the battery to
  fall below twenty per cent. It lowers the frame rate of background tabs and
  defers their timers, which is the same thing Luma does from outside for
  applications that cannot be told.
- `MemorySaverModeSavings: 1` lets the browser release tabs nobody has looked
  at for a long time. The tab stays on the strip and reloads when it is
  picked, so nothing is lost.

They are installed under `policies/recommended`, not `policies/managed`. A
recommended policy changes the default and leaves the control enabled, so a
person who wants either of these back can simply change it in the browser's own
settings and the browser will keep their choice. A managed policy would grey
the control out, which is not how Luma treats the person's own machine.

Removing the package removes the files and the browsers return to their own
defaults at the next start.

## What is deliberately not here

`BackgroundModeEnabled: false` would stop a browser staying resident after its
last window closes, which is a real saving. It is not shipped, because it also
silently stops the browser's notifications arriving when no window is open, and
some people rely on exactly that. A saving that quietly breaks something the
person was depending on is not a saving; it is a bug we chose.

If it is offered at all it belongs in Settings, off by default, worded so the
consequence is plain — something like "Close the browser completely when its
last window closes (you will stop getting its notifications)". Until that
exists, this file does not decide it on anyone's behalf.
