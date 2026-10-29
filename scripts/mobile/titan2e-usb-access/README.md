# Opt-in Titan USB administrative access

This is an owner-invoked recovery tool, not a default phone-image component.
It adds USB ECM using the existing Titan kernel, a NetworkManager profile with
no default route, and a socket bound only to `titanusb0`, `172.16.42.1:2222`.
The USB peer uses `02:54:49:54:41:02` and `172.16.42.2/30`.

An explicitly supplied Ed25519 key admits root administration only from that
USB peer. Password, keyboard-interactive and empty-password authentication,
forwarding, and tunnelling are disabled. The normal SSH configuration and
account databases are untouched. Keep the corresponding private key private;
never commit device keys, captured logs, firmware, or personal card contents.

On a Linux operator host, mount the owner's powered-off Titan card, confirm its
UUID and deployment, and supply the public key:

```sh
sudo python3 install.py --root CARD_MOUNT --expected-uuid CARD_UUID --public-key KEY.pub
```

The installer requires a separate writable ext4 card mount with matching UUID
and exactly one Titan deployment. It refuses existing files or symlinked parent
paths and only adds files in that deployment's `/etc`. It never changes `/usr`,
boot partitions, bootloader slots, firmware or subscriber identity. The manifest
at `/etc/project-titan/installed.json` records the new files and enable links.

To revoke access on the running phone, stop and disable
`project-titan-sshd.socket` and `project-titan-usb.service`, then remove only the
files and links in the manifest. The same removal can be performed with the
powered-off card mounted on the operator host. Existing configuration does not
need restoring because the installer never overwrites it. Remove the dedicated
host USB profile and revoke/destroy its private key when retiring the access.

Checks on a server establish source/configuration properties only. USB
enumeration, interface identity, key authentication, rejection of other peers,
and normal cold-boot behavior require the actual Titan and operator host.
