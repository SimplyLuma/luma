# Luma boot policy

`grub` and `plymouth` are separate surfaces with separate ownership:

The installed UEFI entry is named **Luma** by bootupd, which reads the target's
`/etc/system-release`. Shim's fallback/recovery path reads its own UCS-2LE
`BOOTX64.CSV` (or architecture counterpart), so image finalization runs the
packaged `luma-prepare-efi-identity` against the image root before installation.
It changes only the Fedora shim record's label and comment, retaining its
loader, arguments, `EFI/fedora` path, and signed executable bytes. It never
opens firmware variables, removes an existing entry, or touches other vendors.
An existing Fedora-labelled firmware entry may therefore remain on a machine;
the displayed label alone does not identify which installed payload it boots.
The interfaces are documented by [shim fallback](https://github.com/rhboot/shim/blob/main/README.fallback)
and [bootupd 0.3.2](https://github.com/coreos/bootupd/blob/v0.3.2/src/efi.rs).

The wordmark uses the unchanged canonical brand SVG. The boot package renders
it at every existing layout width (desktop 104–142 px, handheld 120–480 px),
including its separate orange stop. Plymouth selects the matching raster
without shrinking a 2219 px image through its bilinear scaler, preserving
antialiased SVG edge coverage at the drawn size. This does not change layout,
the approved brand geometry, or Plymouth's native display scale selection.

- GRUB keeps Fedora's BLS deployment and rollback entries intact. Atomic
  Fedora's OSTree-generated configuration delegates its dynamic `blscfg`
  invocation to `luma.cfg`. Fedora Atomic's OSTree entries do not carry the
  upstream `--show-default` metadata, so both the primary view and **Show
  advanced startup options** use the real, unfiltered rollback-preserving BLS
  inventory. The light menu automatically starts the default after three
  seconds. On UEFI systems where GRUB's
  `fwsetup --is-supported` succeeds,
  **Boot device and firmware settings** opens the real firmware chooser.
- On bootupd installs, which is every image-built install, GRUB stays hidden.
  `luma-boot-theme` ships `09_luma_hidden_menu.cfg` into bootupd's static
  configuration, so the firmware logo stays on screen from power-on to
  Plymouth. bootupd writes that configuration only at install, so
  `luma-boot-hidden-menu` adds the piece to computers that update: at boot, and
  from luma-update right after staging. It changes only its own section and
  skips a `grub.cfg` whose menu timing an administrator changed. Esc, F8 or a held Shift during the one-second hidden countdown opens
  the menu, and `menu_show_once_timeout` still forces it. `luma.cfg` switches
  to its graphical terminal only when the menu is drawn. See
  `docs/changes/2026-09-16-flicker-free-boot.md` for the boot timeline and what
  is hardware-bound (Lunar Lake's `xe` blanks once at probe).
- Plymouth owns the graphical handoff after the kernel starts. Desktop adds
  `rhgb quiet`, and Luma's plymouth selects the packaged `luma-loading`
  presentation in `/usr/share/plymouth/plymouthd.defaults`: Luma's Ink surface
  with the centred wordmark and four-dot loader. `/etc/plymouth/plymouthd.conf`
  is a template with no settings, so nothing in `/etc` (a package reinstall, a
  client-side plymouth override, OSTree's merge) can take the splash away; an
  administrator's `[Daemon]` section there still wins (ADR-045).

  The firmware draws its own logo (the vendor's, published through the ACPI
  BGRT table) before any of our code runs, and GRUB's hidden menu keeps it on
  screen until Plymouth starts. `luma-loading` begins on exactly that screen:
  Luma's Plymouth (`patches/plymouth/0002-script-firmware-background.patch`)
  gives script themes the firmware logo, placed by two-step's own rules, and
  the Luma wordmark comes up at the bottom of that screen, holds for 1.0 s, and
  then over 0.9 s the vendor logo fades while the wordmark rises into the Luma
  lockup and the loader comes up under it. There is no black frame and the
  vendor logo does not move. Timing follows Plymouth's clock
  (`Plymouth.GetTime`, patch 0003). Where the firmware
  published no logo, or on an unpatched Plymouth, the splash starts directly
  on the Luma surface; shutdown and reboot always do.

  Disk-unlock prompts keep the wordmark and put a Luma field under it: the
  prompt text, a padlock, the field with bullets (or the typed answer for
  questions) and a Caps Lock indicator, all rendered for the profile's surface
  at package build with `luma-firmware`'s geometry. Offline update screens
  (`plymouth change-mode --updates` and friends) show Plymouth's title,
  "Do not turn off your computer" and a progress bar with its percentage under
  the wordmark.

  `luma-firmware`, which keeps the vendor logo for the whole boot with Luma's
  loader and a bottom wordmark, stays packaged and complete for administrators
  who select it. Its images are rendered from the brand SVGs at package build
  by `scripts/boot/render-firmware-theme.py`, at the pixel size Plymouth draws
  them; `tests/smoke/boot-firmware-theme.sh` checks the render is reproducible
  and correctly sized.

  `luma-loading` remains packaged, and handhelds, which have no firmware logo
  to inherit, keep `luma-loading-handheld`: mobile composition writes its own
  image-owned `plymouthd.conf` selecting it. Both keep Plymouth's standard
  detail view and native exceptional prompts available for diagnostics and
  recovery.

The platform-specific Plymouth configuration is copied into `/etc` during
image composition. Selection is explicit at the image/initramfs boundary; it
does not depend on display-width guessing or per-user settings.
`apply-grub-policy.sh` uses GRUB's own persistent environment after verifying
that the running system is OSTree-booted and that the Atomic-generated
configuration consumes it. It refuses mutable Workstation systems, whose
Fedora-generated GRUB configuration remains byte-for-byte authoritative. On
Atomic systems it retains the original as `grub.cfg.pre-luma-advanced` before
replacing only the generated file's dynamic `blscfg` call with a source
statement; BLS remains live and authoritative. It deliberately does not run
`grub2-mkconfig`, which
cannot probe an Atomic composefs root and does not own this image's generated
configuration.
They are source-controlled product configuration, not a first-login script or
runtime mutation.

## Moving existing machines to a new splash setting

Image-built machines receive `/etc/plymouth/plymouthd.conf` from the image,
and OSTree's three-way `/etc` merge replaces it on update for as long as it is
unmodified. Machines provisioned before the image pipeline had Luma's setting
written over Fedora's file, so to OSTree it is a local edit and would be kept
forever. For those, `luma-boot-theme` ships the current desktop setting and
the SHA-256 of every earlier one (`plymouthd.conf.previous`), and the static
one-shot `luma-boot-splash-migrate.service` compares them early in each boot:
an exact match is still Luma's own and is replaced atomically with the current
file; anything else -- an administrator's edit, a handheld's setting, a link,
no file -- is left alone. It is idempotent and has nothing to do once the file
is current. When `config/boot/plymouthd.conf` changes again, append the
outgoing digest to `plymouthd.conf.previous`; the smoke test fails if any
version in history is neither current nor listed.

Each default is applied at most once: the helper records the default it has
reconciled the machine with in `/etc/plymouth/.luma-boot-splash-default`, so
an administrator who later picks an earlier Luma setting again keeps it.

The splash itself is drawn from the initramfs. Image-built initramfs images
carry the image's setting. On machines with local initramfs regeneration
(`rpm-ostree initramfs --enable`), rpm-ostree builds the next deployment's
initramfs from the running system's `/etc`, before the new deployment's helper
has run; the `46luma-boot-splash` dracut module asks the helper
(`--check`) at that point and, where the file is about to be moved, gives the
initramfs the current default and its theme. The first boot of the update
already shows the new splash.
