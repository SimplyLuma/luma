# Luma desktop configuration

This directory is the source of truth for Luma-owned GNOME defaults. The
running design VM is only a preview surface.

## Composition contract

- `packages.txt` pins Luma's patched GNOME Shell, native Shelf, libadwaita
  design system, and the locally built Files, tiling, and background packages.
- `inputs.env` pins source checksums and the isolated Fedora RPM build image.
- `dconf/profile/user`, `dconf/db/luma.d/00-luma-desktop`, and
  `dconf/db/gdm.d/00-prairie-login` are installed at the same paths below
  `/etc` and compiled with `dconf update` during image composition. GDM needs
  its own database because the greeter does not consume a person's settings.
- `gtk-3.0/settings.ini` and `gtk-4.0/settings.ini` are installed to
  `/etc/gtk-3.0/settings.ini` and `/etc/gtk-4.0/settings.ini`. They are the
  only Luma-owned GTK-level (not GSettings) system default today:
  `gtk-error-bell=0`, so GTK never asks for a beep on Backspace in an empty
  field or any other refused text action. A person can still turn it back on
  in their own `$XDG_CONFIG_HOME/gtk-3.0/settings.ini` or `gtk-4.0/settings.ini`.
- Values are defaults, not locks. A person can change them through GNOME
  Settings or the extension's supported preferences interface.
- No login script, autostart command, timer, or boot-time mutation applies
  these preferences.

The GNOME Control Center option-order changes are maintained separately as a
source patch under `patches/gnome-control-center/` because presentation order
belongs to the Settings application, not to dconf.

Build the patched package on `luma-lab` with
`scripts/packages/build-gnome-control-center.sh`. The script verifies Fedora's
source RPM before applying the isolated Luma patch and records checksums for
all produced packages.

Build the patched Shell packages with
`scripts/packages/build-gnome-shell.sh`. The script verifies the exact Fedora
source RPM archived by Koji, applies Luma's isolated user-session panel patch,
and retains GNOME Shell's package split, license, and identity.

The normal desktop dock is the source-owned GNOME Shell **Shelf**, built by the
Shell package above. Dash to Dock is neither installed nor enabled in desktop
composition and owns no desktop lifecycle. Its separately pinned builder is
retained only for explicitly scoped handheld experiments until those renderers
consume the shared Shelf contract.

Build the patched Files packages with `scripts/packages/build-nautilus.sh`.
The script verifies Fedora's exact source RPM, applies the documented search
and sidebar patch, and retains Nautilus's upstream package split and identity.

Build the distro-wide light design system with
`scripts/packages/build-libadwaita.sh`. It patches Fedora's exact libadwaita
source package, so generic colors, radii, header bars, controls, entries, and
navigation rows are shared by every libadwaita application rather than copied
into Files-specific CSS.

GNOME Calculator's v1 application composition is maintained as a clean source
patch under `patches/gnome-calculator/`; its release RPM is built by
`scripts/packages/build-gnome-calculator.sh`. During visual work,
`scripts/dev/sync-calculator-preview.sh` and
`scripts/dev/deploy-calculator-preview.sh` provide a persistent, user-home-only
preview lane; they do not replace the system package or compose an OS image.
Prairie Calculator intentionally exposes one standard Basic surface while
retaining GNOME Calculator's parser, arithmetic engine, keyboard entry,
clipboard behavior, package identity, and attribution.

Notes and Calendar are shared Prairie core applications. Notes also handles
plain-text and Markdown files in independent file windows without the Notes
library or history. The historical GNOME Calendar/Text Editor patch builders
remain as source references; they are excluded from the current package batch,
emulator roles and default dock. Existing system Flatpaks are retired only after
the shared native provider is staged.

Build the tiling packages with `scripts/packages/build-tiling.sh`. It verifies
the exact GNOME Extensions-reviewed Tiling Shell zip and upstream license,
applies the documented teardown-order patch and safe file modes, and separately
packages Luma's small Quick Settings bridge. All three component builders use
one persistent, pinned Fedora RPM container so dependencies are paid once while
each package build still starts from a fresh RPM top directory.

Build the Luma wallpaper collection with
`scripts/packages/build-backgrounds.sh`. It verifies the source-controlled
asset hashes, installs standard GNOME wallpaper-picker metadata, and leaves the
desktop preference unlocked. Adding a wallpaper extends the collection rather
than replacing existing choices.

Build the inheriting Prairie application icon theme with
`scripts/packages/build-prairie-icon-theme.sh`. The separate noarch RPM keeps
rebranded artwork outside application packages, overrides icons by existing
application IDs, inherits Adwaita and hicolor, and ships Prairie as an unlocked
GSettings default.

## Acceptance contract

A fresh user on a fresh image must receive all of the following:

1. A bottom Shelf that remains visible on the desktop, contains separate dock
   and action/status islands, and reserves ordinary window work area.
2. No normal-session top panel, hidden geometry, hit target, or strut.
3. Locale-aware time first, followed by weekday and date in the action island;
   12-hour is the
   unlocked default and Settings can switch it to 24-hour.
4. Natural scrolling for mouse and touchpad.
5. Close, minimize, and maximize controls on the left of standard windows.
6. User-changeable settings; none of these values may be locked.
7. A Quick Settings menu named Tiling, off by default, with visual layout
   selection and the credited Tiling Shell editor actions; no redundant
   top-panel tiling indicator.
8. Adaptive mouse acceleration by default.
9. A Gestures group in Touchpad Settings that accurately documents GNOME's
   native three-finger Activities and workspace gestures.
10. A Show Apps button at the left-leading edge of the Shelf dock.
11. Prism as the unlocked default wallpaper in light and dark appearance, with
    Prism and Luma Mesh Gradient both available in GNOME's wallpaper picker.
12. Filer as the first application in the unlocked fresh-profile dock order,
    using its existing `org.gnome.Nautilus.desktop` compatibility ID.
13. Prairie as the unlocked icon-theme default, with Filer resolved from the
    theme by `org.gnome.Nautilus` and all unbranded icons inherited unchanged.
14. The Prairie GDM greeter uses the same unlocked 12-hour default as a fresh
    desktop profile while remaining isolated from each person's preference.
