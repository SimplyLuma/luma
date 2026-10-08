# Desktop launcher policy

This package supplies standard XDG desktop-entry overrides in the immutable
directory `/usr/share/luma/desktop-overrides/applications`. A packaged standard
`environment.d` assignment prepends its data root to `XDG_DATA_DIRS`, preserving
existing entries (or the standard defaults). User-local desktop entries retain
their normal higher priority. It never overwrites another
RPM's files. Install on desktop compositions only; handheld provider functionality
and shared application binaries are unchanged. `NoDisplay` hides launcher entries
while preserving direct program use. Uninstalling restores upstream visibility
in the next fresh session. There is no login replay or resident helper. On
OSTree, this avoids mutable `/usr/local` payloads. Acceptance requires a fresh
GDM-to-GNOME Shell session and GIO/activated-application visibility checks;
observing the user-manager environment alone is not sufficient.

LibreOffice Writer/Calc/Impress remain required engines for Write/Grid/Stage.
Search is Luma's system-search configuration surface. Its launcher is hidden;
the configuration executable and D-Bus search service remain available. The
provider's own desktop entry also declares NoDisplay for future compositions.
Foot and GNOME Text Editor are separate, removable applications.

Rygel Preferences configures the upstream home-network media-sharing provider;
Color Profile Viewer inspects colour profiles. These backend utilities are
hidden from the application aggregate while remaining callable by their owning
settings/workflows. Neither is a Luma product launcher.

## ThinkPad migration policy (September 10, 2026)

The additional GNOME/Fedora and Wine entries mirror the installed provider
desktop IDs and retain launch metadata while setting NoDisplay. Their source
is the Fedora 44 RPM /usr/share/applications and system Flatpak export desktop
entries captured on the ThinkPad; they are upstream desktop metadata, not
new Luma applications. Provider code and user data are retained.

The dconf profile includes Luma below local/site policy and above distro policy.
Packaged defaults remain unlocked. An existing Fedora user's explicit wallpaper,
favorites and window-button preferences require a one-time, user-authorized
migration; no login replay is installed. Dock order was compared with the
running reference desktop emulator on September 10. Android launcher presence
is a separate gate: a favorite ID does not prove its application is installed.

Validation: RPM6 and RPM7 built and desktop-file-validate passed. The exported
ThinkPad base currently treats replace/remove requests as inactive and a normal
install conflicts with its installed RPM5. RPM7 has NOT been activated. Temporary
account-local NoDisplay entries provide requested cleanup; backups live in
~/.local/state/luma/polish-20260910. Remove these managed copies after successful
image integration and fresh-session validation. Do not describe this as completed
image rollout. Rebuild after the latest emulator dock comparison before release.

The profile is installed as /etc/dconf/profile/luma and selected through the
existing package environment.d file, preserving dconf’s ownership of its user
profile. Fresh image composition uses the distinct profile without a file conflict.

Viola (the `viola-browser-stable` image package) is the default web browser.
`gnome-mimeapps.list` maps http, https, HTML and XHTML to
`com.rhyme.viola.desktop`, the browser's own desktop identity (Chromium's
`CHROME_DESKTOP`, which Viola hands to `xdg-settings` when it checks or sets
itself as default), so Viola never reports that it is not the default. The
visible launcher is `viola-browser.desktop`, which the default dock carries.
These are system defaults only: a person's own `~/.config/mimeapps.list`
choice always wins.

Viewer (`luma-viewer`) is the default for PDFs (application/pdf and the
x-bzpdf, x-gzpdf, x-xzpdf and x-ext-pdf variants) and images.
`gnome-mimeapps.list` outranks gnome-session's
/usr/share/applications/gnome-mimeapps.list, which names Document Viewer.
Document Viewer is not part of Luma: `org.gnome.Papers` and `org.gnome.Evince`
are on the system Flatpak replacement list (provider `luma-viewer`), so
existing installs lose the Flatpak once at boot. The Papers RPMs Fedora's base
carries are kept because none of them is a visible application:
papers-thumbnailer draws PDF thumbnails in Files, papers-previewer is GTK's
hidden print preview, and papers-nautilus adds the document properties page.

`luma-default-apps-migration` runs once per account (a user unit wanted by
every session, skipped by a stamp in ~/.local/state/luma afterwards). For each
PDF and image type, a personal default that is missing, names a replaced stock
viewer (Papers, Evince, Loupe, Eye of GNOME), names an application that is no
longer installed, or names a web browser (browser installers claim PDFs
without asking) becomes Viewer. Any other personal choice is kept. Every
decision goes to the journal for Luma Vitals (LUMA_VITALS_KIND=default-apps).
Gates: tests/unit/test_viewer_pdf_default.py (source) and
tests/os/gate/default-pdf-viewer.sh (installed image).

## Creator preview baseline (October 5, 2026)

Luma Calculator, Terminal, and Disks replace the stock application frontends.
The desktop image excludes GNOME Disk Utility, Calculator, Ptyxis, Terminal,
Console, Yelp/Help, Firefox, and the GNOME Extensions manager at package
composition, rather than merely filtering their icons. OpenEmu and Imager are
optional applications and are excluded from the default image. Their source
and support for independently installed applications are preserved.

Secondary Displays and Mods are hidden by standard immutable NoDisplay
overrides. Displays remains available to its owning display workflows. The
Mods transactional/recovery provider remains intact; this decision does not
enable arbitrary GNOME extensions or privileged Mod payloads. Luma Search,
Filer's Applications provider, and native GIO consumers use should_show(), so
these overrides apply to both application browsing and search.

The unlocked fresh-profile dock starts with Filer and Viola and ends with
Viewer and Settings. It pins the native Luma Terminal and no optional
Creative/Office/Studio application. Existing personal dock order is preserved.

`tests/check_launcher_visibility.py --self-test` exercises GIO's actual XDG
override resolution, preserving provider command metadata and visible native
role IDs. It proves that making either hidden entry visible is rejected.
Fresh-session application browsing/search, packaged composition, upgrade and
rollback are separate acceptance gates; fixture evidence does not close them.
