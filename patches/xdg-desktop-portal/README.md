# xdg-desktop-portal

Fedora's xdg-desktop-portal, rebuilt by `scripts/packages/build-xdg-desktop-portal.sh`
from the SRPM pinned in `config/desktop/inputs.env` (desktop-components group
`xdg-desktop-portal`, both architectures).

- `0001-open-uri-open-package-files-in-the-system-installer.patch`: OpenURI
  and OpenFile show the application chooser for every file type except web
  and mail links and folders, until one application has been picked three
  times from the same caller (`src/open-uri.c`, `should_use_default_app()` and
  the threshold check in `handle_open_in_thread_func()`). Package files opened
  from a sandboxed file manager or a browser's downloads therefore always
  asked. `/usr/share/luma/open-directly.list` (`[Open Directly]`, content
  type = desktop id; shipped by luma-desktop-launcher-policy from
  `src/luma-desktop-launcher-policy/open-directly.list`) names types that open
  in a system handler without the chooser. While the named handler is the
  default for the type and its desktop entry's real path is in
  `/usr/share/applications` (the read-only OS image), the file opens in it
  directly. Entries anywhere else are ignored, and an application's `ask`
  option and the per-type always-ask setting still show the chooser. Luma
  lists the package types Valet installs; Valet reviews a package and asks
  before installing it. The patch adds OpenURI integration tests for the
  direct, ask, always-ask and untrusted-location cases. Luma-specific.
- `0002-request-run-disconnect-off-the-main-loop.patch`: backport of
  upstream a337e718 (flatpak/xdg-desktop-portal#2093). Every request took
  its lock for every peer leaving the bus before checking the sender, and
  OpenDirectory holds that lock while it calls FileManager1.ShowItems
  synchronously. A client disconnecting during the call parked the main
  loop; a file manager started by ShowItems that reads the Settings portal
  during startup then could not answer, and Show in Files took the full
  25 s D-Bus timeout. Drop when the SRPM is 1.23 or later.
- `0003-open-uri-open-files-in-their-chosen-default.patch`: outside
  open-directly.list, a file still opened the chooser even when its type has
  a default, so a screenshot or PDF double-clicked in Filer asked on a fresh
  install, and "Always Open" in luma-portal's chooser (which records the
  choice with `g_app_info_set_as_default_for_type()`) never took effect.
  While the type's default is named in `[Default Applications]` of a
  mimeapps list in `$XDG_CONFIG_HOME` or a system configuration directory
  (`/etc/xdg/gnome-mimeapps.list` from luma-desktop-launcher-policy), the
  file opens in it directly. GIO's fallback default and lists in data
  directories still ask; `ask` and always-ask still win. Luma-specific.
- `0004-audio-capture-portal.patch`: adds `org.freedesktop.portal.AudioCapture`
  (`src/audio-capture.c`, `data/org.freedesktop.portal.AudioCapture.xml`,
  `data/org.freedesktop.impl.portal.AudioCapture.xml`). An application asks to
  record other applications' sound; the backend (luma-portal) shows the choice
  and returns one PipeWire node; `OpenPipeWireRemote` returns a remote that
  sees only that node (core rwx, node factory r, the node rx, `PW_ID_ANY` 0).
  Sessions, option validation and restore tokens (permission table
  `audio-capture`) follow ScreenCast; `audio_mode` uses the ScreenCast v7
  values from flatpak/xdg-desktop-portal#2208. Tests:
  `tests/test_audiocapture.py`. Written to be proposed upstream.
- `0000-luma-fedora-spec.patch`: adds the patches, the `luma.N` release and a
  `%check` running the upstream OpenURI and the AudioCapture integration
  tests.
