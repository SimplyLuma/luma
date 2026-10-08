#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""lumaui-conform, Shell route: headless GNOME Shell screenshots for pixel mode.

    shell_capture.py <scenario.json> <theme> <phone 0|1> <out-dir> [overlay-dir]

Runs on the ThinkPad (the Shell there is the one being patched): gnome-shell --headless with
a virtual monitor, on a private session and system bus, a private XDG_RUNTIME_DIR and private
XDG dirs, so nothing reaches Nick's session, settings or screen. The Shell's JS and theme come
from the overlay directory (G_RESOURCE_OVERLAYS=/org/gnome/shell=<dir>; default
~/.local/share/luma-shell-live/active, the live overlays). A tiny extension puts the Shell in
unsafe mode so the states can be driven through org.gnome.Shell.Eval, and each state is saved
through org.gnome.Shell.Screenshot as <out-dir>/<state>.png.

Scenario, "shell" section:
  "size": "1180x740"            the virtual monitor (phone: "phone_size", default 390x844)
  "settings": {"org/gnome/desktop/interface": {"clock-format": "'24h'"}, ...}   GSettings keyfile
  "reset": ["js", ...]          after start-up and before every state (default: clear notifications)
  "states": [{"name": "quick-options", "eval": ["js", ...], "wait": 800}, ...]
One capture at a time on this machine, and only while its load is below its CPU count
(the same slot and guard as remote_capture.py). Everything is stopped on any exit.
"""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

EXT = "lumaui-conform@projectluma.org"
BUS_CONF = ('<busconfig><type>session</type><auth>EXTERNAL</auth><policy context="default">'
            '<allow own="*"/><allow send_destination="*"/><allow receive_sender="*"/></policy></busconfig>')
EXT_JS = """import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
export default class Conform extends Extension {
    enable() { global.context.unsafe_mode = true; }
    disable() {}
}
"""
DEFAULT_RESET = ["Main.messageTray.getSources().forEach(s => s.destroy())", "Main.overview.hide()"]


def stop(proc):
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def gdbus(env, obj, method, *args, timeout=10):
    return subprocess.run(["gdbus", "call", "--session", "-d", "org.gnome.Shell", "-o", obj, "-m", method, *args],
                          env=env, capture_output=True, text=True, timeout=timeout)


def take_slot():
    cpus = os.cpu_count() or 1
    for i in range(61):
        if os.getloadavg()[0] <= cpus:
            break
        if i == 0:
            print(f"lumaui-conform: this machine is busy (load {os.getloadavg()[0]:.1f} > {cpus}); waiting up to 60 s", file=sys.stderr)
        time.sleep(1)
    else:
        print("lumaui-conform: still busy, skipped", file=sys.stderr)
        sys.exit(5)
    d = os.path.expanduser("~/.cache/lumaui-conform")
    os.makedirs(d, exist_ok=True)
    f = open(os.path.join(d, "slot-1.lock"), "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("lumaui-conform: waiting for the capture slot", file=sys.stderr, flush=True)
        fcntl.flock(f, fcntl.LOCK_EX)
    return f


def main() -> int:
    scn_path, theme, phone, out = sys.argv[1], sys.argv[2], sys.argv[3] == "1", sys.argv[4]
    overlay = sys.argv[5] if len(sys.argv) > 5 and sys.argv[5] else os.path.expanduser("~/.local/share/luma-shell-live/active")
    shell = json.load(open(scn_path)).get("shell", {})
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, lambda s, f: sys.exit(128 + s))
    slot = take_slot()
    os.makedirs(out, exist_ok=True)
    t = tempfile.mkdtemp(prefix="lumaui-conform-shell.")
    procs = []
    log = open(os.path.join(out, "shell.log"), "w")
    try:
        rt = os.path.join(t, "rt")
        os.makedirs(rt, mode=0o700)
        xdg = {k: os.path.join(t, "xdg", k) for k in ("config", "data", "cache", "state")}
        for d in xdg.values():
            os.makedirs(d)
        ext = os.path.join(xdg["data"], "gnome-shell", "extensions", EXT)
        os.makedirs(ext)
        with open(os.path.join(ext, "metadata.json"), "w") as f:
            json.dump({"uuid": EXT, "name": "lumaui-conform", "description": "capture driver", "shell-version": ["50"]}, f)
        with open(os.path.join(ext, "extension.js"), "w") as f:
            f.write(EXT_JS)
        settings = {"org/gnome/shell": {"enabled-extensions": f"['{EXT}']", "disable-user-extensions": "false",
                                        "welcome-dialog-last-shown-version": "'999'"},
                    "org/gnome/desktop/interface": {"color-scheme": "'prefer-dark'" if theme == "dark" else "'prefer-light'"}}
        for group, keys in shell.get("settings", {}).items():
            settings.setdefault(group, {}).update(keys)
        os.makedirs(os.path.join(xdg["config"], "glib-2.0", "settings"))
        with open(os.path.join(xdg["config"], "glib-2.0", "settings", "keyfile"), "w") as f:
            for group, keys in settings.items():
                f.write(f"[{group}]\n" + "".join(f"{k}={v}\n" for k, v in keys.items()))
        with open(os.path.join(t, "bus.conf"), "w") as f:
            f.write(BUS_CONF)
        env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS",
                                                                 "DBUS_SYSTEM_BUS_ADDRESS") and not k.startswith("XDG_")}
        env.update(XDG_RUNTIME_DIR=rt, XDG_CONFIG_HOME=xdg["config"], XDG_DATA_HOME=xdg["data"], XDG_CACHE_HOME=xdg["cache"],
                   XDG_STATE_HOME=xdg["state"], XDG_SESSION_TYPE="wayland", XDG_CURRENT_DESKTOP="GNOME",
                   GSETTINGS_BACKEND="keyfile", LANG="en_US.UTF-8", TZ=shell.get("tz", "America/Chicago"),
                   G_RESOURCE_OVERLAYS=f"/org/gnome/shell={overlay}")
        for name in ("bus", "sysbus"):
            path = os.path.join(t, name)
            benv = dict(env, DBUS_SESSION_BUS_ADDRESS=f"unix:path={path}")
            procs.append(subprocess.Popen(["systemd-socket-activate", "-E", "DBUS_SESSION_BUS_ADDRESS", "-E", "XDG_RUNTIME_DIR", "-l", path,
                                           "dbus-broker-launch", "--scope=user", f"--config-file={t}/bus.conf"],
                                          env=benv, stdout=log, stderr=log, start_new_session=True))
        time.sleep(0.3)
        env.update(DBUS_SESSION_BUS_ADDRESS=f"unix:path={t}/bus", DBUS_SYSTEM_BUS_ADDRESS=f"unix:path={t}/sysbus")
        size = shell.get("phone_size", "390x844") if phone else shell.get("size", "1180x740")
        procs.append(subprocess.Popen(["gnome-shell", "--headless", "--wayland", "--no-x11", "--wayland-display=lumaui-conform-shell",
                                       "--virtual-monitor", size], env=env, stdout=log, stderr=log, start_new_session=True))
        for _ in range(120):
            if gdbus(env, "/org/gnome/Shell", "org.gnome.Shell.Eval", "global.context.unsafe_mode").stdout.startswith("(true, 'true')"):
                break
            time.sleep(0.5)
        else:
            print("lumaui-conform: the headless Shell did not come up in unsafe mode (see shell.log)", file=sys.stderr)
            return 4
        time.sleep(float(shell.get("settle", 2.0)))
        for state in shell.get("states", []):
            for js in shell.get("reset", DEFAULT_RESET) + state.get("eval", []):
                r = gdbus(env, "/org/gnome/Shell", "org.gnome.Shell.Eval", js)
                if not r.stdout.startswith("(true"):
                    print(f"shell: {state['name']}: eval failed: {js[:80]} -> {(r.stdout or r.stderr).strip()[:200]}", file=sys.stderr)
            time.sleep(state.get("wait", 800) / 1000)
            png = os.path.join(out, f"{state['name']}.png")
            r = gdbus(env, "/org/gnome/Shell/Screenshot", "org.gnome.Shell.Screenshot.Screenshot", "false", "false", png)
            print(f"shell: {state['name']} {'ok' if os.path.exists(png) else 'FAILED: ' + (r.stdout or r.stderr).strip()[:200]}", flush=True)
    finally:
        for p in reversed(procs):
            stop(p)
        log.close()
        shutil.rmtree(t, ignore_errors=True)
        slot.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
