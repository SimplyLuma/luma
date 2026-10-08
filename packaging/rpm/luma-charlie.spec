# SPDX-License-Identifier: Apache-2.0

Name:           luma-charlie
Version:        0.2.14
Release:        1.luma.6.creator20261007.1%{?dist}
Summary:        Native adaptive mail client for Project Luma
License:        Apache-2.0
URL:            https://opencharlie.com/
Source0:        charlie-luma.tar.gz
Source1:        LICENSE.md
Source2:        org.project_luma.shell-state.gschema.xml

BuildArch:      noarch
BuildRequires:  meson >= 1.3
BuildRequires:  ninja-build
BuildRequires:  python3-devel >= 3.11
BuildRequires:  desktop-file-utils
BuildRequires:  libappstream-glib
BuildRequires:  librsvg2-tools
# %%check runs the agent tests: a local TLS IMAP server and GLib notification wire format.
BuildRequires:  openssl
BuildRequires:  python3-gobject-base
# %%check drives the real window with key presses: one-key shortcuts must not
# fire while typing. Install the pinned luma-developer-platform RPM first.
BuildRequires:  python3-gobject
BuildRequires:  gtk4 >= 4.22
BuildRequires:  libadwaita >= 1.9
BuildRequires:  luma-developer-platform >= 0.1.0-1.luma.94.creator20261006.1
BuildRequires:  webkitgtk6.0 >= 2.48
BuildRequires:  libsecret >= 0.21
BuildRequires:  xorg-x11-server-Xvfb
BuildRequires:  libXtst
BuildRequires:  dbus-daemon
Requires:       luma-application-installer >= 0.1.0-1.luma.58
Requires:       python3 >= 3.11
Requires:       python3-gobject
Requires:       gtk4 >= 4.22
Requires:       libadwaita >= 1.9
Requires:       luma-developer-platform >= 0.1.0-1.luma.94.creator20261006.1
Requires:       webkitgtk6.0 >= 2.48
Requires:       libsecret >= 0.21
Requires:       evolution-data-server >= 3.54
Requires:       gnome-online-accounts >= 3.54

%description
Charlie is Luma's responsive first-party mail application. It uses the native
GTK 4, libadwaita and Luma AppKit frame and keeps its protocol, storage and
search engine independent from presentation.

%prep
%autosetup -n charlie-luma
cp %{SOURCE1} LICENSE.md

%build
%meson
%meson_build -j4

%install
%meson_install
desktop-file-validate %{buildroot}%{_datadir}/applications/org.projectluma.Charlie.desktop
desktop-file-validate %{buildroot}%{_sysconfdir}/xdg/autostart/org.projectluma.Charlie.Agent.desktop

%check
PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}" %{python3} -m unittest discover -s tests -v
appstream-util validate-relax --nonet data/org.projectluma.Charlie.metainfo.xml
PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}" %{python3} -m py_compile charlie_luma/*.py
# Import the installed payload from outside the source tree. Source-PYTHONPATH
# tests cannot detect a required Python module omitted from Meson install.
installed_site=%{buildroot}%{python3_sitelib}
source_import_check=$PWD/tests/installed_payload_import.py
(cd /tmp && PYTHONPATH="$installed_site${PYTHONPATH:+:$PYTHONPATH}" PYTHONDONTWRITEBYTECODE=1 \
  %{python3} "$source_import_check" "$installed_site")
runtime=$(mktemp -d)
xvfb-run -a --server-args="-screen 0 1440x900x24" dbus-run-session -- \
  env GSK_RENDERER=cairo GTK_A11Y=none GTK_USE_PORTAL=0 XDG_RUNTIME_DIR="$runtime" \
    CHARLIE_RESOURCE_DIR="%{buildroot}%{_datadir}/charlie" \
    PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}" %{python3} tests/single_key_shortcuts_runtime.py
for appearance in light dark; do
  xvfb-run -a --server-args="-screen 0 1920x1080x24" dbus-run-session -- \
    env GSK_RENDERER=cairo GTK_A11Y=none GTK_USE_PORTAL=0 XDG_RUNTIME_DIR="$runtime" \
      CHARLIE_TEST_COLOR_SCHEME="$appearance" LUMA_CREATOR_SCHEMA_FILE=%{SOURCE2} \
      CHARLIE_RESOURCE_DIR="%{buildroot}%{_datadir}/charlie" \
      PYTHONPATH="$installed_site${PYTHONPATH:+:$PYTHONPATH}" %{python3} tests/v71_window_runtime.py
done
# The WebKit reader must paint an actual local mail document at responsive widths,
# not just import or allocate its containing window. JS and storage remain off.
xvfb-run -a --server-args="-screen 0 1440x1000x24" dbus-run-session -- \
  env GSK_RENDERER=cairo GTK_A11Y=none GTK_USE_PORTAL=0 XDG_RUNTIME_DIR="$runtime" \
    LUMA_CREATOR_SCHEMA_FILE=%{SOURCE2} CHARLIE_RESOURCE_DIR="%{buildroot}%{_datadir}/charlie" \
    PYTHONPATH="$installed_site${PYTHONPATH:+:$PYTHONPATH}" %{python3} tests/creator_followup_runtime.py

xvfb-run -a --server-args="-screen 0 800x600x24" dbus-run-session -- \
  env GSK_RENDERER=cairo GTK_A11Y=none GTK_USE_PORTAL=0 XDG_RUNTIME_DIR="$runtime" \
    PYTHONPATH="$installed_site${PYTHONPATH:+:$PYTHONPATH}" %{python3} tests/html_reader_runtime.py

%files
%license LICENSE.md
%{_bindir}/org.projectluma.Charlie
%{python3_sitelib}/charlie_luma/
%{_datadir}/applications/org.projectluma.Charlie.desktop
%{_datadir}/metainfo/org.projectluma.Charlie.metainfo.xml
%{_datadir}/dbus-1/services/org.projectluma.Charlie.service
%{_datadir}/dbus-1/services/org.projectluma.MailHost1.service
%dir %{_datadir}/luma
%dir %{_datadir}/luma/background
%{_datadir}/luma/background/org.projectluma.Charlie.toml
%config(noreplace) %{_sysconfdir}/xdg/autostart/org.projectluma.Charlie.Agent.desktop
%{_datadir}/gnome-shell/search-providers/org.projectluma.Charlie.search-provider.ini
%{_datadir}/icons/hicolor/*/apps/org.projectluma.Charlie.*
%{_datadir}/charlie/charlie.css

%changelog
* Wed Oct 07 2026 Project Luma <maintainers@projectluma.org> - 0.2.14-1.luma.5.creator20261007.1
- Keep keyring, OAuth and mail transport in the native signed-application broker
- Share only the declared mailbox dataset with the independently updated UI
- Validate attachment bytes, connection identity and bounded owner-bound jobs

* Tue Oct 06 2026 Project Luma <maintainers@projectluma.org> - 0.2.14-1.luma.4.creator20261006.1
- Give formatted mail the full responsive lane so native WebKit can paint its body
- Pin Add account below mailbox scrolling and keep a shared gap between panes
- Use the shared search header and account empty state/provider navigation controls
- Check actual HTML pixels, responsive allocations and native account transitions

* Mon Oct 05 2026 Project Luma <maintainers@projectluma.org> - 0.2.14-1.luma.3.creator20261005.1
- Keep the shared mailbox Island surface and rim on the same native bounds
- Preserve inner content spacing and phone ownership across resize transitions
- Give Quit its canonical icon; verify actual frame geometry and action behavior

* Fri Oct 02 2026 Project Luma <maintainers@projectluma.org> - 0.2.14-1.luma.2~mobile20261002.1
- Install the shared v71 window and fixture modules required by the native app
- Import the actual installed Python payload during package checks
- Generate all icon exports from approved canonical v71 SVG artwork
- Exercise the shared adaptive window and guarded shortcuts with actual GTK

* Fri Sep 18 2026 Project Luma <maintainers@projectluma.org> - 0.2.14-1.luma.1
- Replace raw account plumbing with provider-first AppKit enrollment and Microsoft OAuth
- One-key shortcuts (E archive, Delete move to Trash) no longer fire while
  typing: they were application accelerators, which GTK runs before the
  focused field, so "e" typed in the inline reply archived the conversation.
  They now run from the window after the focused widget, only when focus is
  not in a text field; the menu still shows the key

* Thu Sep 17 2026 Project Luma <maintainers@projectluma.org> - 0.2.13-1.luma.1
- Withdraw a stale stable-ID sign-in notification after proven authentication

* Thu Sep 17 2026 Project Luma <maintainers@projectluma.org> - 0.2.12-1.luma.1
- Recover stale Gmail access tokens, resolve cached avatars and open formatted mail surfaces

* Wed Sep 16 2026 Project Luma <maintainers@projectluma.org> - 0.2.11-1.luma.1
- Expand formatted mail completely inline with one compact full-width collapse control

* Wed Sep 16 2026 Project Luma <maintainers@projectluma.org> - 0.2.10-1.luma.1
- Strengthen the HTML preview fade and make Show less a comfortable full-width footer

* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 0.2.9-1.luma.1
- Expand formatted mail inside its conversation with a centered fade affordance

* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 0.2.8-1.luma.1
- Render every stored UTC mail timestamp in the person's current local timezone

* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 0.2.7-1.luma.2
- Follow the published luma-background agent contract: BackgroundAgent1, installed
  declaration, no shipped unit or activation file, RequestBackground from the window

* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 0.2.7-1.luma.1
- Check mail in a windowless background agent with IMAP IDLE and notifications

* Mon Sep 14 2026 Project Luma <maintainers@projectluma.org> - 0.2.6-1.luma.1
- Start for Shell search without opening a window

* Mon Sep 14 2026 Project Luma <maintainers@projectluma.org> - 0.2.5-1.luma.1
- Distinguish IMAP connection aborts from rejected account credentials

* Mon Sep 14 2026 Project Luma <maintainers@projectluma.org> - 0.2.4-1.luma.1
- Minimize the primary window on close while keeping Charlie resident

* Mon Sep 14 2026 Project Luma <maintainers@projectluma.org> - 0.2.3-1.luma.1
- Bake the Luma platform silhouette into the freedesktop application icon

* Sun Sep 13 2026 Project Luma <maintainers@projectluma.org> - 0.2.2-1.luma.1
- Simplify conversational HTML, quoted history and embedded images

* Sun Sep 13 2026 Project Luma <maintainers@projectluma.org> - 0.2.1-1.luma.1
- Polish conversations, formatted mail, attachments, threading and persistence

* Sun Sep 13 2026 Project Luma <maintainers@projectluma.org> - 0.2.0-1.luma.1
- Add real account enrollment and Google OAuth/XOAUTH2

* Sun Sep 13 2026 Project Luma <maintainers@projectluma.org> - 0.1.1-1.luma.1
- Polish the canonical pane measures and compact navigation

* Sun Sep 13 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.1
- Add the initial native adaptive Charlie mail candidate
