#!/bin/bash
# lumaui-conform service on the build server: one whole conform run (spec,
# GTK capture, compare) in throwaway containers of localhost/lumaui-conform-thinkpad.
# Installed as /srv/lumaui-conform/conform-serve.sh by install.sh; the ThinkPad's
# key may run only this (authorized_keys command=...).
#
#   tar of wt/ [kit/] [pixel/] | conform-serve.sh <app> [--all | --theme dark|light] [--phone] [--spec-only] [--pixel]
#
# stdin:  a tar with wt/ (the caller's worktree) and, for repos without the
#         kit, kit/ (a LumaUI foundation tree).
# stdout: a tar of <app>/<stamp>-<variant>/ (REPORT.md, PNGs, JSON). Progress
#         goes to stderr. Exit: 0 PASS, 1 FAIL, 2+ could not run.
# At most 12 variants run at once on this machine (flock slots), 4 while the
# nightly build (luma-os-nightly.service) runs; each run is a
# private directory under runs/ and named containers, removed on any exit,
# including the caller hanging up.
set -euo pipefail
umask 022
root=/srv/lumaui-conform
image=localhost/lumaui-conform-thinkpad
slots=12          # at most this many variants at once...
nightly_slots=4   # ...and this many while the nightly OS build runs, which must never starve
min_free_gb=20

say() { echo "lumaui-conform: $*" >&2; }
usage() { say "usage: <app> [--all | --theme dark|light] [--phone] [--spec-only] [--pixel]"; exit 2; }

# Words only: a forced command must never evaluate what the caller sent.
read -r -a argv <<< "${SSH_ORIGINAL_COMMAND:-$*}"
[ "$(basename -- "${argv[0]:-x}")" = conform-serve.sh ] && argv=("${argv[@]:1}")
app=${argv[0]:-}
[[ "$app" =~ ^[a-z][a-z0-9_-]{0,40}$ ]] || usage
theme=dark phone= all= spec_only= pixel=
i=1
while [ $i -lt ${#argv[@]} ]; do
  case "${argv[$i]}" in
    --all) all=1 ;;
    --theme) i=$((i + 1)); theme=${argv[$i]:-}; [[ "$theme" =~ ^(dark|light)$ ]] || usage ;;
    --phone) phone=1 ;;
    --spec-only) spec_only=1 ;;
    --pixel) pixel=1 ;;  # the caller's PNGs are in the tar under pixel/
    *) usage ;;
  esac
  i=$((i + 1))
done
if [ -n "$all" ]; then variants=("dark" "light" "dark phone" "light phone"); else variants=("$theme${phone:+ phone}"); fi

id=$(date +%Y%m%d-%H%M%S)-$$
stamp=${id%-*}
work=$root/runs/$id
cleanup() {
  status=$?
  trap - EXIT INT TERM HUP
  podman ps -a -q --filter "name=^lumaui-conform-$id-" | xargs -r podman rm -f -t 3 >/dev/null 2>&1 || true
  rm -f "$root"/queue/*-"$id"-* "$root"/held/"$id".* 2>/dev/null
  jobs -p | xargs -r kill 2>/dev/null || true
  rm -rf "$work"
  exit $status
}
trap cleanup EXIT
trap 'exit 130' INT TERM HUP
# The caller hanging up closes the ssh session; nothing signals us, so watch for it.
parent=$PPID
( while kill -0 "$parent" 2>/dev/null; do sleep 1; done; kill -TERM $$ 2>/dev/null ) &

free_gb=$(df --output=avail -BG / | tail -1 | tr -dc 0-9)
if [ "${free_gb:-0}" -lt $min_free_gb ]; then say "the server has ${free_gb} GB free on / (keeps $min_free_gb GB); try later"; exit 4; fi
mkdir -p "$work/src" "$work/tools" "$work/out" "$root/slots"
chown -R 1000:1000 "$work"
# The nightly OS build must never starve. It is a oneshot, so while it runs systemd reports it
# "activating" (not "active"); any luma-os-nightly* unit counts, and so does the quarter hour
# before its timer fires (00:00 America/Chicago), so running captures drain before it starts.
nightly_near() {
  [ -n "$(systemctl list-units 'luma-os-nightly*.service' --state=active,activating,reloading --no-legend --plain 2>/dev/null)" ] && return 0
  local hm; hm=$(TZ=America/Chicago date +%H%M)
  [ "$hm" -ge 2345 ] || [ "$hm" -lt 15 ]
}

run() {  # run <name> <podman args...>: a named throwaway container, output to stderr
  local name=lumaui-conform-$id-$1; shift
  local cpus=3
  nightly_near && cpus=2
  podman run --rm --init --name "$name" --security-opt label=disable --pids-limit 4096 --memory 8g --cpus $cpus --shm-size 1g "$@" >&2
}

# 1. Unpack the caller's trees and assemble the tools: the server's copy, with the
#    worktree's (else the kit's) measuring parts and scenarios when it has them.
run unpack -i --network=none -v "$work:/w" -v "$root/tools/lumaui-conform:/srv-tools:ro" "$image" sh -euc '
  tar -x -C /w/src --no-same-owner --no-same-permissions
  [ -d /w/src/wt ] || { echo "lumaui-conform: the tar has no wt/" >&2; exit 2; }
  cp -R /srv-tools/. /w/tools/
  from=server
  for t in /w/src/kit/tools/lumaui-conform /w/src/wt/tools/lumaui-conform; do
    [ -d "$t/scenarios" ] && cp -R "$t/scenarios/." /w/tools/scenarios/
    [ -f "$t/compare.js" ] || continue
    for f in spec_capture.js compare.js lib accepted-deviations.json harness/lumaui_conform_harness.py harness/sitecustomize.py; do
      [ -e "$t/$f" ] && rm -rf "/w/tools/$f" && cp -R "$t/$f" "/w/tools/$f"
    done
    from=${t#/w/src/}
  done
  echo "$from" > /w/tools/MEASURED-BY' || exit 2
[ -f "$work/tools/scenarios/$app.json" ] || { say "no scenario for $app (tools/lumaui-conform/scenarios/$app.json)"; exit 2; }
variant_budget=$(python3 "$root/tools/lumaui-conform/server/variant-budget.py" "$work/tools/scenarios/$app.json") || exit 2
say "$app: measuring parts from $(cat "$work/tools/MEASURED-BY"); ${#variants[@]} variant(s), up to $slots at once on the server ($nightly_slots during the nightly build)"
say "$app: complete-variant budget ${variant_budget}s, including declared Studio waits"


# Admission. A capture needs a free slot (flock on slots/slot-N.lock, held until it exits) and a
# 1-minute load of at most max_load: an oversubscribed server gets slower, not faster. Waiting
# captures queue in queue/ (<ticket>-<run>-<what>-<pid>); only the head may start, the head being
# the oldest entry among the runs holding the fewest captures (held/<run>.<what>.<pid>), so one
# run's --all interleaves with others instead of starving them. Entries of dead processes are
# dropped. After 20 minutes of load alone holding a capture back, it starts once no capture runs.
max_load=24
slot_take() {  # slot_take <what>
  local what=$1 s cap waited=0 me head load
  mkdir -p "$root/queue" "$root/held"
  me=$root/queue/$(date +%s%N)-$id-$what-$BASHPID
  : > "$me"
  while :; do
    exec {qlock}>"$root/queue.lock"
    flock "$qlock"
    head=$(python3 "$root/queue-head.py" "$root")
    if [ "$head" = "${me##*/}" ]; then
      load=$(cut -d' ' -f1 /proc/loadavg)
      if awk -v l="$load" -v m=$max_load 'BEGIN { exit !(l <= m) }' || { [ $waited -ge 1200 ] && [ -z "$(podman ps -q --filter name=lumaui-conform-)" ]; }; then
        cap=$slots
        nightly_near && cap=$nightly_slots
        for s in $(seq 1 $cap); do
          exec {slot_fd}>"$root/slots/slot-$s.lock"
          if flock -n "$slot_fd"; then
            rm -f "$me"; : > "$root/held/$id.$what.$BASHPID"
            exec {qlock}>&-
            return 0
          fi
          exec {slot_fd}>&-
        done
        why="all $cap server slots are busy$(nightly_near && echo " (nightly build: $nightly_slots slots)" || true)"
      else
        why="the server is loaded (1-minute load $load > $max_load)"
      fi
    else
      why="queued: $(ls "$root/queue" | wc -l) captures waiting"
    fi
    exec {qlock}>&-
    [ $((waited % 60)) = 0 ] && say "$app ($what): $why; waiting ($waited s so far)"
    sleep 2
    waited=$((waited + 2))
  done
}
kit=/w/src/wt
[ -d "$work/src/kit" ] && kit=/w/src/kit
image_run=$image
extra=()

# 2. C/C++ apps (scenario gtk.build): build once, before the captures. One build of an app
#    at a time (its meson dirs live in cache/<app>); ccache is shared by every app.
if [ -z "$pixel$spec_only" ] && python3 -c 'import json, sys; sys.exit(0 if "build" in json.load(open(sys.argv[1])).get("gtk", {}) else 1)' "$work/tools/scenarios/$app.json"; then
  image_run=localhost/lumaui-conform-cbuild
  mkdir -p "$root/cache/$app" "$root/ccache" "$root/srpms" "$work/dist/conform"
  chown 1000:1000 "$root/cache/$app" "$root/ccache" "$work/dist" "$work/dist/conform"
  exec {app_lock}>"$root/cache/$app.lock"
  flock -n "$app_lock" || { say "$app: waiting for another build of $app"; flock "$app_lock"; }
  slot_take build
  if ! run build --network=none -v "$work/src:/w/src:ro" -v "$work/tools:/w/tools:ro" -v "$root/cache/$app:/cache" \
    -v "$root/ccache:/ccache" -v "$work/dist/conform:/opt/conform" -v "$root/srpms:/srpms:ro" \
    -e LUMAUI_CONFORM_KIT=$kit -e CCACHE_DIR=/ccache -e CCACHE_MAXSIZE=15G \
    "$image_run" bash /w/tools/c_build.sh "/w/tools/scenarios/$app.json" 2>&1 | sed -u "s/^/[build] /" >&2; then
    say "$app: the build failed (above)"; exit 3
  fi
  exec {slot_fd}>&- {app_lock}>&-
  rm -f "$root/held/$id.build.$$"
  extra=(-v "$work/dist/conform:/opt/conform:ro")
fi

# 3. Keep the served Studio tree stable while its content is hashed and captured.
#    install.sh --design takes the exclusive lock for rsync. The cache contains
#    only complete Studio reference pairs; native GTK runs on every invocation.
mkdir -p "$root/reference-cache"
exec {design_lock}>"$root/design.lock"
flock -s "$design_lock"
image_id=$(podman image inspect --format '{{.Id}}' "$image_run") || exit 2

# 4. One container per variant, each holding one of the server's slots.
variant() {  # variant <n> <theme> [phone]
  local n=$1 t=$2 ph=${3:-} reference_identity="$work/reference-$1.json" run_status
  local -a reference_options=()
  slot_take "v$n"
  if python3 "$work/tools/server/reference-cache.py" prepare \
      --design "$root/design" --scenario "$work/tools/scenarios/$app.json" \
      --capture "$work/tools/spec_capture.js" --loader "$work/tools/lib/playwright.js" \
      --image-id "$image_id" --theme "$t" ${ph:+--phone} --output "$reference_identity"; then
    reference_options=(-v "$root/reference-cache:/w/reference-cache:ro" -v "$reference_identity:/w/reference-identity.json:ro" \
      -e LUMAUI_CONFORM_REFERENCE_CACHE=/w/reference-cache -e LUMAUI_CONFORM_REFERENCE_IDENTITY=/w/reference-identity.json)
  else
    say "$app ($t${ph:+ phone}): reference cache unavailable; capturing Studio"
    reference_identity=
  fi
  if run "v$n" "${extra[@]}" -v "$work/src:/w/src:ro" -v "$work/tools:/w/tools:ro" -v "$root/design:/design:ro" -v "$work/out:/w/out" "${reference_options[@]}" \
    -e LUMAUI_CONFORM_HOST=local -e LUMAUI_CONFORM_OUT=/w/out -e LUMAUI_CONFORM_REPO=/w/src/wt -e LUMAUI_CONFORM_KIT=$kit \
    -e LUMAUI_CONFORM_STUDIO_ROOT=/design -e LUMAUI_CONFORM_STAMP="$stamp" \
    "$image_run" timeout "$variant_budget" sh /w/tools/run.sh "$app" --theme "$t" ${ph:+--phone} ${spec_only:+--spec-only} ${pixel:+--pixel /w/src/pixel} 2>&1 | sed -u "s/^/[$t${ph:+ phone}] /" >&2; then
    run_status=0
  else
    run_status=${PIPESTATUS[0]}
  fi
  if [ -n "$reference_identity" ] && [ -f "$work/out/$app/$stamp-$t${ph:+-phone}/spec-meta.json" ]; then
    python3 "$work/tools/server/reference-cache.py" publish \
      --cache "$root/reference-cache" --identity "$reference_identity" \
      --out "$work/out/$app/$stamp-$t${ph:+-phone}" || say "$app ($t${ph:+ phone}): reference cache publication failed; capture retained"
  fi
  return "$run_status"
}
pids=()
n=0
for v in "${variants[@]}"; do
  n=$((n + 1))
  # shellcheck disable=SC2086
  variant $n $v &
  pids+=($!)
done
status=0
for p in "${pids[@]}"; do
  wait "$p" && s=0 || s=$?
  [ $s -gt $status ] && status=$s
done

# 5. Hand back the run folders.
tar -C "$work/out" -cf - .
exit $status
