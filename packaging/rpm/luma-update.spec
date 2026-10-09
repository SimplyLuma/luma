# SPDX-License-Identifier: Apache-2.0

Name:           luma-update
Version:        1.0.0
Release:        1.luma.19%{?dist}
Summary:        Luma system update agent: signed channels, staged rollouts, rollback
License:        Apache-2.0
URL:            https://github.com/ProjectLuma/Luma
Source0:        luma-update-%{version}.tar.gz
Source1:        LICENSE.md
# luma-os-remote, copied by scripts/packages/build-luma-update.sh from their one source:
Source2:        luma.conf
Source3:        update-mirrorlist
Source4:        luma-os-release.asc
Source5:        luma-update-graph.pub
BuildArch:      noarch

BuildRequires:  python3-devel
BuildRequires:  systemd-rpm-macros
BuildRequires:  gnupg2

Requires:       luma-os-remote = %{version}-%{release}
Requires:       python3
Requires:       python3-gobject-base
Requires:       rpm-ostree
Requires:       ostree
Requires:       polkit
Requires:       dbus-common
Requires:       systemd
Recommends:     greenboot
Recommends:     NetworkManager
Recommends:     upower

# ADR-030 replaces the Recent channel client of ADR-015.
Obsoletes:      luma-update-client < 0.2.0-0
Provides:       luma-update-client = %{version}-%{release}

%description
luma-update keeps a Luma computer on its update channel (stable, beta or
nightly). luma-updated verifies the channel's minisign-signed update graph,
chooses a release with staged-rollout wariness, dead-ends and barriers, stages
that exact OSTree commit through rpm-ostree's D-Bus API, and asks the person to
restart. greenboot health checks confirm the new system and roll back on
failure. The package also provides the luma-update command, the session
notifier, polkit policy, and anonymous update reporting that can be switched
off.

%package -n luma-os-remote
Summary:        Luma's OSTree remote and release signing keys, for every channel
License:        Apache-2.0
Requires:       ostree

%description -n luma-os-remote
The "luma" OSTree remote (every channel, from the public repository named in
the root-only mirror list /etc/luma/update-mirrorlist), the Luma OS Release
public key it verifies commits and summaries with, and the update-graph public
key luma-update verifies release metadata with. With it, any Luma system,
including one built from another base, can start following Official, Beta or
Nightly (luma-update AdoptChannel) without an administrator setting anything up.

%prep
%autosetup -n luma-update-%{version}
cp %{SOURCE1} LICENSE.md

%build
gpg --batch --quiet --no-options --homedir "$PWD" --dearmor <%{SOURCE4} >luma-release.gpg
fingerprint=$(gpg --batch --no-options --homedir "$PWD" --with-colons --show-keys luma-release.gpg | awk -F: '$1 == "fpr" { print $10; exit }')
test "$fingerprint" = 7D3DAFCE2BCA13A2B68F2761C8CE1A1B51D96CE2
grep -Fxq 'url=mirrorlist=file:///etc/luma/update-mirrorlist' %{SOURCE2}
grep -Fxq 'gpgkeypath=/etc/pki/ostree/luma-release.gpg' %{SOURCE2}
python3 -c 'import luma_update.dbus_interface as d; print(d.introspection_xml(), end="")' \
  > org.projectluma.Update1.xml

%install
install -d %{buildroot}%{python3_sitelib}/luma_update
install -m 0644 luma_update/*.py %{buildroot}%{python3_sitelib}/luma_update/
install -D -m 0755 bin/luma-update %{buildroot}%{_bindir}/luma-update
install -D -m 0755 bin/luma-updated %{buildroot}%{_libexecdir}/luma-updated
install -D -m 0755 bin/luma-update-notifier %{buildroot}%{_libexecdir}/luma-update-notifier
install -D -m 0755 bin/luma-update-boot %{buildroot}%{_libexecdir}/luma-update-boot

for unit in luma-updated.service luma-update-check.service luma-updated.timer luma-update-boot.service; do
  install -D -m 0644 data/systemd/$unit %{buildroot}%{_unitdir}/$unit
done
for unit in luma-update-notifier.service luma-update-notifier.path luma-update-notifier.timer; do
  install -D -m 0644 data/systemd-user/$unit %{buildroot}%{_userunitdir}/$unit
done
install -D -m 0644 data/presets/80-luma-update.preset %{buildroot}%{_presetdir}/80-luma-update.preset
install -D -m 0644 data/presets/80-luma-update-user.preset %{buildroot}%{_userpresetdir}/80-luma-update-user.preset

install -D -m 0644 data/dbus/org.projectluma.Update1.conf %{buildroot}%{_datadir}/dbus-1/system.d/org.projectluma.Update1.conf
install -D -m 0644 data/dbus/org.projectluma.Update1.service %{buildroot}%{_datadir}/dbus-1/system-services/org.projectluma.Update1.service
install -D -m 0644 data/dbus/org.projectluma.Update.session.service %{buildroot}%{_datadir}/dbus-1/services/org.projectluma.Update.service
install -D -m 0644 org.projectluma.Update1.xml %{buildroot}%{_datadir}/dbus-1/interfaces/org.projectluma.Update1.xml
install -D -m 0644 data/polkit/org.projectluma.update.policy %{buildroot}%{_datadir}/polkit-1/actions/org.projectluma.update.policy
install -D -m 0644 data/polkit/49-org.projectluma.update.rules %{buildroot}%{_datadir}/polkit-1/rules.d/49-org.projectluma.update.rules
install -D -m 0644 data/applications/org.projectluma.Update.desktop %{buildroot}%{_datadir}/applications/org.projectluma.Update.desktop

# The agent's own health check and greenboot hooks live in greenboot's read-only
# /usr/lib tree; /etc stays for the administrator's checks. The display
# manager, shell and NetworkManager checks belong to the OS image (ADR-030
# section 10); luma-greenboot-common.sh gives them the same trial-boot rule.
install -D -m 0644 data/greenboot/luma-greenboot-common.sh %{buildroot}%{_prefix}/lib/luma-update/luma-greenboot-common.sh
for check in data/greenboot/check/required.d/*.sh; do
  install -D -m 0755 "$check" %{buildroot}%{_prefix}/lib/greenboot/check/required.d/$(basename "$check")
done
install -D -m 0755 data/greenboot/green.d/50-luma-update-record.sh %{buildroot}%{_prefix}/lib/greenboot/green.d/50-luma-update-record.sh
install -D -m 0755 data/greenboot/red.d/50-luma-update-record.sh %{buildroot}%{_prefix}/lib/greenboot/red.d/50-luma-update-record.sh

install -D -m 0755 data/NetworkManager/90-luma-update %{buildroot}%{_prefix}/lib/NetworkManager/dispatcher.d/90-luma-update
install -D -m 0644 data/config/update.conf %{buildroot}%{_prefix}/lib/luma/update.conf
install -D -m 0644 data/config/statistics.conf %{buildroot}%{_sysconfdir}/luma/statistics.conf
install -d -m 0755 %{buildroot}%{_prefix}/lib/luma-update/graph-keys.d
install -m 0644 %{SOURCE5} %{buildroot}%{_prefix}/lib/luma-update/graph-keys.d/luma-update-graph.pub
install -D -m 0644 %{SOURCE2} %{buildroot}%{_sysconfdir}/ostree/remotes.d/luma.conf
install -D -m 0600 %{SOURCE3} %{buildroot}%{_sysconfdir}/luma/update-mirrorlist
install -D -m 0644 luma-release.gpg %{buildroot}%{_sysconfdir}/pki/ostree/luma-release.gpg
install -d -m 0755 %{buildroot}%{_sysconfdir}/luma/update-graph-keys.d

install -d %{buildroot}%{_tmpfilesdir}
cat > %{buildroot}%{_tmpfilesdir}/luma-update.conf <<'EOF'
d /var/lib/luma-update 0700 root root -
d /run/luma-update 0755 root root -
EOF

%check
python3 -m unittest discover -s tests -p 'test_*.py'

%post
%systemd_post luma-updated.service luma-update-check.service luma-updated.timer luma-update-boot.service
%systemd_user_post luma-update-notifier.path luma-update-notifier.timer

%preun
%systemd_preun luma-updated.service luma-update-check.service luma-updated.timer luma-update-boot.service
%systemd_user_preun luma-update-notifier.path luma-update-notifier.timer

%postun
%systemd_postun luma-updated.timer luma-update-boot.service
%systemd_user_postun luma-update-notifier.path luma-update-notifier.timer

%files
%license LICENSE.md
%doc README.md
%{python3_sitelib}/luma_update/
%{_bindir}/luma-update
%{_libexecdir}/luma-updated
%{_libexecdir}/luma-update-notifier
%{_libexecdir}/luma-update-boot
%{_unitdir}/luma-updated.service
%{_unitdir}/luma-update-check.service
%{_unitdir}/luma-updated.timer
%{_unitdir}/luma-update-boot.service
%{_userunitdir}/luma-update-notifier.service
%{_userunitdir}/luma-update-notifier.path
%{_userunitdir}/luma-update-notifier.timer
%{_presetdir}/80-luma-update.preset
%{_userpresetdir}/80-luma-update-user.preset
%{_datadir}/dbus-1/system.d/org.projectluma.Update1.conf
%{_datadir}/dbus-1/system-services/org.projectluma.Update1.service
%{_datadir}/dbus-1/services/org.projectluma.Update.service
%{_datadir}/dbus-1/interfaces/org.projectluma.Update1.xml
%{_datadir}/polkit-1/actions/org.projectluma.update.policy
%{_datadir}/polkit-1/rules.d/49-org.projectluma.update.rules
%{_datadir}/applications/org.projectluma.Update.desktop
%dir %{_prefix}/lib/luma-update
%{_prefix}/lib/luma-update/luma-greenboot-common.sh
%dir %{_prefix}/lib/luma-update/graph-keys.d
%dir %{_prefix}/lib/greenboot
%dir %{_prefix}/lib/greenboot/check
%dir %{_prefix}/lib/greenboot/check/required.d
%dir %{_prefix}/lib/greenboot/green.d
%dir %{_prefix}/lib/greenboot/red.d
%{_prefix}/lib/greenboot/check/required.d/43-luma-updated.sh
%{_prefix}/lib/greenboot/green.d/50-luma-update-record.sh
%{_prefix}/lib/greenboot/red.d/50-luma-update-record.sh
%{_prefix}/lib/NetworkManager/dispatcher.d/90-luma-update
%dir %{_prefix}/lib/luma
%{_prefix}/lib/luma/update.conf
%dir %{_sysconfdir}/luma
%config(noreplace) %{_sysconfdir}/luma/statistics.conf
%dir %{_sysconfdir}/luma/update-graph-keys.d
%{_tmpfilesdir}/luma-update.conf

%files -n luma-os-remote
%license LICENSE.md
%config(noreplace) %{_sysconfdir}/ostree/remotes.d/luma.conf
%config(noreplace) %attr(0600,root,root) %{_sysconfdir}/luma/update-mirrorlist
%dir %{_sysconfdir}/pki/ostree
%{_sysconfdir}/pki/ostree/luma-release.gpg
%{_prefix}/lib/luma-update/graph-keys.d/luma-update-graph.pub

%changelog
* Sat Sep 26 2026 Project Luma <maintainers@projectluma.org> - 1.0.0-1.luma.17
- Updates stage when the new release ships a package this computer had added: an identical or older added copy is removed, a newer one is kept and the release's copy left out; both are reported.

* Mon Sep 21 2026 Project Luma <maintainers@projectluma.org> - 1.0.0-1.luma.16
- A refused early-updates pull now really retries from the public
  repository: rpm-ostree reports every failed pull as a transaction
  failure, so "No valid mirrors were found in mirrorlist" and HTTP 401/403
  are now recognised inside that wrapped error. Other transaction failures
  keep their class and never switch repositories.

* Mon Sep 21 2026 Project Luma <maintainers@projectluma.org> - 1.0.0-1.luma.15
- An update whose base now ships a package this computer had added
  (rpm-ostree install from a file or a repository) no longer fails with
  "cannot install both ... conflicting requests": the added request is
  removed with that update (a local package by NEVRA), logged, and listed
  in the new RemovedPackages property until the next staging.
- A revoked or unreachable preview credential no longer blocks a public
  channel: the pull is retried once from the public repository and the
  credential is set aside only when that works. A mirror list that still
  names a preview repository with no credential record uses the public one.
- An error and a last attempt recorded while another deployment was booted
  (or by an agent that never recorded which) are forgotten at start, so the
  first check after restarting into an update runs and the old error is
  not shown.

* Thu Sep 17 2026 Project Luma <maintainers@projectluma.org> - 1.0.0-1.luma.14
- Name the booted build from the graph when os-release names no build
  (luma-release's stage-only os-release layered onto an older image).

* Thu Sep 17 2026 Project Luma <maintainers@projectluma.org> - 1.0.0-1.luma.13
- Remember the graph's name for the booted release when nothing is available,
  so an up-to-date image from before release names shows its real name.

* Thu Sep 17 2026 Project Luma <maintainers@projectluma.org> - 1.0.0-1.luma.12
- Release names: status, D-Bus and the notifier say what people call a
  release ("Luma (Prairie, Beta 0, Nightly 20260917) is ready"), never
  "Luma 1.0.0-nightly..." or "Luma 1.0". New string properties BootedName,
  StagedName, AvailableName, WaitingName, RolledBackName and IgnoredName
  (JSON booted_name, ...); every existing property is unchanged.
- The booted system is named by /usr/lib/os-release PRETTY_NAME. Another
  version is named by the signed graph's optional display_name, else its
  deployment's own os-release, else derived from the version with the booted
  codename and stage. An image older than release names is derived too.
- The update graph's optional release display_name (at most 200 characters)
  is parsed and validated; graphs without it parse as before. Version
  comparison and target selection never read it.

* Wed Sep 16 2026 Project Luma <maintainers@projectluma.org> - 1.0.0-1.luma.11
- New subpackage luma-os-remote, required by luma-update: the "luma" OSTree
  remote for every channel (public repository through the root-only mirror
  list), the Luma OS Release public key and the update-graph public key, in
  the image's own paths. Any Luma system, including one built from another
  base, can start following Official, Beta or Nightly without an administrator
- AdoptChannel removes base package overrides made against the old base (the
  channel's versions are used); packages added on top stay, and the running
  deployment stays as the rollback

* Wed Sep 16 2026 Project Luma <maintainers@projectluma.org> - 1.0.0-1.luma.10
- Every channel is public (ADR-030 section 4, 2026-09-16): SetChannel,
  SetChannelNow, AdoptChannel and checks follow beta and nightly with no
  enrollment and no credential, from the image's one public repository
- AvailableChannels always lists stable, beta and nightly
- EnrollPreview and LeavePreview stay for compatibility; EnrollPreview on a
  computer that follows no channel but could (Adoptable) enrolls and adopts
- PreviewSource says who set up an existing credential (hub, staff-media,
  unknown) so Depot can say so
- Hub's answers are plain: 401 is SignInRequired, 403 not entitled, 429 wait
  a few minutes, other statuses try later
- A pull the download server refuses (HTTP 401/403, a revoked credential) is
  classed access-denied with a message that says how to get back

* Wed Sep 16 2026 Project Luma <maintainers@projectluma.org> - 1.0.0-1.luma.9
- A package added with sudo dnf install and already applied to the running
  system (rpm-ostree apply-live) no longer shows as a change waiting for a
  restart (ADR-038).

* Wed Sep 16 2026 Project Luma <maintainers@projectluma.org> - 1.0.0-1.luma.8
- After staging a release, run that release's luma-boot-hidden-menu so GRUB's
  menu stays hidden from the next start on computers that update instead of
  reinstalling; a failure is logged and never stops the update

* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 1.0.0-1.luma.7
- A computer that follows no Luma channel is no longer a dead end:
  AdoptChannel starts it following one, rebasing onto
  luma:luma/1/<arch>/<channel> and staging that channel's newest usable
  release, with the running deployment kept as the rollback
- Publish Adoptable and UnmanagedReason (other-origin, no-remote, no-key,
  no-image-system, busy) so Depot can say exactly what is true and what
  it would take, never only that something is unavailable
- Adoption is offered only where the Luma remote and an update-graph key
  are installed; the graph must still verify, and nothing is applied
  until a person restarts
- luma-update adopt <stable|beta|nightly> on the command line, and
  luma-update status says why an unmanaged computer follows nothing
* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 1.0.0-1.luma.6
- Updates are optional and visible: SetAutomaticDownload turns background
  downloading off (and stops one already running), IgnoreVersion silences
  one release without hiding it, and ClearIgnoredVersion is the way back
- Publish what Depot audits: AutomaticDownload, IgnoredVersion,
  RepositoryUrl read from the image's own OSTree remote with any preview
  credential redacted, GraphUrl, SignatureVerified, SigningKeyId,
  LastCheckReason and LastCheckAttempt
- The notifier says nothing about an ignored version and withdraws a
  notification already on screen for it
- luma-update automatic-download, ignore and unignore on the command line
* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 1.0.0-1.luma.5
- Leaving early updates never revokes a staff install medium's per-batch
  preview credential (record source staff-media) or any record without a
  per-device Luma Hub credential id; it resets the mirror list and removes
  the record only
- Enrollment records source hub and Hub's credential_id; only those
  credentials are revoked, after the local removal, without ever blocking
  or failing the channel change, and every outcome is logged

* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 1.0.0-1.luma.4
- Apply the kernel arguments a release declares in /usr/lib/bootc/kargs.d
  to the staged deployment through rpm-ostree's KernelArgs (append missing,
  remove only arguments the agent added that a release dropped); staging
  fails and is removed when they cannot be set

* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 1.0.0-1.luma.3
- Fix the findings of the independent review before release
- Mark a commit not to retry for good only after a failed health check;
  other marks expire after a week, a marked barrier is retried after a
  backoff and shows barrier-blocked instead of up to date, and the previous
  boot menu entry chosen once is not a failed update
- Treat times recorded while the clock ran ahead as elapsed
- Serialize the state store across threads; run report flushing,
  acknowledgement and boot reconciliation as operations
- Apply(): restart only into the agent's own update or rollback, only for a
  caller logind would allow (including other people's sessions), through
  RebootWithFlags with inhibitor checks for root
- Follow no redirect on Hub API requests, so bearer tokens stay with Hub
- Redact the preview credential from logs and D-Bus errors
- Do not download automatically when NetworkManager or UPower do not answer
- Log at error priority when greenboot trial-boot state cannot be read
- Add Cancel() and luma-update cancel; stop automatic downloads when the
  connection becomes metered or power runs low
- Stage promoted releases whose timestamps are older, classify
  min-free-space errors, remove a wrong staged commit, accept graphs for
  three days, and support the image's update-mirrorlist remote layout

* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 1.0.0-1.luma.2
- Rebuild from the launch integration branch, which carries Depot and the
  Luma remote beside the agent; no change to the agent's source, so the
  build does not share a NEVRA with the earlier 1.luma.1 file

* Tue Sep 15 2026 Project Luma <maintainers@projectluma.org> - 1.0.0-1.luma.1
- Replace luma-update-client with the ADR-030 update agent: luma-updated on
  D-Bus, signed graph with staged rollouts, dead-ends, barriers and signed
  rollback, exact-commit staging through rpm-ostree, greenboot trial-boot
  checks, notifier, anonymous reporting, preview enrollment and the
  luma-update command
