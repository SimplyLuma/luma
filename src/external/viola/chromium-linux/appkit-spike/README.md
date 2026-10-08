# Viola native application host

This host supplies Viola’s Luma window, sidebar, floating page action bar,
menus, address suggestions, downloads and Mini windows. It drives the retained
Chromium engine through the existing private pipe and native frame transport.
The native RPM supplies required host services; the signed independent Flatpak
application owns activation on installed Luma systems.

The desktop, compact and phone presentations use the shared AppKit components.
The pointer-and-rings application artwork is the existing supplied Viola icon.
The launcher retains the native profile and imports it by copying when needed;
updates of the same Flatpak application retain its existing profile directory.
No browser history, bookmarks, cookies or credentials are deleted by updating.

Native release 36 restores the qualified floating toolbar and artwork that were
omitted from the release 35 source assembly. The Chromium engine, signed host
lease service and profile migration paths remain unchanged.

`test_*.py` covers host behavior. `qualify_*.py` provides private compositor and
real-engine checks. Disposable QA modes use isolated profiles and are not
release acceptance by themselves. The package build verifies its exact host
manifest and the retained engine manifest.

The application uses the dedicated `com.rhyme.viola.browser` icon name so an
older native package's generic icon alias cannot shadow its new artwork. The
ISO desktop policy uses that same app-owned icon. Existing release 35 images
with an immutable `Icon=viola-browser` override need the host policy update for
the dock icon; updating only the app does not rewrite that system override.
