# SPDX-License-Identifier: Apache-2.0

Name:           luma-openh264
Version:        0.1.0
Release:        1.luma.2%{?dist}
Summary:        Cisco's OpenH264 binary, downloaded from Cisco onto this computer
License:        Apache-2.0
URL:            https://projectluma.org/media
Source0:        luma-openh264.tar.gz
Source1:        LICENSE.md

BuildArch:      noarch
BuildRequires:  bzip2
BuildRequires:  curl
BuildRequires:  systemd-rpm-macros
# The loader: Luma's noopenh264 uses /var/lib/openh264/libopenh264.so.8 when
# this package has downloaded it, and stays a stub otherwise.
Requires:       noopenh264 >= 2.6.0-4.luma.1
Requires:       bzip2
Requires:       coreutils
Requires:       curl
# Settings switches it with pkexec (org.projectluma.openh264).
Requires:       polkit
Requires:       systemd

%description
Luma's software H.264 encoder and decoder (GStreamer's openh264 plugin and
FFmpeg's libopenh264) need Cisco's OpenH264 binary, which is covered by Cisco's
H.264 patent license only when Cisco distributes it. This package carries no
codec: luma-openh264.service downloads Cisco's binary from Cisco onto the
device, checks it against the SHA-256 recorded here, and installs the matching
org.projectluma.Platform.openh264 Flatpak extension, whose binary flatpak also
fetches from Cisco. luma-openh264 enable/disable/status puts the user in
control of it.

OpenH264 Video Codec provided by Cisco Systems, Inc.

%prep
%autosetup -n luma-openh264
cp %{SOURCE1} LICENSE.md
# A checkout with default ACLs must not leak group-only modes into the package.
chmod -R u=rwX,go=rX .

%build

%install
install -D -m 0755 luma-openh264 %{buildroot}%{_bindir}/luma-openh264
install -D -m 0644 cisco-openh264.conf %{buildroot}%{_datadir}/luma-openh264/cisco-openh264.conf
install -D -m 0644 NOTICE %{buildroot}%{_datadir}/luma-openh264/NOTICE
install -D -m 0644 luma-openh264.service %{buildroot}%{_unitdir}/luma-openh264.service
install -D -m 0644 luma-openh264.timer %{buildroot}%{_unitdir}/luma-openh264.timer
install -D -m 0644 luma-openh264.tmpfiles %{buildroot}%{_tmpfilesdir}/luma-openh264.conf
install -D -m 0644 org.projectluma.openh264.policy %{buildroot}%{_datadir}/polkit-1/actions/org.projectluma.openh264.policy
install -D -m 0644 luma-openh264.rules %{buildroot}%{_datadir}/polkit-1/rules.d/50-luma-openh264.rules
# On by default: the wants link covers images and upgrades, the preset covers
# systemctl preset(-all). The setting itself is /etc/luma-openh264/disabled.
install -d %{buildroot}%{_unitdir}/timers.target.wants
ln -s ../luma-openh264.timer %{buildroot}%{_unitdir}/timers.target.wants/luma-openh264.timer
install -d %{buildroot}%{_presetdir}
printf 'enable luma-openh264.timer\n' > %{buildroot}%{_presetdir}/60-luma-openh264.preset
chmod -R u=rwX,go=rX %{buildroot}

%check
bash tests/test-luma-openh264.sh %{buildroot}%{_bindir}/luma-openh264
# The recorded files are Cisco's 2.6.0 builds for both architectures.
. ./cisco-openh264.conf
test "$OPENH264_SONAME" = libopenh264.so.8
# pkexec runs the tool itself; the rule allows exactly enable and disable.
grep -q '<annotate key="org.freedesktop.policykit.exec.path">%{_bindir}/luma-openh264</annotate>' org.projectluma.openh264.policy
grep -q '"/usr/bin/luma-openh264"' luma-openh264.rules
grep -q 'var requests = \["enable", "disable"\];' luma-openh264.rules
for arch in X86_64 AARCH64; do
  url=OPENH264_${arch}_URL
  case ${!url} in https://ciscobinary.openh264.org/libopenh264-${OPENH264_VERSION}-linux*.8.so.bz2) ;; *) exit 1 ;; esac
done

%post
%systemd_post luma-openh264.timer

%preun
%systemd_preun luma-openh264.timer luma-openh264.service

%postun
%systemd_postun luma-openh264.timer

%files
%license LICENSE.md
%doc NOTICE
%{_bindir}/luma-openh264
%{_datadir}/luma-openh264/
%{_unitdir}/luma-openh264.service
%{_unitdir}/luma-openh264.timer
%{_unitdir}/timers.target.wants/luma-openh264.timer
%{_presetdir}/60-luma-openh264.preset
%{_tmpfilesdir}/luma-openh264.conf
%{_datadir}/polkit-1/actions/org.projectluma.openh264.policy
%{_datadir}/polkit-1/rules.d/50-luma-openh264.rules

%changelog
* Fri Nov 20 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.2
- polkit: the active local session may run luma-openh264 enable and disable
  (Settings › Apps)

* Thu Nov 19 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.1
- First release: download Cisco's OpenH264 2.6.0 binary from Cisco for the OS
  and the Flatpak runtime, with enable, disable and status
