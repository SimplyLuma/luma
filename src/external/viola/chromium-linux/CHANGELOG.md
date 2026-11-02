# Viola Linux changes

## 2026-11-02

- The address bar handle shows only while the bar is hidden. It is drawn as a
  notch of the window's bottom frame: the frame's colour, inverted corners and
  no outline. Hiding the bar sinks it into the handle; revealing it rises back
  out. With animations disabled both happen at once.

## 2026-10-08

- Restored the shared floating page toolbar, sidebar surfaces, address suggestions,
  downloads, sharing and Mini controls from the qualified native application.
  Native release 36 and independent app version 0.2.10.36 include the current
  pointer-and-rings artwork.
- Retained the Chromium engine, signed host service, application identity and
  profile paths. Added a regression covering bookmark and committed SQLite
  history preservation, including later changes inside the app profile.
