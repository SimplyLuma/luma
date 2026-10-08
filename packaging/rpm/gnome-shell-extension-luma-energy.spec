# SPDX-License-Identifier: MPL-2.0

%global extension_uuid energy@project-luma.local

Name:           gnome-shell-extension-luma-energy
Version:        0.1.0
Release:        1.luma.1%{?dist}
Summary:        Tells Luma Energy which applications the person can actually see
License:        MPL-2.0
URL:            https://projectluma.org/developer/platform/energy
Source0:        extension.js
Source1:        metadata.json
Source2:        README.md
Source3:        LICENSE
BuildArch:      noarch
BuildRequires:  nodejs
BuildRequires:  python3
Requires:       gnome-shell >= 50

%description
On Wayland nothing tells an application that it is completely covered by
another window, so a browser or Electron window buried under a maximised
window believes it is visible and keeps painting. The compositor is the only
component that knows otherwise. This extension reports which applications are
focused, visible, occluded or hidden to the Luma Energy service, and wakes
applications before the person needs them.

It reports only. It holds no policy, applies no limit, and does nothing at all
when Luma Energy is not running.

%prep
cp %{SOURCE3} LICENSE

%build

%check
node --check %{SOURCE0}
python3 -c "import json,sys; d=json.load(open(sys.argv[1])); \
assert d['uuid'] == '%{extension_uuid}', d; \
assert '50' in d['shell-version'], d" %{SOURCE1}
# It must stay a reporter. Anything that writes a limit belongs in the service,
# where it can be undone on the way out; a Shell extension that sets cgroup
# properties would leave them behind every time the Shell restarts.
! grep -qE 'cpu\.(weight|max|uclamp)|cgroup\.freeze|systemctl' %{SOURCE0}
# It must never make the Shell wait on another process to draw a frame.
! grep -qE '_sync\(|call_sync' %{SOURCE0}

%install
install -D -m 0644 %{SOURCE0} \
  %{buildroot}%{_datadir}/gnome-shell/extensions/%{extension_uuid}/extension.js
install -D -m 0644 %{SOURCE1} \
  %{buildroot}%{_datadir}/gnome-shell/extensions/%{extension_uuid}/metadata.json
install -D -m 0644 %{SOURCE2} %{buildroot}%{_pkgdocdir}/README.md

%files
%license LICENSE
%doc %{_pkgdocdir}/README.md
%{_datadir}/gnome-shell/extensions/%{extension_uuid}/

%changelog
* Sun Sep 20 2026 Project Luma <maintainers@projectluma.org> - 0.1.0-1.luma.1
- First release. Reports which applications are focused, visible, occluded or
  hidden, and wakes applications before the person reaches them.
