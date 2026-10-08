#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
#
# Release gate checks: the installed system presents itself as Luma, in words
# and in pictures (ADR-040). Run as root inside a disposable VM installed from
# the candidate image or from a nightly medium. Needs no display and no
# network. Prints one JSON line per check ({"check", "result":
# "pass"|"fail"|"skip", "detail"}) and exits 1 if any check failed.
#
#   luma-identity.sh
#
# Deliberate technical exceptions (ADR-040), never reported: ID_LIKE,
# PLATFORM_ID and LUMA_FEDORA_RELEASE in os-release; /etc/fedora-release,
# /etc/redhat-release and /etc/system-release-cpe; RPM %dist (fc44), package
# names and Fedora repository names; the kernel release string; the OSTree
# stateroot and remote names; the GNOME Online Accounts "Fedora" provider icon;
# the boot loader's \EFI\fedora directory (shim's signed path).
set -uo pipefail
failed=0
visible='fedora|silverblue'

emit() {
  python3 -c 'import json, sys; print(json.dumps({"check": sys.argv[1], "result": sys.argv[2], "detail": sys.argv[3][-600:]}))' "$1" "$2" "$3"
  [ "$2" = fail ] && failed=1
  return 0
}

# 1. os-release: the identity every presenter reads.
detail=$(python3 - <<'PY'
import re
fields = {}
for line in open("/usr/lib/os-release", encoding="utf-8"):
    if "=" in line:
        key, value = line.rstrip("\n").split("=", 1)
        fields[key] = value.strip('"')
problems = []
for key, want in (("NAME", "Luma"), ("ID", "luma"), ("ID_LIKE", "fedora"), ("LOGO", "luma-logo"),
                  ("DEFAULT_HOSTNAME", "luma")):
    if fields.get(key) != want:
        problems.append(f"{key}={fields.get(key)!r}, want {want!r}")
# The release's name (owner decision 2026-09-17): "Luma (Prairie, Beta 0,
# Nightly 20260916)", "Luma (Prairie, Beta 1)", "Luma (Version 1, Prairie)";
# PRETTY_NAME is "Luma (VERSION)" and never an unreleased "Luma 1.0".
pretty, version = fields.get("PRETTY_NAME", ""), fields.get("VERSION", "")
if not re.fullmatch(r"Luma \((\w+, Beta \d+(\.\d+)?(, Nightly \d{8})?|Version \d+, \w+(, Nightly \d{8})?)\)", pretty) \
        or pretty != f"Luma ({version})":
    problems.append(f"PRETTY_NAME={pretty!r} VERSION={version!r}")
if fields.get("VERSION_CODENAME", "").capitalize() not in pretty or not re.fullmatch(r"\d+", fields.get("VERSION_ID", "")):
    problems.append(f"VERSION_CODENAME={fields.get('VERSION_CODENAME')!r} VERSION_ID={fields.get('VERSION_ID')!r}")
if fields.get("LUMA_RELEASE_CHANNEL") == "nightly" and f"Nightly {fields.get('LUMA_NIGHTLY_DATE')}" not in pretty:
    problems.append(f"nightly named for {fields.get('LUMA_NIGHTLY_DATE')!r}: {pretty!r}")
exceptions = {"ID_LIKE", "PLATFORM_ID", "LUMA_FEDORA_RELEASE"}
for key, value in fields.items():
    if key not in exceptions and re.search(r"fedora|silverblue", value, re.I):
        problems.append(f"{key} names Fedora: {value}")
print("; ".join(problems))
PY
)
[ -z "$detail" ] && emit os-release-names-luma pass "$(grep -E '^(PRETTY_NAME|VERSION_ID|LOGO)=' /usr/lib/os-release | tr '\n' ' ')" ||
  emit os-release-names-luma fail "$detail"

# 2. What hostnamed tells Settings, hostnamectl and portals.
os=$(busctl get-property org.freedesktop.hostname1 /org/freedesktop/hostname1 org.freedesktop.hostname1 \
  OperatingSystemPrettyName 2>/dev/null | sed 's/^s //; s/"//g')
if [ -z "$os" ]; then
  emit hostnamed-names-luma skip "hostnamed did not answer"
elif echo "$os" | grep -qiE "$visible" || ! echo "$os" | grep -q '^Luma'; then
  emit hostnamed-names-luma fail "OperatingSystemPrettyName: $os"
else
  emit hostnamed-names-luma pass "$os"
fi

# 3. Boot menu entries.
titles=$(grep -h '^title' /boot/loader/entries/*.conf 2>/dev/null)
if [ -z "$titles" ]; then
  emit boot-entries-name-luma skip "no BLS entries under /boot/loader/entries"
elif echo "$titles" | grep -qiE "$visible"; then
  emit boot-entries-name-luma fail "$titles"
else
  emit boot-entries-name-luma pass "$(echo "$titles" | tr '\n' ';')"
fi

# 3b. The initramfs names the system while it boots ("Booting initrd of ...").
initrd=/usr/lib/modules/$(uname -r)/initramfs.img
if [ ! -r "$initrd" ] || ! command -v lsinitrd >/dev/null; then
  emit initramfs-names-luma skip "no readable $initrd or no lsinitrd"
else
  initrd_release=$(lsinitrd -f usr/lib/initrd-release "$initrd" 2>/dev/null | grep -E '^(NAME|PRETTY_NAME)=')
  if echo "$initrd_release" | grep -q '^NAME="\?Luma' && ! echo "$initrd_release" | grep -qiE "$visible"; then
    emit initramfs-names-luma pass "$(echo "$initrd_release" | tr '\n' ' ')"
  else
    emit initramfs-names-luma fail "initrd-release: $(echo "$initrd_release" | tr '\n' ' ')"
  fi
fi

# 3c. The firmware's boot menu: the entries that start this system's boot
# loader (\EFI\fedora\..., shim's signed path), which bootupd names from
# /etc/system-release at install. The firmware's own entries (the disk, network
# boot) are not the system's. Read from the EFI variables, not efibootmgr's
# changing output format.
detail=$(python3 - <<'PY'
import glob, os, re, struct
guid = "8be4df61-93ca-11d2-aa0d-00e098032b8c"
efivars = os.environ.get("LUMA_IDENTITY_EFIVARS", "/sys/firmware/efi/efivars")
if not os.path.isdir(efivars):
    print("skip\tnot booted with UEFI"); raise SystemExit
names, bad = [], []
for path in sorted(glob.glob(f"{efivars}/Boot[0-9A-F][0-9A-F][0-9A-F][0-9A-F]-{guid}")):
    try:
        data = open(path, "rb").read()[4:]  # EFI variable attributes
        _, list_len = struct.unpack("<IH", data[:6])  # EFI_LOAD_OPTION
        end = 6
        while end + 1 < len(data) and data[end:end + 2] != b"\0\0":
            end += 2
        label = data[6:end].decode("utf-16-le", "replace")
        raw = data[end + 2:end + 2 + list_len]
        device = raw.decode("utf-16-le", "ignore") + raw[1:].decode("utf-16-le", "ignore")
    except (OSError, struct.error):
        continue
    if "\\efi\\fedora\\" not in device.lower():
        continue
    entry = os.path.basename(path)[:8]
    names.append(f"{entry} {label}")
    if re.search(r"fedora|silverblue", label, re.I):
        bad.append(f"{entry} {label}")
if not names:
    print("skip\tno firmware entry for this system's boot loader")
elif bad:
    print("fail\t" + "; ".join(bad))
else:
    print("pass\t" + "; ".join(names))
PY
)
case "$detail" in
  pass$'\t'*|fail$'\t'*|skip$'\t'*) emit firmware-boot-entry-luma "${detail%%$'\t'*}" "${detail#*$'\t'}" ;;
  *) emit firmware-boot-entry-luma fail "could not read the firmware boot entries: $detail" ;;
esac

# 3d. The machine's name. A new install is called luma unless the install
# named it; the gate's own kickstarts name their VMs, which proves nothing
# about the default.
static=$(hostnamectl hostname --static 2>/dev/null || head -n 1 /etc/hostname 2>/dev/null)
current=${static:-$(hostname 2>/dev/null)}
case "$static" in
  luma-gate|luma-gate-*|luma-media-test)
    emit default-hostname-luma skip "named by the test kickstart: $static" ;;
  *)
    if echo "$current" | grep -qiE "$visible|^localhost"; then
      emit default-hostname-luma fail "hostname: ${current:-unset} (static: ${static:-unset})"
    elif [ "$current" = luma ]; then
      emit default-hostname-luma pass "luma"
    else
      emit default-hostname-luma pass "named at install: $current"
    fi ;;
esac

# 3e. A computer a person has logged in to has a name of its own ("Nick’s
# ThinkPad", nicks-thinkpad), not a default one: the install named it, or
# luma-device-name@.service did at the first login.
pretty=$(hostnamectl hostname --pretty 2>/dev/null)
logged_in=
while IFS=: read -r user _ uid _ _ _ shell; do
  [ "$uid" -ge 1000 ] 2>/dev/null && [ "$uid" -lt 60000 ] || continue
  case "$shell" in */nologin|*/false) continue ;; esac
  if [ -d "/run/user/$uid" ] || journalctl -q --no-pager -u "user@$uid.service" 2>/dev/null | grep -q .; then
    logged_in="$user"
    break
  fi
done < /etc/passwd
if [ -z "$logged_in" ]; then
  emit device-named-after-login skip "no person has logged in"
elif [ -z "$pretty" ] && echo "${static,,}" | grep -qxE 'fedora|fedora\.localdomain|localhost|localhost-live|localhost\.localdomain|luma|'; then
  emit device-named-after-login fail "$logged_in logged in but the machine is still called ${static:-unset} with no pretty name; $(journalctl -q --no-pager -t luma-device-name 2>/dev/null | tail -n 3 | tr '\n' ' ')"
else
  emit device-named-after-login pass "${pretty:-$static} (${static:-unset}) after $logged_in logged in"
fi

# 4. The text files older tools print.
release=$(cat /etc/system-release 2>/dev/null)
issue=$(cat /etc/issue 2>/dev/null)
if echo "$release" | grep -q '^Luma' && ! echo "$release $issue" | grep -qiE "$visible"; then
  emit release-files-name-luma pass "$release"
else
  emit release-files-name-luma fail "system-release: $release; issue: $issue"
fi

# 5. Logos: the names programs ask for resolve to Luma's files.
problems=""
for icon in scalable/apps/luma-logo.svg scalable/apps/luma-logo-text.svg scalable/apps/luma-logo-text-dark.svg \
            scalable/places/start-here.svg 48x48/places/start-here.png; do
  path=/usr/share/icons/hicolor/$icon
  owner=$(rpm -qf --qf '%{NAME}' "$path" 2>/dev/null)
  [ "$owner" = luma-logos ] || problems="$problems $icon (owner: ${owner:-missing})"
done
logo_pkg=$(rpm -q --whatprovides system-logos --qf '%{NAME} ' 2>/dev/null)
[ "$logo_pkg" = "luma-logos " ] || problems="$problems system-logos provided by: $logo_pkg"
httpd_logo_pkg=$(rpm -q --whatprovides 'system-logos(httpd-logo-ng)' --qf '%{NAME} ' 2>/dev/null)
case "$httpd_logo_pkg" in
  "luma-logos-httpd "|*"no package provides"*) ;;
  *) problems="$problems system-logos(httpd-logo-ng) provided by: $httpd_logo_pkg" ;;
esac
for name in fedora-logos fedora-logos-httpd generic-logos generic-logos-httpd; do
  rpm -q "$name" >/dev/null 2>&1 && problems="$problems $name is installed"
done
# Pictures, not empty directories: a removed package can leave an empty
# Fedora-named directory that nothing shows.
marks=$(find /usr/share/icons /usr/share/pixmaps /usr/share/plymouth /usr/share/gdm /usr/share/backgrounds \
  /usr/share/gnome-background-properties /usr/share/anaconda \( -type f -o -type l \) \
  \( -ipath '*fedora*' -o -ipath '*silverblue*' \) ! -name 'goa-account-fedora*' 2>/dev/null | head -20)
[ -z "$marks" ] || problems="$problems Fedora artwork: $(echo "$marks" | tr '\n' ' ')"
[ -z "$problems" ] && emit logos-are-luma pass "system-logos from luma-logos; no Fedora artwork" ||
  emit logos-are-luma fail "$problems"

# 6. The login screen's logo.
login_logo=$(runuser -u gdm -- gsettings get org.gnome.login-screen logo 2>/dev/null ||
  gsettings get org.gnome.login-screen logo 2>/dev/null)
if echo "$login_logo" | grep -q 'luma'; then
  emit login-logo-is-luma pass "$login_logo"
else
  emit login-logo-is-luma fail "org.gnome.login-screen logo: $login_logo"
fi

# 7. Plymouth.
theme=$(plymouth-set-default-theme 2>/dev/null)
# Comments in the theme's files are not shown; everything else is.
if [ -n "$theme" ] && ! echo "$theme" | grep -qiE "$visible" &&
   ! grep -rhviE '^[[:space:]]*(#|//)' "/usr/share/plymouth/themes/$theme" 2>/dev/null | grep -aqiE "$visible"; then
  emit boot-splash-is-luma pass "$theme"
else
  emit boot-splash-is-luma fail "theme: ${theme:-none}"
fi

# 8. Names people read in menus, extensions, first-login setup and Settings.
detail=$(python3 - <<'PY'
import configparser, glob, json, re
hits = []
pattern = re.compile(r"fedora|silverblue", re.I)
for path in glob.glob("/usr/share/applications/*.desktop"):
    parser = configparser.RawConfigParser(strict=False, interpolation=None)
    try:
        parser.read(path, encoding="utf-8")
        entry = parser["Desktop Entry"]
    except Exception:
        continue
    if entry.get("NoDisplay", "false") == "true" or entry.get("Hidden", "false") == "true":
        continue
    for key in ("Name", "GenericName", "Comment"):
        if pattern.search(entry.get(key, "")):
            hits.append(f"{path} {key}={entry.get(key)}")
for path in glob.glob("/usr/share/gnome-shell/extensions/*/metadata.json"):
    data = json.load(open(path, encoding="utf-8"))
    if pattern.search(data.get("name", "") + " " + data.get("description", "")):
        hits.append(f"{path}: {data.get('name')}")
for path in glob.glob("/usr/share/gnome-initial-setup/*") + glob.glob("/usr/share/gnome-background-properties/*"):
    try:
        if pattern.search(open(path, encoding="utf-8", errors="replace").read()):
            hits.append(path)
    except IsADirectoryError:
        pass
print("; ".join(hits))
PY
)
[ -z "$detail" ] && emit menus-name-no-fedora pass "desktop entries, extensions, initial setup, backgrounds" ||
  emit menus-name-no-fedora fail "$detail"

# 9. Settings › About has no GNOME donation row or GNOME rows.
gcc_bin=$(command -v gnome-control-center)
if [ -z "$gcc_bin" ]; then
  emit settings-about-is-luma skip "gnome-control-center is not installed"
elif grep -aqF 'donate.gnome.org' "$gcc_bin" || grep -aqF 'Support GNOME' "$gcc_bin" ||
     grep -aqF 'gnome_version_row' "$gcc_bin"; then
  emit settings-about-is-luma fail "gnome-control-center still carries the donation or GNOME version rows"
else
  emit settings-about-is-luma pass "$(rpm -q gnome-control-center)"
fi

exit "$failed"
