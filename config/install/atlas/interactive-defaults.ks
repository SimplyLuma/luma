# Luma installer defaults: Anaconda reads this file when no kickstart is given,
# which is how the Atlas web UI (and any interactive install) gets its payload.
# A kickstart on the boot line would make the install "automated", which the
# web UI refuses, so the payload lives here and every choice stays with the
# person at the installer.
#
# The file is the same on every medium. What differs (channel, carried
# payload, test payload) is in /usr/share/luma-installer-atlas/media.env,
# written into the installer runtime by
# scripts/install/atlas-iso/build-installer-iso.sh, next to the Luma OS
# Release public key. The installer runtime trusts only that key for the OSTree
# pull, so GPG verification cannot be switched off from here (ADR-030 §9).

# Choose where the payload comes from: the OSTree repository on the install
# medium when it carries the channel's ref (fast, offline), otherwise Luma's
# HTTPS repository. Anaconda runs %pre before it reads the rest of this file,
# so the %include below sees the choice.
%pre --interpreter=/usr/bin/bash --erroronfail
set -euo pipefail
media=/usr/share/luma-installer-atlas/media.env
out=/run/luma-atlas
mkdir -p "$out"
# media.env is key=value lines written at build time; read it without
# evaluating it.
declare -A m=()
while IFS='=' read -r key value; do
    case "$key" in ''|'#'*) continue ;; esac
    m[$key]=$value
done < "$media"
for key in channel remote https_url mirrorlist ref stateroot medium_repo volid; do
    [ -n "${m[$key]:-}" ] || { echo "atlas: media.env has no $key" >&2; exit 1; }
done

source=online
url=${m[https_url]}
pull_ref=${m[ref]}
if [ -n "${m[test_payload_url]:-}" ]; then
    source=test
    url=${m[test_payload_url]}
    pull_ref=${m[test_payload_ref]}
else
    medium_root=
    for candidate in /run/install/repo /run/install/source /run/initramfs/live; do
        if [ -d "$candidate/${m[medium_repo]}/objects" ]; then
            medium_root=$candidate
            break
        fi
    done
    # With rd.live.ram=1 dracut can leave the medium unmounted once stage 2
    # is in memory; mount it again read-only by its label.
    if [ -z "$medium_root" ] && [ -e "/dev/disk/by-label/${m[volid]}" ]; then
        mkdir -p /run/install/repo
        if mount -o ro "/dev/disk/by-label/${m[volid]}" /run/install/repo 2>/dev/null &&
           [ -d "/run/install/repo/${m[medium_repo]}/objects" ]; then
            medium_root=/run/install/repo
        fi
    fi
    if [ -n "$medium_root" ] &&
       ostree --repo="$medium_root/${m[medium_repo]}" rev-parse "${m[ref]}" >/dev/null 2>&1; then
        source=medium
        url="file://$medium_root/${m[medium_repo]}"
    fi
fi

case "$url" in
    https://*|file:///*) ;;
    *) echo "atlas: refusing payload URL $url (HTTPS or the medium only)" >&2; exit 1 ;;
esac

# No --nogpg: the pull is verified against the key in
# /usr/share/ostree/trusted.gpg.d, the only key the runtime holds.
printf 'ostreesetup --osname=%s --remote=%s --url=%s --ref=%s\n' \
    "${m[stateroot]}" "${m[remote]}" "$url" "$pull_ref" > "$out/payload.ks"
# Whether the medium carries a staff preview credential; never its value.
enrolled=false
[ -s /usr/share/luma-installer-atlas/preview-credential ] && enrolled=true
python3 -c 'import json, sys; d = dict(zip(["channel", "source", "url", "ref"], sys.argv[1:5])); d["preview_enrolled"] = sys.argv[5] == "true"; print(json.dumps(d))' \
    "${m[channel]}" "$source" "$url" "$pull_ref" "$enrolled" > "$out/payload.json"
echo "atlas: installing ${m[ref]} from $source ($url)"
%end

%include /run/luma-atlas/payload.ks
firewall --use-system-defaults

%post --erroronfail
cp /etc/skel/.bash* /root
# Luma always starts at its login screen, however the install was driven.
systemctl set-default graphical.target
%end

# Atlas's "Sign me in automatically". Anaconda's Users module has no property
# for it, so Atlas records the choice in the installer's memory and this
# script applies it to the installed system's display manager. The username is
# validated again here; nothing else is read from the file.
%post --nochroot --erroronfail
intent=/run/luma-atlas/autologin
conf="$ANA_INSTALL_PATH/etc/gdm/custom.conf"
if [ -s "$intent" ]; then
    user=$(head -n 1 "$intent")
    case "$user" in
        ''|*[!a-z0-9_-]*|[!a-z_]*) echo "atlas: ignoring invalid autologin user" >&2; exit 0 ;;
    esac
    if ! grep -q "^${user}:" "$ANA_INSTALL_PATH/etc/passwd" 2>/dev/null; then
        echo "atlas: autologin user $user does not exist in the target" >&2
        exit 0
    fi
    mkdir -p "$(dirname "$conf")"
    [ -f "$conf" ] || printf '[daemon]\n' > "$conf"
    grep -q '^\[daemon\]' "$conf" || printf '[daemon]\n' >> "$conf"
    sed -i -e '/^AutomaticLoginEnable=/d' -e '/^AutomaticLogin=/d' "$conf"
    sed -i "/^\[daemon\]/a AutomaticLoginEnable=True\nAutomaticLogin=${user}" "$conf"
    chroot "$ANA_INSTALL_PATH" /usr/sbin/restorecon -F /etc/gdm/custom.conf 2>/dev/null || :
    echo "atlas: automatic login enabled for $user"
fi
%end

# The installed system's name (ADR-040, device names): "Nick’s ThinkPad"
# (nicks-thinkpad), from the account created here and the computer's model,
# by the installed system's own luma-device-name helper. A name the kickstart
# set is never replaced. With no account yet (first-login setup), or a system
# that predates the helper, the name is left unset (placeholders removed) and
# the system names itself at the first login. Existing machines are never
# renamed: this runs only on install.
%post --nochroot --erroronfail --interpreter=/usr/bin/bash
set -uo pipefail
root=${ANA_INSTALL_PATH:-/mnt/sysroot}
helper="$root/usr/libexec/luma-os/luma-device-name"
if [ -f "$helper" ]; then
    python3 "$helper" install --root "$root" 2>&1 | sed 's/^luma-device-name: /atlas: /' || :
    exit 0
fi
file="$root/etc/hostname"
current=$(head -n 1 "$file" 2>/dev/null | tr -d '[:space:]') || current=
case "$current" in
    '')
        echo "atlas: the new system is named at the first login" ;;
    localhost|localhost.localdomain|localhost-live|fedora|fedora.localdomain|luma)
        rm -f "$file"
        echo "atlas: removed the placeholder name $current; the new system is named at the first login" ;;
    *)
        echo "atlas: the new system keeps the name it was given: $current" ;;
esac
exit 0
%end

# The explicit city choice is read back from Anaconda before Atlas continues.
# Apply it after Anaconda's tasks to the actual OSTree deployment /etc, and
# retain the representative city's offline tzdata coordinates for Clock/Weather.
%post --nochroot --erroronfail
/usr/libexec/luma-installer-atlas/atlas-media apply-location "$ANA_INSTALL_PATH"
%end

# Atlas's "Get more apps" (ADR-028 Depot, section 14). Only collection ids the
# Atlas catalog knows are written; Depot's first-boot provisioning reads the
# file and installs from the Luma remote. The installer downloads nothing.
%post --nochroot --erroronfail
python3 - <<'PY'
import json
import os
import subprocess

intent = "/run/luma-atlas/first-boot-apps.json"
catalog = "/usr/share/luma-installer-atlas/app-collections.json"
root = os.environ.get("ANA_INSTALL_PATH", "/mnt/sysroot")
target = os.path.join(root, "etc/luma/first-boot-apps.json")

try:
    with open(intent, encoding="utf-8") as handle:
        choice = json.load(handle)
except (OSError, ValueError):
    print("atlas: no first-boot app choice recorded")
    raise SystemExit(0)

with open(catalog, encoding="utf-8") as handle:
    known = [collection["id"] for collection in json.load(handle)["collections"]]
wanted = choice.get("collections", []) if isinstance(choice, dict) else []
collections = [identifier for identifier in known if identifier in wanted]

os.makedirs(os.path.dirname(target), mode=0o755, exist_ok=True)
temporary = target + ".atlas"
with open(temporary, "w", encoding="utf-8") as handle:
    json.dump({"collections": collections, "applications": []}, handle)
    handle.write("\n")
os.chmod(temporary, 0o644)
os.replace(temporary, target)
subprocess.run(["chroot", root, "/usr/sbin/restorecon", "-F", "/etc/luma", "/etc/luma/first-boot-apps.json"], check=False)
print("atlas: first-boot apps recorded:", collections)
PY
%end

# ADR-030 §9: whatever the install came from, the installed system updates
# online from its first boot. Its origin is luma:luma/1/x86_64/<channel> on
# the luma remote, verified with the Luma OS Release key, and the channel is
# recorded for luma-update.
#
# The remote uses the image's mirror-list layout (luma-update 1.0.0-1.luma.3):
# /etc/ostree/remotes.d/luma.conf always says
# url=mirrorlist=file:///etc/luma/update-mirrorlist, a world-readable file
# with no secret in it, and /etc/luma/update-mirrorlist (root, 0600) holds the
# repository URL. The installer writes the medium's HTTPS URL there and never
# puts a URL in the remote file. Preview channels (beta, nightly) are served
# only with a credential. Until Hub enrolls devices one by one, staff media for
# a preview channel may carry one per-batch credential
# (build-installer-iso.sh --preview-credential-file), kept root-only in the
# installer at /usr/share/luma-installer-atlas/preview-credential. When it is
# there, the mirror list gets the preview URL with the credential in it, and
# /etc/luma/update-preview-credential (root, 0600) gets luma-update's
# enrollment record, as EnrollPreview writes it. Leaving the channel through
# luma-update resets the mirror list to the public URL and removes the record.
# The credential is never printed: logs say only "preview credential
# installed". Media without it leave enrollment to luma-update after first
# boot.
%post --nochroot --erroronfail --interpreter=/usr/bin/python3
import glob
import os
import re
import json
import shutil
import subprocess
import sys
import time

MEDIA = "/usr/share/luma-installer-atlas/media.env"
KEY = "/usr/share/luma-installer-atlas/luma-release.gpg"
CREDENTIAL = "/usr/share/luma-installer-atlas/preview-credential"
PHYSICAL_ROOT = "/mnt/sysimage"


def fail(message):
    print("atlas: " + message, file=sys.stderr)
    raise SystemExit(1)


def write(path, text, mode=0o644):
    os.makedirs(os.path.dirname(path), mode=0o755, exist_ok=True)
    temporary = path + ".atlas"
    with open(temporary, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def write_private(path, text):
    """root:root 0600 from the first byte: a private temporary file in the same
    directory, flushed, then renamed over the target."""
    os.makedirs(os.path.dirname(path), mode=0o755, exist_ok=True)
    temporary = path + ".atlas"
    try:
        os.unlink(temporary)
    except FileNotFoundError:
        pass
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        os.fchown(descriptor, 0, 0)
        os.write(descriptor, text.encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)


media = {}
with open(MEDIA, encoding="utf-8") as handle:
    for line in handle:
        line = line.rstrip("\n")
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            media[key] = value

root = os.environ.get("ANA_INSTALL_PATH", "/mnt/sysroot")
channel = media["channel"]
remote = media["remote"]
ref = media["ref"]
if channel not in media["channels"].split():
    fail("unknown channel " + channel)
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", remote):
    fail("invalid remote name " + remote)
if not media["https_url"].startswith("https://"):
    fail("the update remote must use HTTPS")

# The release key, where the image also ships it.
target_key = os.path.join(root, media["target_key"].lstrip("/"))
os.makedirs(os.path.dirname(target_key), mode=0o755, exist_ok=True)
shutil.copyfile(KEY, target_key)
os.chmod(target_key, 0o644)

# The mirror list: the medium's repository URL, root-only, written atomically
# (a private temporary file in the same directory, flushed, then renamed).
mirrorlist = media["mirrorlist"]
if not re.fullmatch(r"/etc/luma/[A-Za-z0-9._-]+", mirrorlist):
    fail("invalid mirror list path " + mirrorlist)
url = media["https_url"]
shown_url = url
credential = None
if os.path.exists(CREDENTIAL):
    # A staff medium's per-batch preview credential. Messages never include it.
    if channel not in media["preview_channels"].split():
        fail(f"this medium carries a preview credential but installs {channel}, not a preview channel")
    with open(CREDENTIAL, encoding="ascii", errors="replace") as handle:
        credential = handle.read().strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,256}", credential):
        fail("the preview credential on this medium is unusable")
    prefix = media["preview_url_prefix"]
    if not prefix.startswith("https://") or prefix.endswith("/"):
        fail("invalid preview repository prefix")
    url = f"{prefix}/{credential}/repo"
    shown_url = f"{prefix}/<preview credential>/repo"
if any(ord(character) < 33 or ord(character) == 127 for character in url):
    fail("invalid repository URL")
write_private(os.path.join(root, mirrorlist.lstrip("/")), url + "\n")

if credential is not None:
    record = media["preview_record"]
    if not re.fullmatch(r"/etc/luma/[A-Za-z0-9._-]+", record):
        fail("invalid preview enrollment record path")
    # luma-update's enrollment record (docs/os/luma-update.md, EnrollPreview).
    # "source" tells it this credential came with the staff medium and is shared
    # by the batch.
    write_private(os.path.join(root, record.lstrip("/")), json.dumps({
        "credential": credential,
        "channel": channel,
        "channels": [channel],
        "issued_at": int(time.time()),
        "source": "staff-media",
    }) + "\n")
    print("atlas: preview credential installed")

# The remote. The image defines it with the mirror-list url; that definition
# is left exactly as it is. Otherwise (an older image with the URL in the
# remote, or no definition, when Anaconda re-adds the kickstart remote with
# the URL it pulled from, possibly the medium) the definition is written with
# the mirror-list url, in whichever file already defines it, so the remote is
# never defined twice.
mirrorlist_url = "mirrorlist=file://" + mirrorlist
definition = (
    f'[remote "{remote}"]\n'
    f"url={mirrorlist_url}\n"
    "gpg-verify=true\n"
    "gpg-verify-summary=true\n"
    f'gpgkeypath={media["target_key"]}\n'
    f'collection-id={media["collection_id"]}\n'
)
remotes_dir = os.path.join(root, "etc/ostree/remotes.d")
header = re.compile(r'^[ \t]*\[[ \t]*remote[ \t]+"' + re.escape(remote) + r'"[ \t]*\][ \t]*$', re.M)
existing = [path for path in sorted(glob.glob(os.path.join(remotes_dir, "*.conf")))
            if header.search(open(path, encoding="utf-8").read())]
remote_file = existing[0] if existing else os.path.join(remotes_dir, remote + ".conf")
for extra in existing[1:]:
    os.unlink(extra)


def luma_section(text):
    """The lines of the remote's group, without its header."""
    lines, inside = [], False
    for line in text.splitlines():
        if line.strip().startswith("["):
            inside = bool(header.match(line))
            continue
        if inside:
            lines.append(line.strip())
    return lines


if existing:
    text = open(remote_file, encoding="utf-8").read()
    keys = {}
    for line in luma_section(text):
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            keys.setdefault(key.strip(), value.strip())
    if keys.get("url") == mirrorlist_url:
        for key in ("gpg-verify", "gpg-verify-summary"):
            if keys.get(key, "true").lower() not in ("true", "1", "yes"):
                fail(f"the image's {os.path.basename(remote_file)} turns {key} off")
        print(f"atlas: the image's {os.path.basename(remote_file)} already reads {mirrorlist}; left as it is")
    else:
        sections = re.split(r'(?m)^(?=[ \t]*\[)', text)
        kept = [section for section in sections if section.strip() and not header.match(section.splitlines()[0])]
        write(remote_file, "".join(kept + [definition]))
        print(f"atlas: {os.path.basename(remote_file)} now reads {mirrorlist}")
else:
    write(remote_file, definition)
    print(f"atlas: wrote {os.path.basename(remote_file)} reading {mirrorlist}")

# The physical repository must not define it as well.
repo_config = os.path.join(PHYSICAL_ROOT, "ostree/repo/config")
if os.path.exists(repo_config) and header.search(open(repo_config, encoding="utf-8").read()):
    subprocess.run(["ostree", "remote", "delete", "--repo=" + os.path.dirname(repo_config), remote], check=True)

# The deployment's origin.
origins = glob.glob(os.path.join(PHYSICAL_ROOT, "ostree/deploy", media["stateroot"], "deploy", "*.origin"))
if len(origins) != 1:
    fail(f"expected one deployment, found {len(origins)}")
wanted = f"{remote}:{ref}"
text = open(origins[0], encoding="utf-8").read()
current = re.search(r"(?m)^refspec=(.*)$", text)
if not current or current.group(1) != wanted:
    if not media.get("test_payload_url"):
        fail(f"the deployment's origin is {current.group(1) if current else 'missing'}, not {wanted}")
    # Test media install a stand-in payload; the origin still names the
    # channel so the update path is what a Luma install gets.
    text = re.sub(r"(?m)^refspec=.*$", "refspec=" + wanted, text) if current else text + f"\n[origin]\nrefspec={wanted}\n"
    write(origins[0], text)
    print(f"atlas: test payload {current.group(1) if current else ''} recorded as {wanted}")

# The channel, for luma-update.
write(os.path.join(root, "etc/luma/update-channel.conf"), f"channel={channel}\n")

subprocess.run(["chroot", root, "/usr/sbin/restorecon", "-F", "-R",
                "/etc/ostree/remotes.d", media["target_key"], "/etc/luma"], check=False)
print(f"atlas: updates follow {wanted} from {shown_url} (through {mirrorlist})")
%end

# Anaconda writes an /etc/fstab entry for / (subvol=root,compress=zstd:1,ro).
# On an OSTree deployment / is composefs, an overlay that cannot be
# reconfigured, so systemd-remount-fs.service would fail on every boot
# ("overlay: No changes allowed in reconfigure", Fedora bug 2348934). The
# kernel command line already mounts / (root= and rootflags=subvol=...), so
# the entry is commented out, not deleted, the same way the OS pipeline's
# migrate-to-channel.sh retires it on migrated machines. /boot, /boot/efi,
# /home, /var and every other entry stay as Anaconda wrote them. If the boot
# entries do not carry the mount, the line is left alone and the reason logged.
%post --nochroot --erroronfail --interpreter=/usr/bin/python3
import glob
import os
import re
import subprocess

PHYSICAL_ROOT = "/mnt/sysimage"
MARK = "# Retired by the Luma installer: / is composefs and is mounted from the kernel command line.\n"

root = os.environ.get("ANA_INSTALL_PATH", "/mnt/sysroot")
fstab = os.path.join(root, "etc/fstab")
lines = open(fstab, encoding="utf-8").read().splitlines(keepends=True)

index = None
for number, line in enumerate(lines):
    fields = line.split()
    if len(fields) >= 2 and not fields[0].startswith("#") and fields[1] == "/":
        index = number
        break

if index is None:
    print("atlas: /etc/fstab has no entry for /")
    raise SystemExit(0)

options = lines[index].split()[3] if len(lines[index].split()) >= 4 else ""
subvol = next((option[len("subvol="):] for option in options.split(",") if option.startswith("subvol=")), None)

entries = sorted(set(glob.glob(os.path.join(PHYSICAL_ROOT, "boot/loader/entries/*.conf")) +
                     glob.glob(os.path.join(root, "boot/loader/entries/*.conf"))))
problems = []
if not entries:
    problems.append("no boot loader entries were found")
for entry in entries:
    kargs = " ".join(line[len("options"):].strip() for line in open(entry, encoding="utf-8")
                     if line.startswith("options"))
    if not re.search(r"(^| )root=\S", kargs):
        problems.append(f"{os.path.basename(entry)} has no root=")
    if subvol and not re.search(r"(^| )rootflags=(\S*,)?subvol=/?" + re.escape(subvol.lstrip("/")) + r"(,| |$)", kargs):
        problems.append(f"{os.path.basename(entry)} does not mount subvolume {subvol}")

if problems:
    print("atlas: keeping the / entry in /etc/fstab: " + "; ".join(problems))
    raise SystemExit(0)

lines[index:index + 1] = [MARK, "# " + lines[index]]
temporary = fstab + ".atlas"
with open(temporary, "w", encoding="utf-8") as handle:
    handle.write("".join(lines))
os.chmod(temporary, os.stat(fstab).st_mode & 0o7777)
os.replace(temporary, fstab)
subprocess.run(["chroot", root, "/usr/sbin/restorecon", "-F", "/etc/fstab"], check=False)
print("atlas: commented out the / entry in /etc/fstab (composefs root)")
%end

# Kernel arguments the release declares in /usr/lib/bootc/kargs.d (for
# example efi_pstore.pstore_disable=0 from luma-vitals-crash-evidence).
# bootc reads that directory when it installs; Anaconda's ostreesetup does
# not, so without this a fresh install boots without them. Each *.toml is
# read with tomllib as bootc does: `kargs` is a list of arguments, and
# `match-architectures`, when present, limits the file to those
# architectures. Arguments already on the new deployment's command line
# (Anaconda's root=, rootflags=, rhgb quiet and the rest) are not added
# again; the others are appended with `ostree admin instutil set-kargs
# --merge`, which keeps everything already there. The same approach as the
# OS pipeline's gate kickstart.
%post --nochroot --erroronfail --interpreter=/usr/bin/python3
import glob
import os
import platform
import subprocess
import tomllib

PHYSICAL_ROOT = "/mnt/sysimage"
root = os.environ.get("ANA_INSTALL_PATH", "/mnt/sysroot")
kargs_dir = os.path.join(root, "usr/lib/bootc/kargs.d")
arch = platform.machine()


def fail(message):
    print("atlas: " + message, flush=True)
    raise SystemExit(1)


def declared_kargs(directory, arch):
    """The arguments every matching kargs.d file declares, in file order."""
    wanted = []
    for path in sorted(glob.glob(os.path.join(directory, "*.toml"))):
        name = os.path.basename(path)
        try:
            with open(path, "rb") as handle:
                data = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError) as error:
            fail(f"kargs.d/{name} cannot be read: {error}")
        arches = data.get("match-architectures")
        if arches is not None:
            if not isinstance(arches, list) or not all(isinstance(item, str) for item in arches):
                fail(f"kargs.d/{name}: match-architectures must be a list of strings")
            if arch not in arches:
                print(f"atlas: kargs.d/{name} is for {', '.join(arches)}, not {arch}; skipped")
                continue
        kargs = data.get("kargs", [])
        if not isinstance(kargs, list) or not all(isinstance(item, str) for item in kargs):
            fail(f"kargs.d/{name}: kargs must be a list of strings")
        for karg in kargs:
            if not karg or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in karg):
                fail(f"kargs.d/{name}: unusable kernel argument {karg!r}")
            wanted.append(karg)
    return wanted


def current_kargs():
    entries = sorted(glob.glob(os.path.join(PHYSICAL_ROOT, "boot/loader/entries/*.conf")))
    if len(entries) != 1:
        fail(f"expected one boot loader entry, found {len(entries)}")
    with open(entries[0], encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("options"):
                return line[len("options"):].split()
    return []


if not os.path.isdir(kargs_dir):
    print("atlas: the release declares no kernel arguments (no /usr/lib/bootc/kargs.d)")
    raise SystemExit(0)

wanted = declared_kargs(kargs_dir, arch)
if not wanted:
    print("atlas: /usr/lib/bootc/kargs.d declares no kernel arguments for " + arch)
    raise SystemExit(0)

present = current_kargs()
added = []
for karg in wanted:
    if karg in present or karg in added:
        continue
    subprocess.run(["ostree", "admin", "instutil", "set-kargs", "--sysroot=" + PHYSICAL_ROOT,
                    "--merge", "--append=" + karg], check=True)
    added.append(karg)

after = current_kargs()
missing = [karg for karg in wanted if karg not in after]
if missing:
    fail("kernel arguments still missing after set-kargs: " + " ".join(missing))
duplicates = sorted({karg for karg in after if after.count(karg) > 1 and karg in wanted + ["rhgb", "quiet"]})
if duplicates:
    fail("kernel arguments now appear twice: " + " ".join(duplicates))
print("atlas: kernel arguments from kargs.d: " + " ".join(wanted))
print("atlas: appended " + (" ".join(added) if added else "nothing (all were already present)"))
print("atlas: boot entry options: " + " ".join(after))
%end

# A staff medium's preview credential must not stay in the installation logs
# Anaconda copies to the installed system (/var/log/anaconda, including its
# journal dump, and the kickstarts in /root). None of Atlas's steps prints it,
# but Anaconda copies those logs only after every %post has run, so this
# cannot check them now. It installs a one-shot unit instead: on the first
# boot, before anyone can sign in, it replaces any occurrence of the
# credential in those files, confirms none is left, and removes itself.
%post --nochroot --erroronfail --interpreter=/usr/bin/python3
import os

CREDENTIAL = "/usr/share/luma-installer-atlas/preview-credential"
root = os.environ.get("ANA_INSTALL_PATH", "/mnt/sysroot")

if not os.path.exists(CREDENTIAL):
    raise SystemExit(0)

SCRIPT = r'''#!/usr/bin/python3
# Written by the Luma installer. Removes a staff medium's preview credential
# from the installation logs on the first boot, then removes itself.
import glob, json, os, sys
root = os.environ.get("LUMA_ATLAS_SCRUB_ROOT", "/")
def at(path):
    return os.path.join(root, path.lstrip("/"))
try:
    with open(at("/etc/luma/update-preview-credential"), encoding="utf-8") as handle:
        credential = json.load(handle)["credential"].encode("ascii")
except (OSError, ValueError, KeyError, AttributeError, UnicodeEncodeError):
    credential = None
paths = [path for path in glob.glob(at("/var/log/anaconda/**"), recursive=True)
         + glob.glob(at("/root/*.cfg")) + glob.glob(at("/var/roothome/*.cfg"))
         if os.path.isfile(path) and not os.path.islink(path)]
left = []
for path in paths if credential else []:
    with open(path, "rb") as handle:
        data = handle.read()
    if credential not in data:
        continue
    stat = os.stat(path)
    temporary = path + ".luma-scrub"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data.replace(credential, b"<preview credential removed>"))
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, stat.st_mode & 0o7777)
    os.chown(temporary, stat.st_uid, stat.st_gid)
    os.replace(temporary, path)
    with open(path, "rb") as handle:
        if credential in handle.read():
            left.append(path)
    print("removed the preview credential from " + path[len(root.rstrip("/")):])
if left:
    sys.exit("the preview credential is still in " + ", ".join(left))
for path in ("/etc/systemd/system/multi-user.target.wants/luma-atlas-scrub-install-logs.service",
             "/etc/systemd/system/luma-atlas-scrub-install-logs.service",
             "/etc/luma/atlas-scrub-install-logs"):
    try:
        os.unlink(at(path))
    except FileNotFoundError:
        pass
'''

UNIT = """[Unit]
Description=Remove the staff media preview credential from the installation logs
After=local-fs.target
Before=systemd-user-sessions.service display-manager.service
ConditionPathExists=/etc/luma/atlas-scrub-install-logs

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /etc/luma/atlas-scrub-install-logs

[Install]
WantedBy=multi-user.target
"""


def write(path, text, mode):
    os.makedirs(os.path.dirname(path), mode=0o755, exist_ok=True)
    temporary = path + ".atlas"
    with open(temporary, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(temporary, mode)
    os.replace(temporary, path)


write(os.path.join(root, "etc/luma/atlas-scrub-install-logs"), SCRIPT, 0o700)
unit = os.path.join(root, "etc/systemd/system/luma-atlas-scrub-install-logs.service")
write(unit, UNIT, 0o644)
wants = os.path.join(root, "etc/systemd/system/multi-user.target.wants")
os.makedirs(wants, mode=0o755, exist_ok=True)
link = os.path.join(wants, "luma-atlas-scrub-install-logs.service")
if not os.path.islink(link):
    os.symlink("/etc/systemd/system/luma-atlas-scrub-install-logs.service", link)
import subprocess
subprocess.run(["chroot", root, "/usr/sbin/restorecon", "-F",
                "/etc/luma/atlas-scrub-install-logs", "/etc/systemd/system/luma-atlas-scrub-install-logs.service",
                "/etc/systemd/system/multi-user.target.wants/luma-atlas-scrub-install-logs.service"], check=False)
print("atlas: first-boot log check for the preview credential installed")
%end
