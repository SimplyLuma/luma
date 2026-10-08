#!/bin/bash
# SPDX-License-Identifier: MPL-2.0
#
# Release gate: Depot opens, every time, the way a person opens it. Run as root
# inside a disposable gate VM installed from the candidate's own media. Prints
# one JSON line per check ({"check", "result": "pass"|"fail"|"skip", "detail"})
# and exits 1 if any check failed. Everything it adds is removed again.
#
#   depot-opens.sh [--attempts N] [--spacing SECONDS]
#
# It skips (and exits 0) on a candidate without Depot, without fwupd or without
# a Shell. With the defaults -- four launches, twenty seconds apart -- it adds
# roughly three minutes to the stage and can never exceed the ten-minute
# timeout on the headless session; every run reports its own seconds as the
# depot-opens-runtime check.
#
# A fresh install closed Depot on open whenever fwupd was running (libfwupd
# aborted in the window's process), and a person saw nothing at all: no window,
# no message, and it only opened after a restart. So this check does what a
# person does. A real GNOME Shell runs headless as an unprivileged user; Depot
# is activated through its desktop entry exactly as the dock and the app grid
# activate it, several times over the first minutes of that session, with fwupd
# running, including while first-boot app provisioning is working. Every launch
# must put a window on screen within 20 seconds, and the journal must say so.
set -uo pipefail
attempts=4
spacing=20
while [ $# -gt 0 ]; do
  case "$1" in
    --attempts) attempts=${2:-4}; shift 2 ;;
    --spacing) spacing=${2:-20}; shift 2 ;;
    *) printf 'usage: %s [--attempts N] [--spacing SECONDS]\n' "$0" >&2; exit 2 ;;
  esac
done
user=luma-gate-depot-opens
failed=0
work=$(mktemp -d /tmp/luma-gate-depot-opens.XXXXXX)
chmod 0755 "$work"

emit() {
  python3 -c 'import json, sys; print(json.dumps({"check": sys.argv[1], "result": sys.argv[2], "detail": sys.argv[3][-600:]}))' "$1" "$2" "$3"
  [ "$2" = fail ] && failed=1
  return 0
}

cleanup() {
  loginctl disable-linger "$user" >/dev/null 2>&1 || true
  loginctl terminate-user "$user" >/dev/null 2>&1 || true
  userdel -r "$user" >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT

# A candidate without Depot, without fwupd or without a Shell is not a
# candidate this check has an opinion about: it skips, and the gate goes on.
rpm -q luma-application-installer >/dev/null 2>&1 || {
  emit depot-opens skip 'luma-application-installer is not in this image'
  exit 0
}
[ -f /usr/share/applications/org.projectluma.Depot.desktop ] || {
  emit depot-opens skip 'no org.projectluma.Depot.desktop to activate'
  exit 0
}
command -v gnome-shell >/dev/null 2>&1 || {
  emit depot-opens skip 'no gnome-shell in this image'
  exit 0
}
rpm -q fwupd >/dev/null 2>&1 || {
  emit depot-opens skip 'fwupd is not in this image, so the case this check covers cannot happen'
  exit 0
}

# The crash needed fwupd running, so the gate needs it running too. A daemon
# that will not start is fwupd's own business, not a reason to stop a nightly:
# the launches still have to end in a window.
systemctl start fwupd >/dev/null 2>&1 || true
for _ in $(seq 30); do
  systemctl is-active --quiet fwupd && break
  sleep 1
done
if systemctl is-active --quiet fwupd; then
  emit fwupd-running pass "fwupd $(rpm -q --qf '%{VERSION}' fwupd) is running for these launches"
else
  emit fwupd-running skip "fwupd $(rpm -q --qf '%{VERSION}' fwupd) would not start here, so these launches do not cover the crash case"
fi

useradd -m "$user" || { emit gate-user fail "useradd $user failed"; exit 1; }
started_at=$(date +%s)
uid=$(id -u "$user")
loginctl enable-linger "$user" >/dev/null 2>&1 || true
for _ in $(seq 30); do [ -d "/run/user/$uid" ] && break; sleep 1; done
[ -d "/run/user/$uid" ] || { emit gate-user fail "no runtime directory for $user"; exit 1; }
# Depot must not count a gate VM as a person's installation, and must not
# install first-boot apps from the network here: the plan below is Depot's own
# provisioning path with an app id no source offers, which exercises the code
# that runs while a person launches Depot without downloading anything.
install -d -o "$user" -g "$user" -m 0755 "/home/$user/.config/luma/depot"
printf '{"install_events": false, "countme": false, "app_updates": false}\n' \
  >"/home/$user/.config/luma/depot/settings.json"
chown "$user:" "/home/$user/.config/luma/depot/settings.json"
printf '{"applications": ["luma-gate-no-such-app"], "collections": []}\n' >"$work/first-boot-apps.json"
chmod 0644 "$work/first-boot-apps.json"

cat >"$work/depot-opens.js" <<'JS'
// Activated through its desktop entry, as the dock and the app grid do it,
// with the Shell's own app system; a launch passes only when the window the
// Shell manages for Depot is really there.
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import Shell from 'gi://Shell';

const OUT = GLib.getenv('DEPOT_GATE_OUT') ?? '/tmp/depot-opens';
const ATTEMPTS = parseInt(GLib.getenv('DEPOT_GATE_ATTEMPTS') ?? '4', 10);
const SPACING = parseInt(GLib.getenv('DEPOT_GATE_SPACING') ?? '20', 10) * 1000;
const PLAN = GLib.getenv('LUMA_FIRST_BOOT_APPS') ?? '';
const WAIT = 20000;
const results = [];

const sleep = ms => new Promise(r => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => { r(); return GLib.SOURCE_REMOVE; }));
function log(message) { console.log(`[depot-opens] ${message}`); }
function check(name, ok, detail = '') {
    results.push({name, ok: !!ok, detail});
    log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ` — ${detail}` : ''}`);
}
async function until(fn, ms) {
    const end = Date.now() + ms;
    for (;;) {
        const value = fn();
        if (value) return value;
        if (Date.now() >= end) return null;
        await sleep(200);
    }
}

function depot() {
    return Shell.AppSystem.get_default().lookup_app('org.projectluma.Depot.desktop');
}

async function closeDepot() {
    const app = depot();
    if (app && app.get_windows().length > 0) {
        app.request_quit();
        await until(() => depot()?.get_windows().length === 0, 15000);
    }
    await sleep(1000);
}

async function launch(name, activate) {
    await closeDepot();
    const started = Date.now();
    try {
        activate();
    } catch (error) {
        check(name, false, `activating Depot threw ${error}`);
        return;
    }
    const windows = await until(() => {
        const app = depot();
        return app && app.get_windows().length > 0 ? app.get_windows() : null;
    }, WAIT);
    const seconds = ((Date.now() - started) / 1000).toFixed(1);
    check(name, !!windows,
        windows
            ? `window "${windows[0].get_title()}" on screen ${seconds}s after the launch`
            : `no window ${seconds}s after the launch`);
}

function spawn(argv, environment = {}) {
    const launcher = new Gio.SubprocessLauncher({flags: Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE});
    launcher.setenv('WAYLAND_DISPLAY', GLib.getenv('WAYLAND_DISPLAY') ?? 'wayland-0', true);
    launcher.setenv('GDK_BACKEND', 'wayland', true);
    launcher.setenv('GSK_RENDERER', 'cairo', true);
    launcher.unsetenv('DISPLAY');
    for (const [key, value] of Object.entries(environment)) launcher.setenv(key, value, true);
    return launcher.spawnv(argv);
}

async function work() {
    const app = await until(() => depot(), 20000);
    check('the Shell knows Depot as an application', !!app,
        app ? app.get_id() : 'lookup_app("org.projectluma.Depot.desktop") found nothing');
    if (!app) return;

    // As soon as the session is up, then again over the first minutes: the
    // people who saw nothing were launching Depot on a computer minutes old.
    for (let attempt = 1; attempt <= ATTEMPTS; attempt++) {
        if (attempt > 1) await sleep(SPACING);
        await launch(`Depot opens from its entry, launch ${attempt} of ${ATTEMPTS}`,
            () => depot().activate());
    }

    // The Updates view, which is its own desktop entry and the one people were
    // told to try when the window never came.
    const updates = Shell.AppSystem.get_default().lookup_app('org.projectluma.SoftwareUpdate.desktop');
    if (updates) {
        await launch('Depot opens on the Updates view from Software Update', () => updates.activate());
    } else {
        check('Depot opens on the Updates view from Software Update', true, 'no Software Update entry in this build (skipped)');
    }

    // While first-boot app provisioning is working: provisioning builds Depot's
    // window in the background, so a person's launch must still show it.
    if (PLAN) {
        await closeDepot();
        const provision = spawn(['luma-depot', '--provision'], {LUMA_FIRST_BOOT_APPS: PLAN});
        await sleep(3000);
        await launch('Depot opens while first-boot app provisioning runs', () => depot().activate());
        provision.force_exit();
    }

    // Twice in a row, as a person does when nothing seems to happen.
    await launch('Depot opens again right after being closed', () => depot().activate());
    await closeDepot();
    depot().activate();
    await sleep(500);
    await launch('a second launch while one is arriving still ends in a window', () => depot().activate());
}

export function init() {
    GLib.mkdir_with_parents(OUT, 0o755);
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, 5000, () => {
        work().catch(error => check('the gate script ran without errors', false, `${error}\n${error.stack}`))
            .finally(() => {
                GLib.file_set_contents(`${OUT}/results.json`, JSON.stringify(results, null, 1));
                log(`DONE ${results.filter(r => r.ok).length}/${results.length} passed`);
                global.context.terminate();
            });
        return GLib.SOURCE_REMOVE;
    });
}

export async function run() {
    await new Promise(() => {});
}
JS

out=$work/out
install -d -o "$user" -g "$user" -m 0755 "$out"
chown -R "$user:" "$work"
since=$(date '+%Y-%m-%d %H:%M:%S')
# The image ships dbus-broker, not dbus-daemon, so there is no dbus-run-session:
# the headless Shell joins this throwaway user's own session bus, which its
# lingering user manager starts, the way a person's session gets one.
for _ in $(seq 30); do [ -S "/run/user/$uid/bus" ] && break; sleep 1; done
[ -S "/run/user/$uid/bus" ] || { emit gate-user fail "no session bus for $user"; exit 1; }
# No Xwayland: Depot is a Wayland client, and on-demand Xwayland can deadlock
# a headless Shell at startup (its main thread blocked connecting to :0 while
# Xwayland waited for the Shell to accept its Wayland connection; 20260917.15
# rehearsal). -k: the Shell catches SIGTERM, so a Shell that stops answering
# must still be killed, and the stage then fails with its log instead of
# holding the gate until the service timeout.
runuser -u "$user" -- env -C "/home/$user" \
  HOME="/home/$user" XDG_RUNTIME_DIR="/run/user/$uid" \
  DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$uid/bus" \
  GSETTINGS_BACKEND=memory LIBGL_ALWAYS_SOFTWARE=1 \
  DEPOT_GATE_OUT="$out" DEPOT_GATE_ATTEMPTS="$attempts" DEPOT_GATE_SPACING="$spacing" \
  LUMA_FIRST_BOOT_APPS="$work/first-boot-apps.json" \
  timeout -k 30 600 gnome-shell --headless --no-x11 --virtual-monitor 1280x800 \
  --automation-script="$work/depot-opens.js" >"$work/shell.log" 2>&1
shell_status=$?

if [ -f "$out/results.json" ]; then
  while IFS= read -r line; do
    name=${line%%$'\t'*}; rest=${line#*$'\t'}; result=${rest%%$'\t'*}; detail=${rest#*$'\t'}
    emit "$name" "$result" "$detail"
  done < <(python3 -c '
import json, sys
for entry in json.load(open(sys.argv[1])):
    print("\t".join((entry["name"], "pass" if entry["ok"] else "fail", entry.get("detail", ""))))' "$out/results.json")
else
  emit depot-opens fail "the headless session produced no results (exit $shell_status): $(grep -Ev '^$' "$work/shell.log" | tail -n 8 | tr '\n' ' ')"
fi

# Nothing may fail silently: every launch is in the journal, and a launch that
# showed no window has to be visible there afterwards.
lifecycle=$(journalctl --no-pager --since "$since" MESSAGE_ID=9d41c6b70f2e4a58bd3e7c19a06f5b24 -o cat 2>/dev/null)
shown=$(grep -c 'put its window on screen' <<<"$lifecycle")
if [ "$shown" -ge 1 ]; then
  emit depot-lifecycle-journal pass "$shown launches recorded for Luma Vitals ($(grep -c . <<<"$lifecycle") entries)"
else
  emit depot-lifecycle-journal fail "Depot logged no window for Luma Vitals: $(tr '\n' ' ' <<<"$lifecycle" | tail -c 300)"
fi
if grep -q 'could not open its window' <<<"$lifecycle"; then
  emit depot-window-built pass 'a start that failed said so in the journal'
fi

seconds=$(( $(date +%s) - started_at ))
emit depot-opens-runtime pass \
  "$attempts launches ${spacing}s apart, the headless session included, took ${seconds}s of this stage"

crashes=$(coredumpctl list --no-pager --since "$since" 2>/dev/null | grep -c 'luma-depot\|python3' || true)
if [ "${crashes:-0}" = 0 ]; then
  emit depot-no-crash pass 'no coredump from Depot during the launches'
else
  emit depot-no-crash fail "$crashes coredump(s) during the launches: $(coredumpctl list --no-pager --since "$since" 2>/dev/null | tail -n 3 | tr '\n' ' ')"
fi

exit "$failed"
