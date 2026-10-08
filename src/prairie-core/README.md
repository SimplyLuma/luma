# Prairie core applications

This source tree owns Project Luma's converged core applications and their
shared GTK4/libadwaita toolkit. Each application is one responsive binary on
desktop and mobile.

Layout and device behavior are intentionally separate:

- `AdwBreakpoint` responds to live available width. Compact is below 640
  logical pixels; 640 and above is the split desktop layout.
- `LUMA_PRESENTATION_MODE=windowed|fullscreen-mobile` controls window chrome.
- `LUMA_INPUT_MODE=pointer|touch` controls target sizing and input affordances.
- If the two explicit capability variables are absent, `LUMA_DEVICE_CLASS`
  provides the platform default. The immutable `/etc/luma-device-class`
  capability marker is the fail-safe when an external launcher drops the
  session environment. Width is never used to infer a phone.

The package includes all eleven responsive, Luma-owned applications from the
Prairie core-app guide. Shared platform services remain shared: EDS supplies
contacts/calendars/tasks, ModemManager supplies SMS and call operations,
PipeWire/GStreamer supply media, and systemd supplies persistent alarms. Luma
owns application storage, presentation, interaction, and error handling.

Production gates are tracked in `docs/research/prairie-core-app-readiness.md`.
A launcher or adaptive surface is never considered a finished application by
itself, and hardware-gated functionality is not claimed until it passes on the
physical FP6.

The physical FP6 currently has no camera media graph and no bidirectional call
audio route. Those app surfaces report the boundary honestly and stay inert.
