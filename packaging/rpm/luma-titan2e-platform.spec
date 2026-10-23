# SPDX-License-Identifier: Apache-2.0
Name: luma-titan2e-platform
Version: 0.1.0
Release: 1.luma.52%{?dist}
Summary: Unihertz Titan 2 Elite platform adapters
License: Apache-2.0
Source0: luma-titan2e-boot-ok
Source1: luma-titan2e-boot-ok.service
Source2: 50-luma-titan2e-platform.preset
Source3: 50-titan2e.env
Source4: phoc-titan2e.ini
Source5: luma-titan2e-phoc-config
Source6: 50-titan2e-watchdog.conf
Source7: luma-titan2e-connsys-up
Source8: luma-titan2e-connsys.service
Source9: luma-titan2e-connsys-fw
Source10: luma-titan2e-gpu-up
Source11: luma-titan2e-gpu.service
Source12: luma-titan2e-panthor.conf
Source13: luma-titan2e-modem-up
Source14: luma-titan2e-modem-fs
Source15: luma-modemd
Source16: luma-titan2e-modem.service
Source17: 78-luma-titan2e-modem.rules
Source18: luma-titan2e-audio-up
Source19: luma-titan2e-audio.service
Source20: ucm2-Titan2Elite.conf
Source21: ucm2-Titan2Elite-HiFi.conf
Source22: 52-luma-titan2e-audio.conf
Source23: libcamera-titan2e.yaml
Source24: 53-luma-titan2e-camera.conf
Source25: luma-titan2e-healthcheck
Source26: 50-luma-titan2e-greenboot.conf
Source27: luma-titan2e-boot-bless
Source28: luma-titan2e-boot-bless.service
Source29: luma-titan2e-growfs
Source30: luma-titan2e-growfs.service
Source31: luma-esim-transport
Source32: luma-titan2e-boot-android
Source33: luma-titan2e-speech
Source34: ucm2-Titan2Elite-VoiceCall.conf
Source35: luma-update.conf
Source36: luma-titan2e-esim
Source37: org.projectluma.Titan2e.Modem1.conf
Source38: test-esim.py
BuildArch: noarch
BuildRequires: systemd-rpm-macros
BuildRequires: python3
Requires: python3
Requires: python3-gobject-base
Requires: luma-esim-broker >= 0.1.0-12.luma1
Requires: lpac-titan2e >= 2.3.0-6.luma1
Requires: luma-phosh
Requires: bluez
Requires: util-linux
Requires: kmod
Requires: linux-firmware >= 20260309
Requires: alsa-ucm
Requires: wireplumber
Requires: libcamera-ipa
Requires: libcamera-tools
Requires: pipewire-plugin-libcamera

%description
Boot-slot confirmation, hardware watchdog, compositor environment, output
scale, the GPU, Wi-Fi/Bluetooth and modem loaders, the audio profile and
speaker amplifier firmware, and the camera's libcamera and WirePlumber
configuration for the Unihertz Titan 2 Elite (MediaTek MT6878).

%prep

%build

%install
install -D -m 0755 %{SOURCE0} %{buildroot}%{_libexecdir}/luma-titan2e-boot-ok
install -D -m 0644 %{SOURCE1} %{buildroot}%{_unitdir}/luma-titan2e-boot-ok.service
install -D -m 0644 %{SOURCE2} %{buildroot}%{_presetdir}/50-luma-titan2e-platform.preset
install -D -m 0644 %{SOURCE3} %{buildroot}%{_prefix}/lib/luma/session.d/50-titan2e.env
install -D -m 0644 %{SOURCE4} %{buildroot}%{_datadir}/luma-titan2e/phoc-titan2e.ini
install -D -m 0755 %{SOURCE5} %{buildroot}%{_libexecdir}/luma-titan2e-phoc-config
install -D -m 0644 %{SOURCE6} %{buildroot}%{_prefix}/lib/systemd/system.conf.d/50-titan2e-watchdog.conf
install -D -m 0755 %{SOURCE7} %{buildroot}%{_libexecdir}/luma-titan2e-connsys-up
install -D -m 0644 %{SOURCE8} %{buildroot}%{_unitdir}/luma-titan2e-connsys.service
install -D -m 0755 %{SOURCE9} %{buildroot}%{_libexecdir}/luma-titan2e-connsys-fw
install -D -m 0755 %{SOURCE10} %{buildroot}%{_libexecdir}/luma-titan2e-gpu-up
install -D -m 0644 %{SOURCE11} %{buildroot}%{_unitdir}/luma-titan2e-gpu.service
install -D -m 0644 %{SOURCE12} %{buildroot}%{_prefix}/lib/modprobe.d/luma-titan2e-panthor.conf
install -D -m 0755 %{SOURCE13} %{buildroot}%{_libexecdir}/luma-titan2e-modem-up
install -D -m 0755 %{SOURCE14} %{buildroot}%{_libexecdir}/luma-titan2e-modem-fs
install -D -m 0755 %{SOURCE15} %{buildroot}%{_libexecdir}/luma-modemd
install -D -m 0755 %{SOURCE36} %{buildroot}%{_libexecdir}/luma-titan2e-esim
install -D -m 0644 %{SOURCE37} %{buildroot}%{_datadir}/dbus-1/system.d/org.projectluma.Titan2e.Modem1.conf
install -D -m 0755 %{SOURCE33} %{buildroot}%{_libexecdir}/luma-titan2e-speech
install -D -m 0644 %{SOURCE16} %{buildroot}%{_unitdir}/luma-titan2e-modem.service
install -D -m 0644 %{SOURCE17} %{buildroot}%{_udevrulesdir}/78-luma-titan2e-modem.rules
install -D -m 0755 %{SOURCE18} %{buildroot}%{_libexecdir}/luma-titan2e-audio-up
install -D -m 0644 %{SOURCE19} %{buildroot}%{_unitdir}/luma-titan2e-audio.service
install -D -m 0644 %{SOURCE20} %{buildroot}%{_datadir}/alsa/ucm2/Titan2Elite/Titan2Elite.conf
install -D -m 0644 %{SOURCE21} %{buildroot}%{_datadir}/alsa/ucm2/Titan2Elite/HiFi.conf
install -D -m 0644 %{SOURCE34} %{buildroot}%{_datadir}/alsa/ucm2/Titan2Elite/VoiceCall.conf
install -d %{buildroot}%{_datadir}/alsa/ucm2/conf.d/Titan2Elite
ln -s ../../Titan2Elite/Titan2Elite.conf %{buildroot}%{_datadir}/alsa/ucm2/conf.d/Titan2Elite/Titan2Elite.conf
install -D -m 0644 %{SOURCE22} %{buildroot}%{_datadir}/wireplumber/wireplumber.conf.d/52-luma-titan2e-audio.conf
install -D -m 0644 %{SOURCE23} %{buildroot}%{_datadir}/libcamera/configuration.yaml
install -D -m 0644 %{SOURCE24} %{buildroot}%{_datadir}/wireplumber/wireplumber.conf.d/53-luma-titan2e-camera.conf
install -D -m 0755 %{SOURCE25} %{buildroot}%{_libexecdir}/luma-titan2e-healthcheck
install -D -m 0644 %{SOURCE35} %{buildroot}%{_sysconfdir}/luma/update.conf
install -D -m 0644 %{SOURCE26} %{buildroot}%{_unitdir}/greenboot-healthcheck.service.d/50-luma-titan2e-greenboot.conf
install -D -m 0755 %{SOURCE27} %{buildroot}%{_libexecdir}/luma-titan2e-boot-bless
install -D -m 0644 %{SOURCE28} %{buildroot}%{_unitdir}/luma-titan2e-boot-bless.service
install -D -m 0755 %{SOURCE29} %{buildroot}%{_libexecdir}/luma-titan2e-growfs
install -D -m 0644 %{SOURCE30} %{buildroot}%{_unitdir}/luma-titan2e-growfs.service
install -D -m 0644 %{SOURCE31} %{buildroot}%{_prefix}/lib/luma-esim-broker/transport
install -D -m 0755 %{SOURCE32} %{buildroot}%{_sbindir}/luma-titan2e-boot-android

%check
PYTHONDONTWRITEBYTECODE=1 python3 %{SOURCE38}

%post
%systemd_post luma-titan2e-boot-ok.service luma-titan2e-boot-bless.service luma-titan2e-growfs.service luma-titan2e-connsys.service luma-titan2e-gpu.service luma-titan2e-modem.service luma-titan2e-audio.service
%{_libexecdir}/luma-titan2e-phoc-config || :
if [ "$1" -gt 1 ]; then
  # luma.21: luma-titan2e-boot-ok moved from multi-user.target to
  # greenboot-success.target (move its link), behind the health check the
  # preset now enables. Both are idempotent.
  systemctl --no-reload reenable luma-titan2e-boot-ok.service >/dev/null 2>&1 || :
  systemctl --no-reload preset greenboot-healthcheck.service >/dev/null 2>&1 || :
fi

%preun
%systemd_preun luma-titan2e-boot-ok.service luma-titan2e-connsys.service luma-titan2e-gpu.service luma-titan2e-modem.service luma-titan2e-audio.service

%postun
%systemd_postun luma-titan2e-boot-ok.service luma-titan2e-connsys.service luma-titan2e-gpu.service luma-titan2e-modem.service luma-titan2e-audio.service
if [ "$1" -eq 0 ] && grep -q '^# Generated by luma-titan2e-platform' /etc/phosh/phoc.ini 2>/dev/null; then
  rm -f /etc/phosh/phoc.ini
fi
if [ "$1" -eq 0 ]; then
  rm -f /etc/luma/session.d/60-titan2e-renderer.env
fi

%triggerpostun -- luma-titan2e-platform < 0.1.0-1.luma.16
# Units added to the preset in an update, where %%systemd_post does not apply
# presets: luma-titan2e-audio.service (luma.8) copies the amplifier firmware;
# the FP6 carrier and modem units (luma.9, luma.11) are Qualcomm-only and a
# second AP-side IMS client must not register beside the modem's own.
# luma.16: boot blessing and the first-boot grow (both OSTree only), and
# greenboot's rollback trigger stays off.
systemctl --no-reload preset luma-titan2e-audio.service luma-fp6-carrier-pdc.service luma-fp6-primary-gw-session.service luma-fp6-imsd.service luma-fp6-cellular-link.service luma-fp6-modem-quiesce.service luma-titan2e-boot-bless.service luma-titan2e-growfs.service greenboot-set-rollback-trigger.service >/dev/null 2>&1 || :

%triggerin -- luma-phosh
%{_libexecdir}/luma-titan2e-phoc-config || :

%files
%defattr(-,root,root,0755)
%{_libexecdir}/luma-titan2e-boot-ok
%{_sbindir}/luma-titan2e-boot-android
%{_libexecdir}/luma-titan2e-phoc-config
%{_libexecdir}/luma-titan2e-connsys-up
%{_libexecdir}/luma-titan2e-connsys-fw
%{_libexecdir}/luma-titan2e-gpu-up
%{_libexecdir}/luma-titan2e-modem-up
%{_libexecdir}/luma-titan2e-modem-fs
%{_libexecdir}/luma-modemd
%{_libexecdir}/luma-titan2e-speech
%{_libexecdir}/luma-titan2e-esim
%{_datadir}/dbus-1/system.d/org.projectluma.Titan2e.Modem1.conf
%{_libexecdir}/luma-titan2e-audio-up
%config(noreplace) %{_sysconfdir}/luma/update.conf
%{_libexecdir}/luma-titan2e-healthcheck
%{_libexecdir}/luma-titan2e-boot-bless
%{_libexecdir}/luma-titan2e-growfs
%{_unitdir}/luma-titan2e-boot-ok.service
%{_unitdir}/luma-titan2e-connsys.service
%{_unitdir}/luma-titan2e-gpu.service
%{_unitdir}/luma-titan2e-modem.service
%{_unitdir}/luma-titan2e-audio.service
%{_unitdir}/luma-titan2e-boot-bless.service
%{_unitdir}/luma-titan2e-growfs.service
%dir %{_unitdir}/greenboot-healthcheck.service.d
%{_unitdir}/greenboot-healthcheck.service.d/50-luma-titan2e-greenboot.conf
%{_udevrulesdir}/78-luma-titan2e-modem.rules
%{_presetdir}/50-luma-titan2e-platform.preset
%{_prefix}/lib/systemd/system.conf.d/50-titan2e-watchdog.conf
%{_prefix}/lib/modprobe.d/luma-titan2e-panthor.conf
%dir %{_prefix}/lib/luma-esim-broker
%{_prefix}/lib/luma-esim-broker/transport
%dir %{_prefix}/lib/luma/session.d
%{_prefix}/lib/luma/session.d/50-titan2e.env
%dir %{_datadir}/luma-titan2e
%{_datadir}/luma-titan2e/phoc-titan2e.ini
%dir %{_datadir}/alsa/ucm2/Titan2Elite
%{_datadir}/alsa/ucm2/Titan2Elite/Titan2Elite.conf
%{_datadir}/alsa/ucm2/Titan2Elite/HiFi.conf
%{_datadir}/alsa/ucm2/Titan2Elite/VoiceCall.conf
%dir %{_datadir}/alsa/ucm2/conf.d/Titan2Elite
%{_datadir}/alsa/ucm2/conf.d/Titan2Elite/Titan2Elite.conf
%{_datadir}/wireplumber/wireplumber.conf.d/52-luma-titan2e-audio.conf
%{_datadir}/wireplumber/wireplumber.conf.d/53-luma-titan2e-camera.conf
%{_datadir}/libcamera/configuration.yaml

%changelog
* Fri Oct 23 2026 Project Luma - 0.1.0-1.luma.52
- When the data path of the automatic mobile data profile stays dead after a
  modem cycle, step it through the other internet APNs listed for the network.

* Fri Oct 23 2026 Project Luma - 0.1.0-1.luma.51
- Create the mobile data profile for a SIM no saved profile serves
  (autoconnect, bound to the SIM, provider APN for data and initial attach);
  existing profiles are never changed.

* Fri Oct 23 2026 Project Luma - 0.1.0-1.luma.50
- Probe the data path after each connect (DNS to the network's resolvers) and,
  when nothing answers, have ModemManager disable and enable the modem once:
  a PDN bound after a long refusal window after boot carried no traffic.

* Fri Oct 23 2026 Project Luma - 0.1.0-1.luma.49
- Hold a data call until IMS is registered after boot or radio on; a PDN
  activated earlier carried no traffic.

* Fri Oct 23 2026 Project Luma - 0.1.0-1.luma.48
- Wait until 5 s after a data context's deactivation before activating it
  again; NetworkManager's immediate reconnect got no traffic.

* Fri Oct 23 2026 Project Luma - 0.1.0-1.luma.47
- Bind the data PDN again after the radio was off (airplane mode, a new IA
  APN); the old binding carried no traffic.

* Fri Oct 23 2026 Project Luma - 0.1.0-1.luma.46
- Keep ModemManager's initial EPS bearer settings as given (bearer properties)
  in /var/lib/luma-titan2e-modem, so NetworkManager's unchanged settings
  compare equal and the radio is not power-cycled on each activation.

* Fri Oct 23 2026 Project Luma - 0.1.0-1.luma.45
- Return the initial attach IP type as ModemManager set it (empty until set),
  so an unchanged NetworkManager setting does not power-cycle the radio.

* Fri Oct 23 2026 Project Luma - 0.1.0-1.luma.44
- Data service: get and set the initial attach APN for ModemManager.

* Fri Oct 23 2026 Project Luma - 0.1.0-1.luma.43
- Re-activate the data context on every connect and send the data switch
  after the activation; track the activation state, since AT+CGACT? lists
  no contexts on the MIPC channel. Disconnect deactivates again.

* Fri Oct 23 2026 Project Luma - 0.1.0-1.luma.42
- Keep the data PDN bound across disconnects until the radio goes off, and
  report the network's IPv6 name servers to ModemManager.

* Fri Oct 23 2026 Project Luma - 0.1.0-1.luma.41
- Skip re-activating an active data context, bind the data call with the
  context's defined PDP type, and ignore MIPC zero padding entries.

* Fri Oct 23 2026 Project Luma - 0.1.0-1.luma.40
- Tag the modem for +CFUN=4 low power, so airplane mode switches the radio off.

* Fri Oct 23 2026 Project Luma - 0.1.0-1.luma.39
- Approve incoming calls the modem announces (APPROVE_INCOMING), so IMS calls
  are presented, and log call status indications.
- Have ModemManager poll the call list every 2 s while idle; incoming IMS
  calls are not reported on the AT port. Log call-related AT lines only.
- Register incoming-call approval at boot (AT+EIAREG and stock's call
  reporting) and answer approvals with the call id and sequence number.
- Packet data calls for ModemManager's bearers over D-Bus (data switch,
  PDN activation and ccmni binding); tag the AT port with the service.
- Keep the re-attach watchdog idle while the radio is off on request.

* Thu Oct 22 2026 Project Luma - 0.1.0-1.luma.38
- Record package directories as 0755 whatever the build tree's modes.
- Read modem indications on the MIPC indication port. Enable the modem's IMS
  once the subscription registers and answer its IMS PDN request; restart the
  radio after a sustained registration loss. LUMA_MODEM_IMS=0 disables IMS.

* Thu Oct 22 2026 Project Luma - 0.1.0-1.luma.37
- Restart the subscription's radio after setting the initial-attach APN; the
  modem uses it only for an attach started by the next power-on.

* Thu Oct 22 2026 Project Luma - 0.1.0-1.luma.36
- Set the initial-attach APN and allow data on the subscription at boot and after
  each eSIM handover, as the modem requires before LTE attach.

* Thu Oct 22 2026 Project Luma - 0.1.0-1.luma.35
- Use native eight-byte MIPC field alignment for eSIM and boot requests.

* Thu Oct 22 2026 Project Luma - 0.1.0-1.luma.34
- Preserve the established UART command deadline during bounded readiness checks.

* Thu Oct 22 2026 Project Luma - 0.1.0-1.luma.32
- Wait for SMS and PDP capabilities before returning an enabled eUICC to ModemManager.

* Thu Oct 22 2026 Project Luma contributors - 0.1.0-1.luma.31
- Bound the post-operation owner check independently of preparation.

* Thu Oct 22 2026 Project Luma - 0.1.0-1.luma.30
- Prove rejected profile-change channel closure through a coordinated native reset.

* Thu Oct 22 2026 Project Luma - 1.luma.29
- Inhibit before profile switching and coordinate native SIM2 reinitialization.
- Verify fresh card readiness before restoring the selected subscription.

* Thu Oct 22 2026 Project Luma - 1.luma.28
- Hand verified enabled-card transitions to ModemManager using its public inhibit API.
- Preserve radio-off state and retain exclusion on uncertain subscription writes.

* Thu Oct 22 2026 Project Luma - 1.luma.27
- Use native MIPC eUICC channels while ModemManager retains its UART.
- Require the native owned-FD broker and atomic Titan LPAC provider cohort.

* Thu Oct 22 2026 Project Luma - 1.luma.26
- Own SIM2 selection and protected eUICC transport in the native modem daemon.

* Tue Oct 20 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.24
- /etc/luma/update.conf: the updater follows the Titan 2 Elite channel
  (luma/1/aarch64/unihertz-titan2e/{channel}, its update graph).
- luma-modemd sets the modem boot data (RAT, MD log mode) before each start.

* Tue Oct 20 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.23
- Health check no longer waits for graphical.target, which multi-user.target
  holds back until the check finishes: greetd active, phoc and phosh running.

* Tue Oct 20 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.22
- Health check: the phone session (phosh) runs, as the FP6 Alpha 1 account
  starts it directly.
- Drop luma-titan2e-session-env: greetd starts luma-phosh-session, which reads
  session.d itself.

* Tue Oct 20 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.21
- luma-titan2e-boot-ok marks the LK slot only after greenboot's health check
  passes (greenboot-success.target), so a boot without a working graphical
  session falls back to slot b.
- Health check: greetd is the display manager and active, phoc runs; no longer
  waits for luma-titan2e-boot-ok.
- Preset enables greenboot-healthcheck.service, which nothing enabled on the
  Titan: without it neither the slot mark nor the OSTree bless ran.
- luma-titan2e-session-env: greetd session commands get the session.d device
  variables (renderer, render node) that only luma-phosh-session read.

* Tue Oct 20 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.20
- luma-titan2e-speech: modem speech on/off over /dev/ccci_aud, run by luma-modemd;
  holds the hostless speech PCM open during a call.
- UCM2 Titan2Elite "Voice Call" verb (earpiece, speaker, microphone).
- Earpiece and speaker are ports of one sink (DL0 feeds both), so media
  defaults to the speaker and callaudiod can switch the call output; drop the
  WirePlumber earpiece priority rule.

* Tue Oct 20 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.19
- luma-titan2e-boot-android: make the bootloader start Android on slot b
  next (the AOSP boot-control block in misc), then restart.

* Tue Oct 20 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.18
- luma-modemd: stock SIM bring-up and IMS configuration over MIPC.

* Tue Oct 20 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.17
- luma-esim-broker uses lpac's AT backend on a CCCI port, switching to SIM2
  around each operation (untested end to end).

* Tue Oct 20 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.16
- Boot counting with the release initramfs: bless the booted deployment once
  greenboot passes. Grow the microSD root to the whole card on first boot.
  greenboot's rollback trigger stays off (no qbootctl A/B on the Titan).

* Tue Oct 20 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.15
- luma-modemd reuses the lowest free FS handle, as the stock daemon does.

* Tue Oct 20 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.14
- Modem udev rules point ModemManager at the WWAN AT port.

* Tue Oct 20 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.13
- Run the required update health checks only on an OSTree deployment.

* Tue Oct 20 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.12
- greenboot health check for the Titan: the boot slot marked, graphical.target
  and phoc (plus the required update checks on an OSTree deployment), instead
  of the FP6 qbootctl backend that needs one.

* Tue Oct 20 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.11
- Disable the FP6 IMS, cellular-link and modem-quiesce units; the MT6878
  modem runs IMS itself.

* Tue Oct 20 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.10
- Order the preset before 60-luma-fp6-platform.preset so its entries win.

* Tue Oct 20 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.9
- Disable the FP6 carrier provisioning units, which fail on this hardware.

* Tue Oct 20 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.8
- Enable luma-titan2e-audio.service when updating from a release that did not
  have it, so the speaker amplifier firmware is copied.

* Mon Oct 19 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.7
- Camera: libcamera simple pipeline with the software ISP for the mtk-seninf
  raw capture driver, the raw V4L2 node hidden from WirePlumber, and the
  libcamera tools and PipeWire plugin the Camera app needs.

* Mon Oct 19 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.6
- Audio: ALSA UCM profile for the Titan2Elite card (speaker, earpiece, main
  microphone), WirePlumber policy (speaker first, idle suspend), and the
  speaker amplifier firmware copied from the vendor partition on first boot.

* Mon Oct 19 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.5
- Modem loader (luma-titan2e-modem.service, not enabled): CCCI modules, the
  modem's file store seeded once from read-only partition mounts, and
  luma-modemd (NVRAM file service, RPC, DRDI copy); ModemManager port hints.

* Mon Oct 19 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.4
- Composite on the Mali-G615: load panthor at boot with linux-firmware's CSF
  firmware (the vendor partition's copy as a fallback), and fall back to pixman
  when the GPU path is not available.

* Mon Oct 19 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.3
- Load the Wi-Fi/Bluetooth drivers at boot; copy their firmware and calibration
  data from the vendor and nvdata partitions on first boot.

* Mon Oct 19 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.2
- Arm the SoC watchdog from systemd.

* Mon Oct 19 2026 Project Luma <builds@project-luma.local> - 0.1.0-1.luma.1
- Boot-slot confirmation, compositor environment and output scale.
