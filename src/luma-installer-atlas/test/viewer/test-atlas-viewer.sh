#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-2.1-or-later
#
# Tests atlas-viewer's watchdog with a stub viewer and a stub error screen.
#
#   test-atlas-viewer.sh [PATH-TO-atlas-viewer]

set -uo pipefail

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
viewer=${1:-$here/../../libexec/atlas-viewer}
viewer=$(CDPATH= cd -- "$(dirname -- "$viewer")" && pwd)/$(basename -- "$viewer")
root=$(mktemp -d)
trap 'rm -rf "$root"' EXIT

passed=0
failed=0

check() {
    if eval "$2"; then
        passed=$((passed + 1))
    else
        failed=$((failed + 1))
        printf 'FAIL: %s\n    %s\n' "$1" "$2"
        [ -f "$work/log" ] && sed 's/^/    log: /' "$work/log" | tail -n 15
    fi
}

cat >"$root/browser" <<'EOF'
#!/usr/bin/env bash
# Stub viewer: plays the next line of $STUB_WORK/plan.
touch "$STUB_WORK/runs"
n=$(( $(wc -l <"$STUB_WORK/runs") + 1 ))
printf '%s|%s|%s|%s|%s\n' "$n" "$$" "${QT_QUICK_BACKEND:-gpu}" "${QTWEBENGINE_CHROMIUM_FLAGS:-}" "$*" >>"$STUB_WORK/runs"
action=$(sed -n "${n}p" "$STUB_WORK/plan" 2>/dev/null)
beat() { date +%s >"$ATLAS_VIEWER_STATE_DIR/viewer-heartbeat"; }
trap 'exit 143' TERM
case "${action:-blank}" in
    draw) beat; while :; do sleep 0.1; done ;;
    draw-exit0) beat; sleep 1.5; exit 0 ;;
    draw-crash) beat; sleep 1.5; exit 139 ;;
    blank) while :; do sleep 0.1; done ;;
    exit*) exit "${action#exit}" ;;
esac
EOF

cat >"$root/fallback" <<'EOF'
#!/usr/bin/env bash
# Stub error screen: records its arguments, exits with the next planned code.
touch "$STUB_WORK/fallbacks"
n=$(( $(wc -l <"$STUB_WORK/fallbacks") + 1 ))
printf '%s\n' "$*" >>"$STUB_WORK/fallbacks"
code=$(sed -n "${n}p" "$STUB_WORK/fallback-plan" 2>/dev/null)
exit "${code:-0}"
EOF
cat >"$root/plymouth" <<'EOF'
#!/usr/bin/env bash
if [ "$1" = --ping ]; then exit 0; fi
printf '%s\n' "$*" >>"$STUB_WORK/splash"
[ -s "$ATLAS_VIEWER_STATE_DIR/viewer-heartbeat" ] && echo frame >>"$STUB_WORK/splash"
EOF
chmod +x "$root/browser" "$root/fallback" "$root/plymouth"

# scenario NAME CMDLINE BROWSER-PLAN FALLBACK-PLAN: runs the viewer to its end.
scenario() {
    work="$root/$1"
    mkdir -p "$work/state"
    printf '%s\n' "$2" >"$work/cmdline"
    printf '%s\n' $3 >"$work/plan"
    printf '%s\n' $4 >"$work/fallback-plan"
    touch "$work/runs" "$work/fallbacks" "$work/splash"
    [ "${HANDOFF-absent}" = absent ] || touch "$work/policy"
    STUB_WORK=$work \
    ATLAS_VIEWER_RUNAS=direct \
    ATLAS_VIEWER_HANDOFF_POLICY="$work/policy" \
    ATLAS_VIEWER_PLYMOUTH="$root/plymouth" \
    ATLAS_VIEWER_BROWSER="$root/browser" \
    ATLAS_VIEWER_FALLBACK="$root/fallback" \
    ATLAS_VIEWER_LOG="$work/log" \
    ATLAS_VIEWER_STATE_DIR="$work/state" \
    ATLAS_VIEWER_CMDLINE="$work/cmdline" \
    ATLAS_VIEWER_FIRST_FRAME=${FIRST_FRAME-2} \
    ATLAS_VIEWER_POLL=0.2 \
        "$viewer" "$(id -un)" http://127.0.0.1/atlas SAMPLE=1 >/dev/null 2>&1
    status=$?
}

runs() { wc -l <"$work/runs" | tr -d ' '; }
run_field() { sed -n "${1}p" "$work/runs" | cut -d'|' -f"$2"; }
fallbacks() { wc -l <"$work/fallbacks" | tr -d ' '; }

scenario draws-in-software "quiet rd.live.ram=1" "draw-exit0" ""
check "software mode draws and closes normally" '[ "$status" = 0 ]'
check "one viewer started" '[ "$(runs)" = 1 ]'
check "Qt Quick renders in software" '[ "$(run_field 1 3)" = software ]'
check "Chromium runs without the GPU" 'run_field 1 4 | grep -q -- "--disable-gpu --disable-gpu-compositing"'
check "software mode keeps the sandbox" '! run_field 1 4 | grep -q -- "--no-sandbox"'
check "the viewer gets the URL" 'run_field 1 5 | grep -q "http://127.0.0.1/atlas"'
check "no error screen" '[ "$(fallbacks)" = 0 ]'
check "the first frame is logged" 'grep -q "Atlas drew its first frame in software mode" "$work/log"'
check "the heartbeat file is under the state directory" '[ -s "$work/state/viewer-heartbeat" ]'

check "without runtime policy the watchdog never calls Plymouth" '[ ! -s "$work/splash" ]'
HANDOFF=present scenario first-frame-handoff "rhgb quiet" "draw-exit0" ""
check "native splash releases only after the real frame" '[ "$status" = 0 ] && [ "$(cat "$work/splash")" = "$(printf "quit\nframe")" ]'
HANDOFF=present scenario error-handoff "rhgb quiet" "blank blank" "0"
check "failed viewer releases splash for the error screen" '[ "$status" = 1 ] && grep -Fxq quit "$work/splash" && ! grep -Fxq frame "$work/splash" && [ "$(fallbacks)" = 1 ]'

scenario never-draws "quiet" "blank blank" "0"
check "nothing drawn: exit 1 after the error screen closes" '[ "$status" = 1 ]'
check "nothing drawn: software, then safe mode" '[ "$(runs)" = 2 ] && [ "$(run_field 1 3)" = software ] && [ "$(run_field 2 3)" = software ]'
check "safe mode: one process, no sandbox, no /dev/shm" 'run_field 2 4 | grep -q -- "--no-sandbox --single-process --disable-dev-shm-usage"'
check "nothing drawn: the error screen says no-frame" 'grep -q -- "--reason no-frame" "$work/fallbacks"'
check "nothing drawn: the detail names both modes" 'grep -q "software mode: nothing drawn within 2s. safe mode: nothing drawn within 2s." "$work/fallbacks"'
check "nothing drawn: no viewer is left running" '! kill -0 "$(run_field 2 2)" 2>/dev/null'
check "diagnostics are logged" 'grep -q "boot options of interest" "$work/log" && grep -q "Anaconda.s backend" "$work/log"'

scenario exits-then-safe "quiet" "exit1 draw-exit0" ""
check "an early exit moves to safe mode, which draws" '[ "$status" = 0 ] && [ "$(runs)" = 2 ] && [ "$(fallbacks)" = 0 ]'
check "the early exit status is logged" 'grep -q "exited in software mode before Atlas drew anything (exit status 1)" "$work/log"'

scenario try-again "quiet" "blank blank draw-exit0" "10"
check "Try again starts safe mode again" '[ "$status" = 0 ] && [ "$(runs)" = 3 ] && run_field 3 4 | grep -q -- "--single-process"'
check "Try again: one error screen" '[ "$(fallbacks)" = 1 ]'

scenario gpu-first "quiet inst.luma.viewer=gpu" "draw-exit0" ""
check "inst.luma.viewer=gpu starts with the GPU" '[ "$status" = 0 ] && [ "$(run_field 1 3)" = gpu ] && ! run_field 1 4 | grep -q -- "--disable-gpu"'

scenario gpu-then-software "inst.luma.viewer=gpu" "blank draw-exit0" ""
check "gpu mode that draws nothing falls back to software" '[ "$status" = 0 ] && [ "$(run_field 2 3)" = software ] && ! run_field 2 4 | grep -q -- "--single-process"'

scenario safe-first "inst.luma.viewer=safe" "draw-exit0" ""
check "inst.luma.viewer=safe starts in safe mode" '[ "$status" = 0 ] && run_field 1 4 | grep -q -- "--single-process"'

scenario crash-after-ready "quiet" "draw-crash draw-exit0" "10"
check "a crash after the first frame shows the error screen" 'grep -q -- "--reason crashed" "$work/fallbacks"'
check "a crash is never restarted without asking" '[ "$(fallbacks)" = 1 ]'
check "after a crash, Open it again uses the same mode" '[ "$status" = 0 ] && [ "$(runs)" = 2 ] && [ "$(run_field 2 3)" = software ] && ! run_field 2 4 | grep -q -- "--single-process"'

scenario fallback-preview "inst.luma.viewer=fallback" "draw-exit0" "10"
check "inst.luma.viewer=fallback shows the error screen first" 'sed -n 1p "$work/fallbacks" | grep -q -- "--reason preview"'
check "from the preview, Start the installer uses software mode" '[ "$status" = 0 ] && [ "$(runs)" = 1 ] && [ "$(run_field 1 3)" = software ]'

scenario bad-mode "inst.luma.viewer=bogus" "draw-exit0" ""
check "an unknown mode is logged and software is used" '[ "$status" = 0 ] && [ "$(run_field 1 3)" = software ] && grep -q "ignoring inst.luma.viewer=bogus" "$work/log"'

FIRST_FRAME= scenario deadline-option "inst.luma.viewer.first-frame=7" "draw-exit0" ""
check "inst.luma.viewer.first-frame sets the deadline" 'grep -q "Atlas has 7s to draw" "$work/log"'
FIRST_FRAME= scenario deadline-bounds "inst.luma.viewer.first-frame=2" "draw-exit0" ""
check "a first-frame deadline below 5s is ignored" 'grep -q "Atlas has 60s to draw" "$work/log" && grep -q "ignoring inst.luma.viewer.first-frame=2" "$work/log"'

# An error screen that cannot start three times: the viewer logs where the
# logs are and then waits for webui-desktop, so it runs in the background here
# and is stopped from outside.
work="$root/fallback-broken"
mkdir -p "$work/state"
printf 'quiet\n' >"$work/cmdline"
printf '%s\n' blank blank >"$work/plan"
printf '%s\n' 3 3 3 >"$work/fallback-plan"
touch "$work/runs" "$work/fallbacks"
STUB_WORK=$work ATLAS_VIEWER_RUNAS=direct ATLAS_VIEWER_BROWSER="$root/browser" ATLAS_VIEWER_FALLBACK="$root/fallback" \
ATLAS_VIEWER_LOG="$work/log" ATLAS_VIEWER_STATE_DIR="$work/state" ATLAS_VIEWER_CMDLINE="$work/cmdline" \
ATLAS_VIEWER_FIRST_FRAME=1 ATLAS_VIEWER_POLL=0.1 \
    "$viewer" "$(id -un)" http://127.0.0.1/atlas >/dev/null 2>&1 &
viewer_pid=$!
for _ in $(seq 150); do
    grep -q "nothing can be drawn on this screen" "$work/log" 2>/dev/null && break
    sleep 0.1
done
check "a broken error screen is tried three times" '[ "$(fallbacks)" = 3 ]'
check "then the log locations are logged" 'grep -q "nothing can be drawn on this screen. Logs:" "$work/log"'
check "and the watchdog stays up for webui-desktop" 'kill -0 "$viewer_pid" 2>/dev/null'
kill -TERM "$viewer_pid" 2>/dev/null
wait "$viewer_pid" 2>/dev/null

work="$root/terminate"
mkdir -p "$work/state"
printf 'quiet\n' >"$work/cmdline"
printf 'draw\n' >"$work/plan"
touch "$work/runs" "$work/fallbacks" "$work/fallback-plan"
STUB_WORK=$work ATLAS_VIEWER_RUNAS=direct ATLAS_VIEWER_BROWSER="$root/browser" ATLAS_VIEWER_FALLBACK="$root/fallback" \
ATLAS_VIEWER_LOG="$work/log" ATLAS_VIEWER_STATE_DIR="$work/state" ATLAS_VIEWER_CMDLINE="$work/cmdline" \
ATLAS_VIEWER_FIRST_FRAME=5 ATLAS_VIEWER_POLL=0.2 \
    "$viewer" "$(id -un)" http://127.0.0.1/atlas >/dev/null 2>&1 &
viewer_pid=$!
for _ in $(seq 50); do
    [ -s "$work/state/viewer-heartbeat" ] && [ -s "$work/runs" ] && break
    sleep 0.1
done
sleep 0.5
kill -TERM "$viewer_pid"
wait "$viewer_pid"
status=$?
check "TERM from webui-desktop ends the watchdog cleanly" '[ "$status" = 0 ]'
check "TERM stops the viewer too" '! kill -0 "$(run_field 1 2)" 2>/dev/null'
check "TERM is logged" 'grep -q "asked to close" "$work/log"'
check "the viewer is asked for fullscreen" 'run_field 1 5 | grep -q -- "--fullscreen"'

printf '%s passed, %s failed\n' "$passed" "$failed"
[ "$failed" = 0 ]
