#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Gate for the Luma ARM disk image (scripts/mobile/build-phone-userdata.sh)
# before it is published beside the nightly ISO. Checks, read-only:
#   layout       GPT with 4096-byte sectors: p1 LumaBoot (EFI type, 1 MiB,
#                480 MiB), p2 LumaRoot (Linux filesystem)
#   boot         p1 FAT, volume ID 032D-57AD, and empty: no device content
#   root         p2 ext4, UUID 03797c05-..., label LumaRoot, clean
#   commit       one deployment of COMMIT with origin luma:REF, no hardware
#                target, /ostree/root.a and root.b on it, device-kit.conf
#                naming LumaBoot
#   generic      no device packages in the deployment
#   labels       physical root, /var/home and /var/lib/systemd labeled
#                (scripts/mobile/common/label-ostree-physical-root.py --check)
#   clock        /var/lib/systemd/timesync/clock no older than the commit
#
# usage: check-arm-disk-image.sh IMAGE COMMIT [--record FILE]
# Prints RESULT <check> pass|fail lines; --record writes the gate JSON that
# scripts/os/lib/arm_disk_image.py entry reads. Root on Linux; work in TMPDIR.
set -euo pipefail
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
if [ -z "${LUMA_DISK_CHECK_NS:-}" ]; then
  LUMA_DISK_CHECK_NS=1 exec unshare --mount --propagation private bash "$0" "$@"
fi
image=${1:?usage: check-arm-disk-image.sh IMAGE COMMIT [--record FILE]}; commit=${2:?commit}; record=""
[ "${3:-}" = --record ] && record=${4:?record file}
root_uuid=03797c05-cf87-4eda-adc4-6ce111baf2d3 boot_id=032D-57AD
work=$(mktemp -d "${TMPDIR:-/var/tmp}/arm-disk-check.XXXXXX") loop=""
cleanup() { umount -R "$work/root" "$work/boot" 2>/dev/null || true; [ -z "$loop" ] || losetup -d "$loop"; rm -rf "$work"; }
trap cleanup EXIT INT TERM
declare -A result detail
check() { # name ok-command... ; detail in $why
  local name=$1; shift; why=""
  if "$@"; then result[$name]=pass; else result[$name]=fail; fi
  detail[$name]=$why; echo "RESULT $name ${result[$name]} $why"
}

python3 "$repo_root/scripts/os/lib/arm_disk_image.py" unsparse "$image" "$work/disk.raw"
loop=$(losetup -b 4096 -r -fP --show "$work/disk.raw")
for _ in 1 2 3 4 5; do [ -b "${loop}p2" ] && break; partx -u "$loop" 2>/dev/null || true; sleep 1; done

layout() {
  cat >"$work/layout.py" <<'EOF'
import json, sys
t = json.load(sys.stdin)["partitiontable"]; p = t["partitions"]
ok = (t["label"] == "gpt" and t.get("sectorsize") == 4096 and len(p) == 2
      and p[0].get("name") == "LumaBoot" and p[0]["type"] == "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"
      and p[0]["start"] == 256 and p[0]["size"] == 480 * 256
      and p[1].get("name") == "LumaRoot" and p[1]["type"] == "0FC63DAF-8483-4772-8E79-3D69D8477DE4")
print(" ".join("%s:%d+%d" % (x.get("name"), x["start"], x["size"]) for x in p), "sector", t.get("sectorsize"))
sys.exit(0 if ok else 1)
EOF
  why=$(sfdisk --json "$loop" | python3 "$work/layout.py")
}
boot() {
  [ "$(blkid -o value -s TYPE "${loop}p1")" = vfat ] && [ "$(blkid -o value -s UUID "${loop}p1")" = "$boot_id" ] || { why="not FAT $boot_id"; return 1; }
  mkdir -p "$work/boot"; mount -o ro "${loop}p1" "$work/boot"
  why="$(find "$work/boot" -mindepth 1 | wc -l) entries"; [ -z "$(find "$work/boot" -mindepth 1 -print -quit)" ]
}
root() {
  [ "$(blkid -o value -s TYPE "${loop}p2")" = ext4 ] && [ "$(blkid -o value -s UUID "${loop}p2")" = "$root_uuid" ] &&
    [ "$(blkid -o value -s LABEL "${loop}p2")" = LumaRoot ] || { why="not ext4 $root_uuid LumaRoot"; return 1; }
  e2fsck -fn "${loop}p2" >"$work/fsck.log" 2>&1 || { why="e2fsck: $(tail -1 "$work/fsck.log")"; return 1; }
  mkdir -p "$work/root"; mount -o ro,noload "${loop}p2" "$work/root"
}
deployment() {
  local r=$work/root d
  d=("$r"/ostree/deploy/luma/deploy/*.0); [ "${#d[@]}" = 1 ] || { why="${#d[@]} deployments"; return 1; }
  deploy=${d[0]}
  [ "$(basename "$deploy")" = "$commit.0" ] || { why="deployment $(basename "$deploy")"; return 1; }
  ref=$(sed -n 's/^refspec=//p' "$deploy.origin")
  [[ "$ref" =~ ^luma:luma/[0-9]+/aarch64/[a-z0-9-]+$ ]] || { why="origin $ref"; return 1; }
  [ "$(ostree --repo="$r/ostree/repo" rev-parse "$ref")" = "$commit" ] || { why="$ref is not $commit"; return 1; }
  [ -z "$(ostree --repo="$r/ostree/repo" show --print-metadata-key=org.projectluma.hardware-target "$commit" 2>/dev/null)" ] || { why="hardware target set"; return 1; }
  for s in a b; do [ "$(readlink -f "$r/ostree/root.$s")" = "$(readlink -f "$deploy")" ] || { why="root.$s"; return 1; }; done
  grep -qx "source=UUID=$boot_id" "$deploy/etc/luma/device-kit.conf" || { why="device-kit.conf"; return 1; }
  why="$ref"
}
generic() {
  local names found
  names=$(rpm --root "$deploy" --dbpath /usr/share/rpm -qa --qf '%{NAME}\n' 2>/dev/null || true)
  [ "$(echo "$names" | grep -c .)" -ge 100 ] || { why="cannot read the deployment's package database"; return 1; }
  found=$(echo "$names" | grep -E '^(luma-(fp6|rpi5|rpi|pixel|titan|hibreak)-|kernel-luma-(rpi|fp6))' | paste -sd' ' || true)
  why=${found:-"no device packages in $(echo "$names" | grep -c .)"}; [ -z "$found" ]
}
labels() {
  local fc=$deploy/etc/selinux/targeted/contexts/files/file_contexts out
  out=$(python3 "$repo_root/scripts/mobile/common/label-ostree-physical-root.py" --file-contexts "$fc" \
    --var home --var lib/systemd --check "$work/root" 2>&1) || { why=$(echo "$out" | head -3 | paste -sd' '); return 1; }
  why="$(getfattr --absolute-names -n security.selinux --only-values "$work/root/ostree" 2>/dev/null | tr -d '\0')"
}
clock() {
  local f=$work/root/ostree/deploy/luma/var/lib/systemd/timesync/clock built t
  [ -f "$f" ] || { why="missing"; return 1; }
  t=$(stat -c %Y "$f")
  built=$(ostree --repo="$work/root/ostree/repo" show --print-metadata-key=org.projectluma.build-timestamp "$commit" 2>/dev/null | tr -dc 0-9 || true)
  [ -n "$built" ] || built=$(date -d "$(ostree --repo="$work/root/ostree/repo" show "$commit" | sed -n 's/^Date: *//p')" +%s)
  why="$(date -u -d "@$t" +%FT%TZ)"; [ "$t" -ge "$built" ] && [ "$t" -le "$(date +%s)" ]
}
check layout layout
check boot boot
check root root
if [ "${result[root]}" = pass ]; then
  check commit deployment
  if [ "${result[commit]}" = pass ]; then check generic generic; check labels labels; check clock clock; fi
fi
verdict=pass
for name in layout boot root commit generic labels clock; do [ "${result[$name]:-fail}" = pass ] || verdict=fail; done
if [ -n "$record" ]; then
  for name in layout boot root commit generic labels clock; do printf '%s\t%s\t%s\n' "$name" "${result[$name]:-fail}" "${detail[$name]:-not run}"; done |
    python3 -c '
import datetime, json, sys
checks = {n: {"result": r, "detail": d} for n, r, d in (l.rstrip("\n").split("\t", 2) for l in sys.stdin)}
json.dump({"schema": "org.projectluma.arm-disk-image-gate/v1", "commit": sys.argv[1], "result": sys.argv[2],
           "image": sys.argv[3], "checks": checks,
           "completed_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
          open(sys.argv[4], "w"), indent=2); print(file=open(sys.argv[4], "a"))' "$commit" "$verdict" "$(basename "$image")" "$record"
fi
echo "GATE $verdict"
[ "$verdict" = pass ]
