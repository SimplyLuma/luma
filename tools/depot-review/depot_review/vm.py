# SPDX-License-Identifier: Apache-2.0
"""Install, launch, screenshot and uninstall an app in a throwaway Luma VM.

A base disk is installed once from published Luma installation media (the
way Try Luma provisions its VM), with one person account that logs in
automatically and root SSH for this host only. Each run boots a copy-on-write
overlay of that disk, so nothing an app does survives the run.

  python3 -m depot_review.vm build-base --iso ISO --out DIR [--arch x86_64]
  python3 -m depot_review.vm run --base DISK --key KEY --package FILE --app-id ID --out DIR
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from pathlib import Path

USER = "luma-review"
WIDTH, HEIGHT = 1920, 1080


def log(message: str) -> None:
    print(time.strftime("%H:%M:%S"), message, file=sys.stderr, flush=True)


def kickstart(public_key: str, console: str) -> str:
    if not re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/]+={0,3}( \S+)?", public_key):
        raise ValueError("invalid review SSH key")
    return f'''text
lang en_US.UTF-8
keyboard us
timezone UTC --utc
network --bootproto=dhcp --device=link --activate --hostname=depot-review
ignoredisk --only-use=vda
zerombr
clearpart --all --initlabel --disklabel=gpt --drives=vda
autopart --type=btrfs --noswap
bootloader --timeout=1 --append="rhgb quiet console=tty0 console={console}"
firstboot --disable
user --name={USER} --gecos="Depot Review" --lock
rootpw --lock
sshkey --username=root "{public_key}"
services --enabled=sshd
firewall --enabled --service=ssh
poweroff
%include /usr/share/anaconda/interactive-defaults.ks
%post --erroronfail
set -e
mkdir -p /etc/gdm /var/lib/AccountsService/users /etc/dconf/db/local.d
printf '[daemon]\\nAutomaticLoginEnable=True\\nAutomaticLogin={USER}\\n' > /etc/gdm/custom.conf
printf '[User]\\nSystemAccount=false\\n' > /var/lib/AccountsService/users/{USER}
install -d -o {USER} -g {USER} /home/{USER}/.config /home/{USER}/.local /home/{USER}/.local/state /home/{USER}/.local/state/luma-setup
printf 'yes\\n' > /home/{USER}/.config/gnome-initial-setup-done
: > /home/{USER}/.local/state/luma-setup/welcome-done
chown {USER}:{USER} /home/{USER}/.config/gnome-initial-setup-done /home/{USER}/.local/state/luma-setup/welcome-done
cat > /etc/dconf/db/local.d/90-depot-review <<'EOF'
[org/gnome/desktop/session]
idle-delay=uint32 0
[org/gnome/desktop/screensaver]
lock-enabled=false
idle-activation-enabled=false
[org/gnome/desktop/lockdown]
disable-lock-screen=true
[org/gnome/desktop/notifications]
show-banners=false
EOF
dconf update || true
systemctl set-default graphical.target
restorecon -RF /etc/gdm /var/lib/AccountsService/users /home/{USER} /etc/dconf || true
%end
%post --nochroot
printf '\\nDEPOT_REVIEW_BASE_COMPLETE\\n' > /dev/{console}
%end
'''


def qemu_base(arch: str) -> list[str]:
    if arch == "x86_64":
        accel = ["-enable-kvm", "-cpu", "host"] if os.access("/dev/kvm", os.W_OK) else ["-cpu", "max"]
        return ["qemu-system-x86_64", "-machine", "q35", *accel]
    if arch == "aarch64":
        firmware = "/usr/share/edk2/aarch64/QEMU_EFI-silent-pflash.raw"
        return ["qemu-system-aarch64", "-machine", "virt", "-cpu", "max", "-bios",
                "/usr/share/edk2/aarch64/QEMU_EFI.fd" if not os.path.exists(firmware) else firmware]
    raise ValueError(arch)


def build_base(iso: Path, out: Path, arch: str = "x86_64", disk_gib: int = 40) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    key = out / "review-key"
    if not key.exists():
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "depot-review", "-f", str(key)],
                       check=True)
    public = key.with_suffix(".pub").read_text().strip()
    console = "ttyS0" if arch == "x86_64" else "ttyAMA0"
    work = Path(tempfile.mkdtemp(prefix="base-", dir=out))
    label = subprocess.run(["isoinfo", "-d", "-i", str(iso)], capture_output=True, text=True).stdout
    match = re.search(r"Volume id:\s*(\S+)", label)
    if not match:
        raise RuntimeError("cannot read the ISO volume label")
    label = match.group(1)
    for name in ("vmlinuz", "initrd.img"):
        with (work / name).open("wb") as target:
            subprocess.run(["bsdtar", "-xOf", str(iso), f"images/pxeboot/{name}"], stdout=target, check=True)
    (work / "ks.cfg").write_text(kickstart(public, console))
    subprocess.run(["xorriso", "-as", "mkisofs", "-quiet", "-o", str(work / "seed.iso"), "-V", "DEPOT_REVIEW_KS",
                    "-J", "-r", str(work / "ks.cfg")], check=True)
    disk = work / "base.qcow2"
    subprocess.run(["qemu-img", "create", "-q", "-f", "qcow2", str(disk), f"{disk_gib}G"], check=True)
    serial = work / "install-serial.log"
    command = qemu_base(arch) + [
        "-m", "6144", "-smp", "4", "-nographic", "-no-reboot",
        "-drive", f"file={disk},if=virtio,format=qcow2,cache=unsafe,discard=unmap",
        "-drive", f"file={iso},media=cdrom,readonly=on",
        "-drive", f"file={work / 'seed.iso'},media=cdrom,readonly=on",
        "-kernel", str(work / "vmlinuz"), "-initrd", str(work / "initrd.img"),
        "-append", f"inst.ks=hd:LABEL=DEPOT_REVIEW_KS:/ks.cfg inst.stage2=hd:LABEL={label} inst.text "
                   f"inst.notmux console={console}",
        "-nic", "user,model=virtio-net-pci", "-serial", f"file:{serial}", "-monitor", "none",
        "-object", "rng-random,filename=/dev/urandom,id=rng0", "-device", "virtio-rng-pci,rng=rng0",
    ]
    log(f"installing {iso.name} into {disk}")
    subprocess.run(command, check=True, timeout=5400, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    if "DEPOT_REVIEW_BASE_COMPLETE" not in serial.read_text(errors="replace"):
        raise RuntimeError(f"install did not complete; see {serial}")
    final = out / f"base-{arch}.qcow2"
    disk.replace(final)
    (out / f"base-{arch}.json").write_text(json.dumps({"iso": str(iso), "label": label,
                                                       "built": time.strftime("%FT%TZ", time.gmtime())}) + "\n")
    shutil.rmtree(work)
    prepare_base(final, arch)
    log(f"base ready: {final}")
    return final


# Jobs that only matter on a real computer and hold the desktop back in a
# throwaway VM: update checks, caches, Snap seeding, network-online waits.
REVIEW_MASKS = ("luma-update-check.service", "luma-update-check.timer",
                "fedora-atomic-desktop-appstream-cache-refresh.service",
                "fedora-atomic-desktop-mandb-update.service", "snapd.seeded.service", "snapd.service",
                "snapd.socket", "NetworkManager-wait-online.service")


def boot(disk: Path, arch: str, work: Path, port: int, memory: str = "4096", cpus: str = "2"):
    display = (["-device", f"virtio-vga,xres={WIDTH},yres={HEIGHT}"] if arch == "x86_64"
               else ["-device", f"virtio-gpu-pci,xres={WIDTH},yres={HEIGHT}"])
    command = qemu_base(arch) + [
        "-m", memory, "-smp", cpus, "-display", "none", *display,
        "-drive", f"file={disk},if=virtio,format=qcow2,cache=unsafe,discard=unmap",
        "-nic", f"user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:{port}-:22",
        "-qmp", f"unix:{work / 'qmp.sock'},server=on,wait=off", "-serial", f"file:{work / 'serial.log'}",
        "-monitor", "none",
        "-object", "rng-random,filename=/dev/urandom,id=rng0", "-device", "virtio-rng-pci,rng=rng0",
    ]
    return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=open(work / "qemu.log", "w"))


def wait_ssh(guest: "Guest", process, timeout: int) -> bool:
    started = time.time()
    while time.time() - started < timeout:
        if process.poll() is not None:
            return False
        if guest.run("true", timeout=30).returncode == 0:
            return True
        time.sleep(5)
    return False


def wait_session(guest: "Guest", timeout: int) -> str:
    started = time.time()
    while time.time() - started < timeout:
        probe = guest.run(f"id -u {USER} && pgrep -u {USER} -x gnome-shell >/dev/null && "
                          f"test -S /run/user/$(id -u {USER})/wayland-0 && "
                          f"loginctl show-user {USER} -p State --value", timeout=20)
        lines = probe.stdout.split()
        if probe.returncode == 0 and len(lines) >= 2 and lines[-1] == "active":
            return lines[0]
        time.sleep(5)
    return ""


def prepare_base(disk: Path, arch: str = "x86_64") -> None:
    """Make an installed base boot straight to a stable logged-in desktop."""
    key = disk.parent / "review-key"
    work = Path(tempfile.mkdtemp(prefix="prepare-", dir=disk.parent))
    try:
        for attempt in ("configure", "verify"):
            port = free_port()
            process = boot(disk, arch, work, port, memory="6144", cpus="4")
            guest = Guest(port, key)
            started = time.time()
            try:
                if not wait_ssh(guest, process, 1500):
                    raise RuntimeError(f"{attempt}: no SSH after {int(time.time() - started)} s")
                log(f"{attempt}: SSH after {int(time.time() - started)} s")
                if attempt == "configure":
                    guest.run("systemctl mask " + " ".join(REVIEW_MASKS), timeout=60, check=True)
                    guest.run("rpm-ostree kargs --append-if-missing=plymouth.enable=0 "
                              "--delete-if-present=rhgb --delete-if-present=quiet", timeout=600, check=True)
                    jobs = guest.run("systemctl list-jobs --no-legend | head -20", timeout=30).stdout
                    log("pending jobs at configure:\n" + jobs)
                else:
                    uid = wait_session(guest, 900)
                    if not uid:
                        jobs = guest.run("systemctl list-jobs --no-legend | head; loginctl list-sessions", timeout=30)
                        raise RuntimeError("verify: no logged-in desktop:\n" + jobs.stdout)
                    log(f"verify: desktop session after {int(time.time() - started)} s")
                    time.sleep(45)
                    again = wait_session(guest, 10)
                    if again != uid:
                        raise RuntimeError("verify: the desktop session did not stay logged in")
                    qmp = QMP(work / "qmp.sock")
                    qmp.command("screendump", filename=str(work / "desktop.ppm"))
                    width, height, rgb = read_ppm(work / "desktop.ppm")
                    write_png(disk.parent / f"base-{arch}-desktop.png", width, height, rgb)
                    log(f"verify: desktop stayed up; boot to desktop took {int(time.time() - started)} s")
                guest.run("systemctl poweroff", timeout=30)
                process.wait(timeout=300)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                shutil.copy(work / "serial.log", disk.parent / f"prepare-{attempt}-serial.log")
    finally:
        shutil.rmtree(work, ignore_errors=True)


# -- QMP and screenshots -----------------------------------------------------------

class QMP:
    def __init__(self, path: Path, timeout: float = 30):
        deadline = time.time() + timeout
        while True:
            try:
                self.sock = socket.socket(socket.AF_UNIX)
                self.sock.connect(str(path))
                break
            except OSError:
                if time.time() > deadline:
                    raise
                time.sleep(0.5)
        self.file = self.sock.makefile("rw")
        json.loads(self.file.readline())
        self.command("qmp_capabilities")

    def command(self, name: str, **arguments):
        self.file.write(json.dumps({"execute": name, "arguments": arguments}) + "\n")
        self.file.flush()
        while True:
            reply = json.loads(self.file.readline())
            if "return" in reply:
                return reply["return"]
            if "error" in reply:
                raise RuntimeError(f"QMP {name}: {reply['error']}")


def read_ppm(path: Path) -> tuple[int, int, bytes]:
    data = path.read_bytes()
    parts = data.split(maxsplit=4)
    if parts[0] != b"P6":
        raise ValueError("unexpected screendump format")
    width, height = int(parts[1]), int(parts[2])
    pixels = parts[4]
    return width, height, pixels[: width * height * 3]


def write_png(path: Path, width: int, height: int, rgb: bytes) -> None:
    stride = width * 3
    raw = b"".join(b"\0" + rgb[y * stride:(y + 1) * stride] for y in range(height))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def changed_fraction(a: bytes, b: bytes) -> float:
    if len(a) != len(b) or not a:
        return 1.0
    step = 3 * 7  # sample every 7th pixel
    total = changed = 0
    for i in range(0, len(a) - 2, step):
        total += 1
        if abs(a[i] - b[i]) + abs(a[i + 1] - b[i + 1]) + abs(a[i + 2] - b[i + 2]) > 24:
            changed += 1
    return changed / max(total, 1)


# -- A run ---------------------------------------------------------------------------

class Guest:
    def __init__(self, port: int, key: Path):
        self.port, self.key = port, key

    def ssh_args(self) -> list[str]:
        return ["-i", str(self.key), "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "IdentitiesOnly=yes",
                "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR"]

    def run(self, command: str, timeout: int = 120, check: bool = False) -> subprocess.CompletedProcess:
        try:
            result = subprocess.run(["ssh", "-p", str(self.port), *self.ssh_args(), "root@127.0.0.1", command],
                                    capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired as expired:
            # While the guest boots, user networking accepts the connection
            # before sshd answers; that is "not yet", not an error.
            result = subprocess.CompletedProcess(expired.cmd, 124, expired.stdout or "", "timed out")
            if isinstance(result.stdout, bytes):
                result.stdout = result.stdout.decode(errors="replace")
        if check and result.returncode != 0:
            raise RuntimeError(f"guest command failed ({result.returncode}): {command}\n{result.stderr[-800:]}")
        return result

    def copy(self, local: Path, remote: str, timeout: int = 600) -> None:
        subprocess.run(["scp", "-q", "-P", str(self.port), *self.ssh_args(), str(local), f"root@127.0.0.1:{remote}"],
                       check=True, timeout=timeout, stdin=subprocess.DEVNULL)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def session_env(uid: str) -> str:
    return (f"XDG_RUNTIME_DIR=/run/user/{uid} DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus "
            f"WAYLAND_DISPLAY=wayland-0")


def run_app(base: Path, key: Path, package: Path, app_id: str, fmt: str, out: Path, arch: str = "x86_64",
            boot_timeout: int = 420) -> dict:
    """Returns {"steps": [{step, ok, message}], "screenshot": path|None, "ok": bool}."""
    out.mkdir(parents=True, exist_ok=True)
    steps: list[dict] = []
    result = {"steps": steps, "screenshot": None, "ok": False, "arch": arch}

    def step(name: str, ok: bool, message: str, **extra) -> bool:
        steps.append({"step": name, "ok": ok, "message": message, **extra})
        log(f"{name}: {'ok' if ok else 'FAIL'} {message}")
        return ok

    if fmt != "flatpak":
        step("install", False, "The VM run covers Flatpak bundles tonight; Luma checks universal packages by hand.",
             internal=True)
        return result
    work = Path(tempfile.mkdtemp(prefix="run-", dir=out))
    overlay = work / "disk.qcow2"
    subprocess.run(["qemu-img", "create", "-q", "-f", "qcow2", "-b", str(base), "-F", "qcow2", str(overlay)],
                   check=True)
    port = free_port()
    process = boot(overlay, arch, work, port, memory="6144", cpus="4")
    guest = Guest(port, key)
    try:
        qmp = QMP(work / "qmp.sock")
        started = time.time()
        if not wait_ssh(guest, process, boot_timeout):
            step("boot", False, "The test VM didn't start in time.", internal=True)
            return result
        uid = wait_session(guest, max(60, boot_timeout - int(time.time() - started)))
        if not uid:
            step("boot", False, "The test VM's desktop didn't start.", internal=True)
            return result
        step("boot", True, f"Luma started ({int(time.time() - started)} s).")
        time.sleep(8)  # let the shell settle
        before = work / "before.ppm"
        qmp.command("screendump", filename=str(before))
        guest.copy(package, "/var/tmp/app.flatpak")
        install = guest.run("flatpak install --system --noninteractive -y /var/tmp/app.flatpak 2>&1", timeout=1200)
        (out / "install.log").write_text(install.stdout + install.stderr)
        if install.returncode != 0:
            step("install", False, "Your app didn't install. " + plain_install_error(install.stdout),
                 detail=install.stdout[-1500:])
            return result
        installed = guest.run(f"flatpak info --system {app_id}", timeout=30)
        step("install", installed.returncode == 0, "Installed." if installed.returncode == 0 else
             "Your app didn't install.")
        if installed.returncode != 0:
            return result
        launch = guest.run(f"runuser -u {USER} -- env {session_env(uid)} systemd-run --user --collect "
                           f"--unit=depot-review-app --property=TimeoutStopSec=10 flatpak run {app_id}", timeout=60)
        if launch.returncode != 0:
            step("launch", False, "Your app couldn't be started.", detail=launch.stderr[-800:])
            return result
        alive, fraction = False, 0.0
        shot = work / "after.ppm"
        launched = time.time()
        # A first start builds Flatpak's library cache for the runtime, which
        # on the VM's cold disk can take over a minute; allow three.
        for _ in range(36):
            time.sleep(5)
            state = guest.run(f"runuser -u {USER} -- env {session_env(uid)} systemctl --user is-active "
                              f"depot-review-app", timeout=20).stdout.strip()
            alive = state in ("active", "activating")
            qmp.command("screendump", filename=str(shot))
            fraction = changed_fraction(read_ppm(before)[2], read_ppm(shot)[2])
            if not alive or fraction > 0.02:
                break
        seconds = int(time.time() - launched)
        time.sleep(3)
        qmp.command("screendump", filename=str(shot))
        width, height, rgb = read_ppm(shot)
        png = out / f"launch-{arch}.png"
        write_png(png, width, height, rgb)
        result["screenshot"] = str(png)
        journal = guest.run(f"journalctl -q --no-pager -o cat _UID={uid} --since=-3min | tail -n 200", timeout=30)
        (out / "app-journal.log").write_text(journal.stdout)
        if not alive:
            step("launch", False, "Your app closed right after it started. Its log is attached for you.",
                 detail=journal.stdout[-1500:])
            return result
        if fraction <= 0.02:
            step("launch", False, "Your app started but no window appeared within three minutes.",
                 detail=journal.stdout[-1500:])
            return result
        step("launch", True, f"Your app opened a window ({seconds} s).")
        guest.run(f"runuser -u {USER} -- env {session_env(uid)} systemctl --user stop depot-review-app", timeout=30)
        guest.run(f"flatpak kill {app_id}", timeout=20)
        remove = guest.run(f"flatpak uninstall --system --noninteractive -y --delete-data {app_id} 2>&1", timeout=300)
        gone = guest.run(f"! flatpak info --system {app_id} >/dev/null 2>&1 && "
                         f"! test -e /var/lib/flatpak/exports/share/applications/{app_id}.desktop", timeout=30)
        ok = remove.returncode == 0 and gone.returncode == 0
        step("uninstall", ok, "Uninstalled cleanly." if ok else "Your app didn't uninstall cleanly.",
             detail=remove.stdout[-800:])
        result["ok"] = all(s["ok"] for s in steps)
        return result
    except (RuntimeError, OSError, subprocess.SubprocessError, ValueError) as error:
        step("vm", False, "The test VM had a problem; Luma will look at it.", internal=True, detail=str(error))
        return result
    finally:
        try:
            process.terminate()
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
        for name in ("serial.log", "qemu.log"):
            if (work / name).exists():
                shutil.copy(work / name, out / name)
        shutil.rmtree(work, ignore_errors=True)


def plain_install_error(text: str) -> str:
    lowered = text.lower()
    if "runtime" in lowered and ("not found" in lowered or "not installed" in lowered or "no remote" in lowered):
        return "Its runtime couldn't be found. Build it on a runtime Depot accepts."
    if "no space" in lowered:
        return "It's too big to install."
    return "Flatpak's message is attached."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build-base")
    b.add_argument("--iso", type=Path, required=True)
    b.add_argument("--out", type=Path, required=True)
    b.add_argument("--arch", default="x86_64")
    pb = sub.add_parser("prepare-base")
    pb.add_argument("--disk", type=Path, required=True)
    pb.add_argument("--arch", default="x86_64")
    r = sub.add_parser("run")
    r.add_argument("--base", type=Path, required=True)
    r.add_argument("--key", type=Path, required=True)
    r.add_argument("--package", type=Path, required=True)
    r.add_argument("--app-id", required=True)
    r.add_argument("--format", default="flatpak")
    r.add_argument("--arch", default="x86_64")
    r.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "prepare-base":
        prepare_base(args.disk, args.arch)
        return 0
    if args.command == "build-base":
        build_base(args.iso, args.out, args.arch)
        return 0
    result = run_app(args.base, args.key, args.package, args.app_id, args.format, args.out, args.arch)
    print(json.dumps(result, indent=1))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
