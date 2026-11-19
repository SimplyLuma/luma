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
- `0000-luma-fedora-spec.patch`: adds the patches, the `luma.N` release and a
  `%check` running the upstream OpenURI integration tests.
