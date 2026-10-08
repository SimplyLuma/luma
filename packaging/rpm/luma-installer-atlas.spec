# SPDX-License-Identifier: LGPL-2.1-or-later

Name:           luma-installer-atlas
Version:        0.1.0
Release:        1.luma.18.creator20261006.4%{?dist}
Summary:        Atlas, Luma's installer front end for Anaconda
# Atlas and the reused anaconda-webui 68 code: LGPL-2.1-or-later.
# Bundled React and React DOM: MIT. Bundled Figtree font: OFL-1.1.
License:        LGPL-2.1-or-later AND MIT AND OFL-1.1
URL:            https://github.com/rhinstaller/anaconda-webui
BuildArch:      noarch

# Prebuilt by scripts/packages/build-luma-installer-atlas.sh from
# src/luma-installer-atlas at an exact commit, with pinned npm and Cockpit inputs.
Source0:        luma-installer-atlas-dist.tar
Source1:        atlas-probe
Source2:        atlas-media
Source3:        90-luma-atlas.conf
Source4:        webui-desktop
Source5:        cockpit-coproc-wrapper.sh
Source6:        webui-cockpit-ws.service
Source7:        LICENSE.anaconda-webui
Source8:        README.md
Source9:        app-collections.json
Source10:       atlas-viewer
Source11:       atlas-viewer-fallback
Source12:       test_atlas_media.py

BuildRequires:  systemd-rpm-macros
BuildRequires:  python3

# Atlas is written against anaconda-webui 68's D-Bus client code, which Fedora
# 44 pairs with anaconda-core 44.30. Anaconda switches to the web UI when
# /usr/share/cockpit/anaconda-webui exists, so Atlas takes that path.
Requires:       anaconda-core >= 44.30
Requires:       cockpit-bridge >= 275
Requires:       cockpit-ws >= 275
Requires:       slitherer
Requires:       polkit
Requires:       python3
Requires:       ostree
Requires:       util-linux
Requires:       systemd
Requires:       libpwquality
# setfacl, for sharing the installer's Wayland display with the viewer
Requires:       acl
# atlas-viewer-fallback, the error screen drawn without a web engine
Requires:       gtk3
Requires:       python3-gobject
Provides:       anaconda-webui = 68
Conflicts:      anaconda-webui

Provides:       bundled(npm(react)) = 18.3.1
Provides:       bundled(npm(react-dom)) = 18.3.1
Provides:       bundled(npm(scheduler)) = 0.23.2
Provides:       bundled(npm(loose-envify)) = 1.4.0
Provides:       bundled(npm(js-tokens)) = 4.0.0
Provides:       bundled(font(Figtree)) = 2.0.3

%description
Atlas is the Luma installer: eight questions in Fedora 44's order, asked one
per screen, in front of Anaconda's own D-Bus modules. It reuses anaconda-webui
68's API clients and storage-plan model and replaces its PatternFly interface.

%prep
%setup -q -c -T
tar -xf %{SOURCE0}

%build
python3 -c "import ast, sys; [ast.parse(open(p).read(), p) for p in sys.argv[1:]]" %{SOURCE1} %{SOURCE2} %{SOURCE11}
bash -n %{SOURCE10}

%check
ATLAS_MEDIA_SOURCE=%{SOURCE2} python3 %{SOURCE12}

%install
install -d %{buildroot}%{_datadir}/cockpit/anaconda-webui
cp -a dist/. %{buildroot}%{_datadir}/cockpit/anaconda-webui/
install -D -m 0755 %{SOURCE1} %{buildroot}%{_libexecdir}/luma-installer-atlas/atlas-probe
install -D -m 0755 %{SOURCE2} %{buildroot}%{_libexecdir}/luma-installer-atlas/atlas-media
install -D -m 0755 %{SOURCE10} %{buildroot}%{_libexecdir}/luma-installer-atlas/atlas-viewer
install -D -m 0755 %{SOURCE11} %{buildroot}%{_libexecdir}/luma-installer-atlas/atlas-viewer-fallback
install -D -m 0644 %{SOURCE3} %{buildroot}%{_sysconfdir}/anaconda/conf.d/90-luma-atlas.conf
install -D -m 0755 %{SOURCE4} %{buildroot}%{_libexecdir}/anaconda/webui-desktop
install -D -m 0755 %{SOURCE5} %{buildroot}%{_libexecdir}/anaconda/cockpit-coproc-wrapper.sh
install -D -m 0644 %{SOURCE6} %{buildroot}%{_unitdir}/webui-cockpit-ws.service
install -D -m 0644 %{SOURCE7} %{buildroot}%{_licensedir}/%{name}/LICENSE.anaconda-webui
install -D -m 0644 dist/fonts/OFL.txt %{buildroot}%{_licensedir}/%{name}/OFL-Figtree.txt
install -D -m 0644 dist/index.js.LEGAL.txt %{buildroot}%{_licensedir}/%{name}/index.js.LEGAL.txt
install -D -m 0644 %{SOURCE8} %{buildroot}%{_docdir}/%{name}/README.md
# Read by the installer kickstart to validate "Get more apps" choices.
install -D -m 0644 %{SOURCE9} %{buildroot}%{_datadir}/%{name}/app-collections.json

%files
%license %{_licensedir}/%{name}
%doc %{_docdir}/%{name}/README.md
%{_datadir}/cockpit/anaconda-webui
%dir %{_libexecdir}/luma-installer-atlas
%{_libexecdir}/luma-installer-atlas/atlas-probe
%{_libexecdir}/luma-installer-atlas/atlas-media
%{_libexecdir}/luma-installer-atlas/atlas-viewer
%{_libexecdir}/luma-installer-atlas/atlas-viewer-fallback
%config(noreplace) %{_sysconfdir}/anaconda/conf.d/90-luma-atlas.conf
%{_libexecdir}/anaconda/webui-desktop
%{_libexecdir}/anaconda/cockpit-coproc-wrapper.sh
%{_unitdir}/webui-cockpit-ws.service
%dir %{_datadir}/%{name}
%{_datadir}/%{name}/app-collections.json

%changelog
* Thu Sep 17 2026 Project Luma contributors - 0.1.0-1.luma.8
- Name the release being installed exactly as the medium's payload os-release
  PRETTY_NAME says, on the review step ("Luma (Prairie, Beta 0, Nightly
  20260916) · from this USB drive") and the last step; plain "Luma" online.
- The installer kickstart names the new system with the installed image's
  luma-device-name helper ("Nick’s ThinkPad", nicks-thinkpad).
- build-installer-iso.sh --display-name names the boot menu and volume label.

* Wed Sep 16 2026 Project Luma contributors - 0.1.0-1.luma.7
- Rebuild at the Luma identity commit (ADR-040), so media name one package
  for one source revision. The package content matches 1.luma.6; the new
  system's default name (luma) and the installer environment's Luma name are
  in the installer kickstart and the ISO's runtime template.

* Tue Sep 15 2026 Project Luma contributors - 0.1.0-1.luma.6
- Rebuild at the installer-runtime kernel commit, so media name one package
  for one source revision. The package content matches 1.luma.5; the runtime
  booting the release's kernel (config/install/atlas/runtime-kernel.env) is
  part of the ISO build, not this package.

* Tue Sep 15 2026 Project Luma contributors - 0.1.0-1.luma.5
- A step's action stays at the foot of the room while the step scrolls behind
  it, and screens 840 px tall or shorter tighten the spacing, so Continue is
  never below the fold on a 1024x768 or 1280x800 laptop.

* Tue Sep 15 2026 Project Luma contributors - 0.1.0-1.luma.4
- atlas-viewer starts and watches the installer viewer: software rendering
  by default (Qt Quick software renderer, Chromium without the GPU), a restart
  in safe mode when Atlas draws no first frame, and a GTK error screen with the
  log locations instead of a silent grey window. Boot options
  inst.luma.viewer= and inst.luma.viewer.first-frame=.
- Atlas writes a heartbeat from drawn animation frames for the watchdog.
- Saved installer logs include /tmp/luma-atlas-viewer.log.

* Tue Sep 15 2026 Project Luma contributors - 0.1.0-1.luma.3
- atlas-probe reports whether the medium enrolled the computer with a staff
  preview credential (never the credential), and the last step then shows no
  enrollment note.

* Tue Sep 15 2026 Project Luma contributors - 0.1.0-1.luma.2
- Rebuild at the kargs.d installer commit under a new release, so staff media
  name one package for one source revision. The package content matches the
  last 1.luma.1 build; the kickstart changes (update channels, mirror list,
  composefs fstab, kargs.d) are part of the ISO build, not this package.

* Mon Sep 14 2026 Project Luma contributors - 0.1.0-1.luma.1
- First Atlas build on anaconda-webui 68 and anaconda-core 44.30, with the
  Get more apps step (ADR-028 Depot, section 14).
