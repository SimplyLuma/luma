# NetworkManager

Fedora's NetworkManager, rebuilt by `scripts/packages/build-networkmanager.sh`
from the SRPM pinned in `config/desktop/inputs.env` (desktop-components group
`networkmanager`, both architectures). Every subpackage the Silverblue base
installs is pinned in `config/desktop/packages.txt`, because they require the
daemon's exact version.

- `0001-manager-keep-a-wowlan-wi-fi-connection-that-survived-suspend.patch`:
  NetworkManager skips Wake-on-LAN/WoWLAN devices when the system suspends
  and takes them down after the wake to recheck connectivity. For Wi-Fi with
  WoWLAN that discards the association the firmware kept: every resume goes
  unavailable, disconnected, scan, reconnect. On a Pixel 8 Pro (bcmdhd,
  s2idle) that is about 13 s without network per wake, open connections
  drop, and a suspend during the reconnect can fail with `-EBUSY` while the
  driver holds its post-connect wakeup source. With the patch a Wi-Fi WoWLAN
  device that is still activated and has carrier after the wake only renews
  its DHCP lease. Tested on the Pixel 8 Pro: 8 suspends of 20 s to 5 minutes,
  including suspends 5 s after a wake, stayed connected with no lost pings
  to the gateway. Not yet tested: losing the association while asleep (no
  carrier, so the old teardown path runs) and a lease that expired during a
  long sleep (the renewal starts a fresh DHCP transaction). Written for
  upstream NetworkManager; drop it once upstream has an equivalent.
- `0000-luma-fedora-spec.patch`: adds the patch and the `luma.N` release.
