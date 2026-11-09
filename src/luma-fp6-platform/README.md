# luma-fp6-platform

Fairphone 6 platform services for the shared ARM image, installed from the
Fairphone 6 device repository as a client-side layer:

- modem: cellular link, resume after sleep, safe shutdown ordering
  (rmtfs, tqftpserv, ModemManager);
- call audio routing (org.projectluma.AudioRoute1), ALSA UCM for the
  Fairphone 6 sound card, WirePlumber policy;
- Bluetooth address from the persist partition (read-only);
- camera GPU fault guard;
- wall clock kept across shutdown (no RTC battery);
- the A/B slot marked successful after each boot (qbootctl -m);
- the handheld device class and the shared handheld dconf defaults
  (config/mobile/luma-shell-handheld.dconf);
- updates on request only (/etc/luma/update.conf).

Units are enabled by the package itself (wants links in /usr), including
rmtfs and tqftpserv from the base image, so a layered install needs no preset.
