#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Install explicitly opted-in USB administration into a mounted Titan card.

Does not repartition, flash, modify /usr, change standard SSH configuration,
read account passwords, or copy firmware or subscriber data.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess

DEVICE_MAC = "02:54:49:54:41:01"
PHONE_ADDRESS = "172.16.42.1"
PEER_ADDRESS = "172.16.42.2"


def public_key(text):
    fields = text.strip().split()
    if len(fields) < 2 or fields[0] != "ssh-ed25519" or "\n" in text.strip():
        raise ValueError("one plain Ed25519 public key required")
    data = base64.b64decode(fields[1], validate=True)
    if len(data) != 51 or data[:19] != b"\0\0\0\x0bssh-ed25519\0\0\0\x20":
        raise ValueError("invalid Ed25519 public key blob")
    return " ".join(fields[:2])


def payloads(key):
    authorized = f'from="{PEER_ADDRESS}",no-agent-forwarding,no-port-forwarding,no-X11-forwarding {public_key(key)} project-titan\n'
    return {
        "etc/project-titan/gadget.py": (Path(__file__).with_name("gadget.py").read_bytes(), 0o644),
        "etc/project-titan/authorized_keys": (authorized.encode(), 0o600),
        "etc/project-titan/enabled": (b"Owner opted in to physical USB administrative access.\n", 0o600),
        "etc/project-titan/sshd_config": (f"""# Endpoint is owned by project-titan-sshd.socket, not a listening daemon.
Include /etc/crypto-policies/back-ends/opensshserver.config
HostKey /etc/ssh/ssh_host_ed25519_key
AuthorizedKeysFile /etc/project-titan/authorized_keys
PermitRootLogin prohibit-password
AllowUsers root
AuthenticationMethods publickey
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitEmptyPasswords no
UsePAM yes
AllowTcpForwarding no
AllowAgentForwarding no
X11Forwarding no
PermitTunnel no
PermitUserEnvironment no
StrictModes yes
""".encode(), 0o600),
        "etc/NetworkManager/system-connections/project-titan-usb.nmconnection": (f"""[connection]
id=Project Titan USB
uuid=609da542-6560-4a29-bb1e-e8a581467ba7
type=ethernet
autoconnect=true

[ethernet]
mac-address={DEVICE_MAC}

[ipv4]
address1={PHONE_ADDRESS}/30
method=manual
never-default=true
may-fail=false

[ipv6]
method=disabled
""".encode(), 0o600),
        "etc/systemd/system/project-titan-usb.service": (b"""[Unit]
Description=Owner-enabled Titan administrative USB link
After=local-fs.target systemd-udevd.service
Before=NetworkManager.service
ConditionPathExists=/etc/project-titan/enabled

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/python3 /etc/project-titan/gadget.py
ExecStartPost=/usr/bin/ssh-keygen -A
ExecStop=/usr/bin/python3 /etc/project-titan/gadget.py --stop
TimeoutStartSec=30

[Install]
WantedBy=multi-user.target
""", 0o644),
        "etc/systemd/system/project-titan-sshd.socket": (f"""[Unit]
Description=Key-only administration on the Titan USB link
DefaultDependencies=no
Requires=project-titan-usb.service
After=project-titan-usb.service
Before=shutdown.target
Conflicts=shutdown.target
ConditionPathExists=/etc/project-titan/enabled

[Socket]
ListenStream={PHONE_ADDRESS}:2222
BindToDevice=titanusb0
FreeBind=yes
Accept=yes

[Install]
WantedBy=multi-user.target
""".encode(), 0o644),
        "etc/systemd/system/project-titan-sshd@.service": (b"""[Unit]
Description=Authenticated Titan USB administration session

[Service]
Type=exec
StandardInput=socket
StandardError=journal
ExecStartPre=/usr/sbin/sshd -t -f /etc/project-titan/sshd_config
ExecStart=/usr/sbin/sshd -i -e -f /etc/project-titan/sshd_config
""", 0o644),
    }


def safe_path(root, relative):
    path = root / relative
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("unsafe relative path")
    for ancestor in (path, *path.parents):
        if ancestor == root:
            break
        if ancestor.is_symlink():
            raise ValueError(f"refusing symlink at {ancestor}")
    return path


def install(deployment, key):
    files = payloads(key)
    links = {f"etc/systemd/system/multi-user.target.wants/{name}": f"/etc/systemd/system/{name}"
             for name in ("project-titan-usb.service", "project-titan-sshd.socket")}
    manifest_path = "etc/project-titan/installed.json"
    for relative in (*files, *links, manifest_path):
        path = safe_path(deployment, relative)
        if os.path.lexists(path):
            raise ValueError(f"existing path, refusing overwrite: {relative}")
    # All paths are new: removal is the rollback; existing configuration is untouched.
    created = []
    try:
        for relative, (data, mode) in files.items():
            path = safe_path(deployment, relative)
            path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            with path.open("xb") as handle:
                created.append(path)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            path.chmod(mode)
        for relative, target in links.items():
            path = safe_path(deployment, relative)
            path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            path.symlink_to(target)
            created.append(path)
        result = {"files": {p: hashlib.sha256(v[0]).hexdigest() for p, v in files.items()}, "links": links}
        path = safe_path(deployment, manifest_path)
        with path.open("x") as handle:
            created.append(path)
            handle.write(json.dumps(result, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        path.chmod(0o600)
        os.sync()
        return result
    except Exception:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--expected-uuid", required=True)
    parser.add_argument("--public-key", required=True, type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("root required")
    root = args.root.resolve(strict=True)
    if root == Path("/") or not os.path.ismount(root):
        raise SystemExit("a separate mounted card root is required")
    mount = json.loads(subprocess.check_output(["findmnt", "-J", "-M", str(root), "-o", "SOURCE,FSTYPE,UUID,OPTIONS"]))["filesystems"][0]
    if mount["uuid"] != args.expected_uuid or mount["fstype"] != "ext4" or "rw" not in mount["options"].split(","):
        raise SystemExit("card UUID/type/read-write mount does not match")
    deployments = list((root / "ostree/deploy/luma/deploy").glob("*.0"))
    if len(deployments) != 1 or not deployments[0].is_dir() or deployments[0].is_symlink():
        raise SystemExit("expected exactly one deployed system")
    deployment = deployments[0]
    identity = (deployment / "usr/lib/os-release").read_text()
    if "IMAGE_ID=luma-mobile-titan2e\n" not in identity:
        raise SystemExit("not a Titan mobile deployment")
    # No account secrets are read; the original SSH host identity is retained.
    if not (deployment / "usr/bin/python3").exists() or not (deployment / "usr/sbin/sshd").is_file():
        raise SystemExit("required runtime is absent")
    result = install(deployment, args.public_key.read_text())
    print(json.dumps({"deployment": deployment.name, **result}, indent=2))


if __name__ == "__main__":
    main()
