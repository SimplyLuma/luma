Name:           luma-boot-theme
Version:        0.1.0
Release:        1.luma.21.creator20261006.2%{?dist}
Summary:        Project Luma quiet Plymouth boot surface
# The Luma wordmark and loader artwork are brand identifiers, governed by the
# Luma Trademark Policy rather than by these copyright licenses.
License:        Apache-2.0 AND MPL-2.0 AND OFL-1.1
BuildArch:      noarch
# luma-loading takes over from the firmware logo with the script plugin's
# firmware background (Luma's plymouth patch 0002). With an unpatched plugin
# the splash starts directly on Luma's surface instead.
Requires:       plymouth-plugin-script
# Luma's plymouth selects luma-loading in /usr/share/plymouth/plymouthd.defaults;
# the /etc/plymouth/plymouthd.conf this package migrates to has no settings of
# its own (ADR-045).
Requires:       plymouth >= 24.004.60-24.luma.3
Requires:       plymouth-plugin-two-step
Requires:       google-figtree-fonts
# luma-boot-hidden-menu
Requires:       python3
# luma-firmware's unlock prompt names the keyboard layout with Plymouth's own
# pre-rendered keymap image, which only matches the plymouth build it ships
# with. Without it the prompt still works and simply omits the layout name.
Recommends:     plymouth-theme-spinner
BuildRequires:  python3
BuildRequires:  librsvg2-tools
BuildRequires:  systemd-rpm-macros

Source0:        luma-loading.plymouth
Source1:        luma-loading.script
Source2:        LICENSE
Source3:        luma-grub-theme.txt
Source4:        prairie.pf2
Source5:        luma.cfg
Source6:        luma-loading-handheld.plymouth
Source7:        luma-loading-handheld.script
Source8:        luma-wordmark.svg
Source9:        luma-wordmark.png
Source10:       luma-loading-dot.svg
Source11:       luma-loading-dot.png
Source12:       OFL.txt
Source13:       luma-wordmark-ink.png
Source14:       luma-loading-dot-ink.png
Source15:       luma-boot-variants.tar.gz
Source16:       luma-theme.script.in
Source17:       compile-theme.py
Source18:       desktop-theme.json
Source19:       mobile-theme.json
Source20:       apply-grub-policy.sh
Source21:       luma-firmware.plymouth
Source22:       render-firmware-theme.py
Source23:       plymouthd.conf
Source24:       plymouthd.conf.previous
Source25:       luma-boot-splash-migrate
Source26:       luma-boot-splash-migrate.service
Source27:       09_luma_hidden_menu.cfg
Source28:       luma-boot-hidden-menu
Source29:       luma-boot-hidden-menu.service
Source30:       luma-boot-splash-module-setup.sh
Source31:       boot-splash-migrate.sh
Source32:       prairie-12.pf2
Source33:       prairie-22.pf2
Source34:       check-grub-fonts.py
Source35:       prepare-efi-identity.py
Source36:       test_luma_efi_identity.py

%description
Project Luma's source-owned desktop and handheld graphical boot surfaces for
Plymouth. The desktop presentation starts on the firmware's own boot logo and
cross-fades to Luma's surface, wordmark and loader, with Luma's unlock prompt
and update screen; the handheld presentation owns the whole screen. The
firmware-logo presentation, luma-firmware, stays available.
All of them share the canonical Luma artwork while retaining Plymouth's native
prompt, diagnostic, and recovery paths.

%prep

%build
# Every luma-firmware image is rendered here from the brand SVGs.
python3 %{SOURCE22} --wordmark %{SOURCE8} --dot %{SOURCE10} --output luma-firmware

%install
install -D -m 0755 %{SOURCE35} %{buildroot}%{_libexecdir}/luma-prepare-efi-identity
install -D -m 0644 %{SOURCE0} \
  %{buildroot}%{_datadir}/plymouth/themes/luma-loading/luma-loading.plymouth
install -D -m 0644 %{SOURCE1} \
  %{buildroot}%{_datadir}/plymouth/themes/luma-loading/luma-loading.script
install -D -m 0644 %{SOURCE6} \
  %{buildroot}%{_datadir}/plymouth/themes/luma-loading-handheld/luma-loading-handheld.plymouth
install -D -m 0644 %{SOURCE7} \
  %{buildroot}%{_datadir}/plymouth/themes/luma-loading-handheld/luma-loading-handheld.script
install -D -m 0644 %{SOURCE9} \
  %{buildroot}%{_datadir}/plymouth/themes/luma-loading-handheld/luma-wordmark.png
install -D -m 0644 %{SOURCE11} \
  %{buildroot}%{_datadir}/plymouth/themes/luma-loading-handheld/luma-loading-dot.png
install -D -m 0644 %{SOURCE13} \
  %{buildroot}%{_datadir}/plymouth/themes/luma-loading/luma-wordmark.png
# The desktop presentation: the theme, the artwork rendered in %%build, and
# Plymouth's own keymap image for the layout name in prompts.
install -D -m 0644 %{SOURCE21} \
  %{buildroot}%{_datadir}/plymouth/themes/luma-firmware/luma-firmware.plymouth
install -m 0644 luma-firmware/*.png %{buildroot}%{_datadir}/plymouth/themes/luma-firmware/
ln -s ../spinner/keymap-render.png \
  %{buildroot}%{_datadir}/plymouth/themes/luma-firmware/keymap-render.png
# The desktop's plymouthd.conf, and every one Luma shipped before it, for the
# one-shot that moves an unedited /etc copy forward.
install -D -m 0644 %{SOURCE23} %{buildroot}%{_datadir}/luma/boot/plymouthd.conf
install -D -m 0644 %{SOURCE24} %{buildroot}%{_datadir}/luma/boot/plymouthd.conf.previous
install -D -m 0755 %{SOURCE25} %{buildroot}%{_libexecdir}/luma-boot-splash-migrate
# A locally regenerated initramfs follows the migration it is built ahead of.
install -D -m 0755 %{SOURCE30} \
  %{buildroot}%{_prefix}/lib/dracut/modules.d/46luma-boot-splash/module-setup.sh
install -D -m 0644 %{SOURCE26} %{buildroot}%{_unitdir}/luma-boot-splash-migrate.service
# Statically wanted: an image default, not a preset an update could skip.
install -d -m 0755 %{buildroot}%{_unitdir}/sysinit.target.wants
ln -s ../luma-boot-splash-migrate.service \
  %{buildroot}%{_unitdir}/sysinit.target.wants/luma-boot-splash-migrate.service
# The hidden GRUB menu. bootupd assembles grub.cfg from this directory when it
# installs a bootloader, so it reaches machines installed from an image that
# carries it; it is inert everywhere else.
install -D -m 0644 %{SOURCE27} \
  %{buildroot}%{_prefix}/lib/bootupd/grub2-static/configs.d/09_luma_hidden_menu.cfg
# bootupd never rewrites an installed grub.cfg, so a computer that updates
# gets the piece from this helper: at boot, and from luma-update right after
# it stages a release. Statically wanted, like the splash migration.
install -D -m 0755 %{SOURCE28} %{buildroot}%{_libexecdir}/luma-boot-hidden-menu
install -D -m 0644 %{SOURCE29} %{buildroot}%{_unitdir}/luma-boot-hidden-menu.service
install -d -m 0755 %{buildroot}%{_unitdir}/multi-user.target.wants
ln -s ../luma-boot-hidden-menu.service \
  %{buildroot}%{_unitdir}/multi-user.target.wants/luma-boot-hidden-menu.service
install -D -m 0644 %{SOURCE14} \
  %{buildroot}%{_datadir}/plymouth/themes/luma-loading/luma-loading-dot.png
tar -xzf %{SOURCE15} -C %{buildroot}%{_datadir}/plymouth/themes/
install -D -m 0644 %{SOURCE18} %{buildroot}%{_datadir}/luma/boot/desktop-theme.json
install -D -m 0644 %{SOURCE19} %{buildroot}%{_datadir}/luma/boot/mobile-theme.json
install -D -m 0644 %{SOURCE8} \
  %{buildroot}%{_datadir}/luma/boot/brand/luma-wordmark.svg
install -D -m 0644 %{SOURCE10} \
  %{buildroot}%{_datadir}/luma/boot/brand/luma-loading-dot.svg
install -D -m 0644 %{SOURCE2} \
  %{buildroot}%{_licensedir}/%{name}/LICENSE
install -D -m 0644 %{SOURCE12} \
  %{buildroot}%{_licensedir}/%{name}/OFL.txt
install -D -m 0644 %{SOURCE3} \
  %{buildroot}%{_datadir}/luma/boot/grub-theme/theme.txt
install -D -m 0644 %{SOURCE4} \
  %{buildroot}%{_datadir}/luma/boot/grub-theme/prairie.pf2
install -D -m 0644 %{SOURCE32} \
  %{buildroot}%{_datadir}/luma/boot/grub-theme/prairie-12.pf2
install -D -m 0644 %{SOURCE33} \
  %{buildroot}%{_datadir}/luma/boot/grub-theme/prairie-22.pf2
install -D -m 0644 %{SOURCE5} \
  %{buildroot}%{_datadir}/luma/boot/luma.cfg
install -D -m 0755 %{SOURCE20} %{buildroot}%{_libexecdir}/luma-apply-grub-policy
# The package default follows the desktop composition. dracut copies only
# the configured theme into the initramfs, so a default pointing at a theme
# that is not there is a dangling link in the one place nothing can fix it.
ln -s luma-loading/luma-loading.plymouth \
  %{buildroot}%{_datadir}/plymouth/themes/default.plymouth

%check
# Run the retained test against this SRPM's helper, including the native
# EFI_LOAD_OPTION UTF-16 description layout and signed/foreign-file guards.
mkdir -p efi-fixture/scripts/boot efi-fixture/tests/unit
cp %{SOURCE35} efi-fixture/scripts/boot/prepare-efi-identity.py
cp %{SOURCE36} efi-fixture/tests/unit/test_luma_efi_identity.py
python3 efi-fixture/tests/unit/test_luma_efi_identity.py
python3 %{SOURCE34} %{buildroot}%{_datadir}/luma/boot/grub-theme
themes=%{buildroot}%{_datadir}/plymouth/themes
# The desktop default is the Luma splash, selected by Luma's plymouth in /usr;
# the /etc template this package moves machines to has no settings, and
# default.plymouth agrees.
if grep -Evq '^[[:space:]]*(#|$)' %{buildroot}%{_datadir}/luma/boot/plymouthd.conf; then
  echo 'the /etc plymouthd.conf template has settings' >&2
  exit 1
fi
test "$(readlink "$themes/default.plymouth")" = luma-loading/luma-loading.plymouth
test -f "$themes/luma-loading/luma-loading.plymouth"
# Its artwork, including the unlock prompt rendered for its surface.
for image in luma-wordmark luma-loading-dot luma-track prompt-lock prompt-field \
  prompt-bullet prompt-capslock; do
  test -s "$themes/luma-loading/$image.png"
done
python3 - "$themes/luma-loading" <<'PYTHON'
import struct, sys
from pathlib import Path
directory = Path(sys.argv[1])
def size(name):
    data = (directory / name).read_bytes()
    assert data[:8] == b'\x89PNG\r\n\x1a\n', name
    return struct.unpack('>II', data[16:24])
for name, dimensions in {'prompt-lock.png': (60, 36), 'prompt-field.png': (300, 36),
                         'prompt-bullet.png': (16, 16), 'prompt-capslock.png': (24, 28)}.items():
    assert size(name) == dimensions, (name, size(name))
for theme, widths in [('luma-loading',range(104,143)),('luma-loading-handheld',range(120,481))]:
    directory=directory.parent/theme
    for width in widths:
        for stem in ('luma-wordmark','luma-wordmark-stop'):
            name=f'{stem}-{width}.png'
            assert size(name)==(width,width*715//2219), (name,size(name))
PYTHON
# The hand-off, the prompt and the update screen are in the desktop script.
for call in Window.HasFirmwareBackground Window.SetFirmwareBackgroundOpacity \
  Plymouth.SetSystemUpdateFunction Plymouth.GetCapslockState Plymouth.GetTime \
  brand_logo_sprite 'global.firmware_hold_seconds = 1.0;' 'global.advanced_hint = "";' \
  prompt-field.png; do
  grep -Fq "$call" "$themes/luma-loading/luma-loading.script"
done
grep -Fxq 'global.handheld = 0;' "$themes/luma-loading/luma-loading.script"
grep -Fxq 'global.handheld = 1;' "$themes/luma-loading-handheld/luma-loading-handheld.script"
# luma-firmware stays complete for anyone who selects it.
for image in luma-firmware.plymouth watermark.png lock.png entry.png bullet.png capslock.png; do
  test -e "$themes/luma-firmware/$image"
done
# The migration moves only Luma's own earlier settings, once, and the dracut
# module that follows it is valid shell.
bash %{SOURCE31} %{buildroot}%{_libexecdir}/luma-boot-splash-migrate \
  %{buildroot}%{_datadir}/luma/boot/plymouthd.conf \
  %{buildroot}%{_datadir}/luma/boot/plymouthd.conf.previous
bash -n %{buildroot}%{_prefix}/lib/dracut/modules.d/46luma-boot-splash/module-setup.sh
if grep -Fq "$(sha256sum %{SOURCE23} | cut -d ' ' -f 1)" %{buildroot}%{_datadir}/luma/boot/plymouthd.conf.previous; then
  echo 'the current plymouthd.conf is listed as an earlier one' >&2
  exit 1
fi

%files
%{_libexecdir}/luma-prepare-efi-identity
%{_libexecdir}/luma-apply-grub-policy
%{_libexecdir}/luma-boot-splash-migrate
%dir %{_prefix}/lib/dracut/modules.d/46luma-boot-splash
%{_prefix}/lib/dracut/modules.d/46luma-boot-splash/module-setup.sh
%{_unitdir}/luma-boot-splash-migrate.service
%{_unitdir}/sysinit.target.wants/luma-boot-splash-migrate.service
%{_prefix}/lib/bootupd/grub2-static/configs.d/09_luma_hidden_menu.cfg
%{_libexecdir}/luma-boot-hidden-menu
%{_unitdir}/luma-boot-hidden-menu.service
%{_unitdir}/multi-user.target.wants/luma-boot-hidden-menu.service
%license %{_licensedir}/%{name}/LICENSE
%license %{_licensedir}/%{name}/OFL.txt
%{_datadir}/plymouth/themes/luma-firmware/
%{_datadir}/plymouth/themes/luma-loading/
%{_datadir}/plymouth/themes/luma-loading-handheld/
%{_datadir}/plymouth/themes/default.plymouth
%{_datadir}/luma/boot/

%changelog
* Mon Oct 05 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.20
- Brand shim fallback CSV entry metadata without altering signed EFI binaries
- Rasterize the canonical wordmark at its actual Plymouth layout sizes

* Fri Sep 18 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.17
- Keep the Luma splash selected on every machine: Luma's plymouth selects it
  in /usr, and /etc/plymouth/plymouthd.conf becomes a template with no
  settings, so a client-side plymouth override can no longer bring back
  Fedora's bgrt splash
- Show the Luma wordmark at the bottom of the firmware logo, hold it for a
  second, then let it rise into Luma's surface as the vendor logo fades
- Time the hand-off and the loader by Plymouth's clock, not by frame count
- Move Luma's .16 setting and Fedora's stock template to the new template
  once, never an administrator's own settings
- Reserve an advanced-startup hint line, empty for now

* Thu Sep 17 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.16
- Make luma-loading the desktop splash again: it starts on the firmware logo
  and cross-fades to Luma's surface, wordmark and loader instead of keeping
  the vendor screen for the whole boot
- Draw the unlock prompt from rendered artwork (lock, field, bullets, Caps Lock)
  and give update screens a title, subtitle and progress bar in luma-loading
- Move an unedited luma-firmware plymouthd.conf to luma-loading once, never an
  administrator's choice, and carry the move into locally built initramfs
  images through the luma-boot-splash dracut module

* Wed Sep 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.15
- Hide GRUB's menu on computers that update, not only on fresh installs:
  luma-boot-hidden-menu adds or refreshes the piece in bootupd's grub.cfg at
  boot and after luma-update stages a release, and leaves a grub.cfg whose
  menu timing an administrator changed alone
- Rename the piece 09_luma_hidden_menu.cfg so it is read before luma.cfg, whose
  graphical terminal would otherwise clear the firmware logo

* Wed Sep 16 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.14
- Hide GRUB's menu on bootupd installs so the firmware logo stays on screen
  from power-on to the boot splash; Esc, F8 or Shift still open it

* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.13
- Finish luma-firmware and make it the desktop splash: the firmware logo stays,
  Luma's four-dot loader runs under it and the Luma wordmark sits at the bottom
- Render every luma-firmware image from the brand SVGs at build, including
  the unlock prompt, and give update screens a title and progress bar
- Move an unedited earlier Luma plymouthd.conf to the new default once, never
  an administrator's

* Mon Sep 07 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.8
- Render the native Hearth desktop surface with canonical artwork
- Bound native unlock prompts and expose real daemon messages on both modes

* Sun Aug 30 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.6
- Add the native Paper handheld Plymouth presentation and canonical wordmark

* Wed Aug 12 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.5
- Add the source-owned light GRUB advanced-startup surface and firmware action

* Tue Aug 11 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.4
- Use boot-font-safe ASCII punctuation in the loading label

* Tue Aug 11 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.3
- Own Plymouth's canonical default-theme symlink for host-only initramfs builds

* Tue Aug 11 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.2
- Keep the graphical prompt path compatible with Plymouth's script parser

* Tue Aug 11 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.1
- Add the first-pass quiet graphical boot surface
