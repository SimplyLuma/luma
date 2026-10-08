#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Derived from tools/lumaui-conform/harness/shell_capture.py at foundation.
# Changes: fail on every Eval/empty capture; record native actor bounds; capture
# both the desktop and its surface directly through ScreenshotArea.
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

import ast
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
            proc.wait(5)
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
    installed = stamped = None
    if shell.get("startup_only") or shell.get("protocol") or shell.get("gtk_protocol"):
        if len(sys.argv) < 6: raise RuntimeError("startup probe requires an explicit isolated overlay")
        installed = subprocess.check_output(["rpm", "-q", "gnome-shell"], text=True).strip()
        with open(os.path.join(overlay, "STAMP")) as f: stamped = f.read().strip()
        if stamped != installed: raise RuntimeError(f"overlay stamp {stamped} differs from installed {installed}")
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
        if shell.get("protocol") or shell.get("gtk_protocol"):
            # Resolve the opt-in desktop identity inside this private session.
            # Even an unexpected source-open fallback cannot launch Nick's app.
            applications = os.path.join(xdg["data"], "applications")
            os.makedirs(applications)
            identity = "org.projectluma.ShellWireGTK" if shell.get("gtk_protocol") else "org.projectluma.Messages"
            with open(os.path.join(applications, identity + ".desktop"), "w") as f:
                f.write("[Desktop Entry]\nType=Application\nName=Luma Wire Fixture\n"
                        "Exec=/usr/bin/false\nIcon=dialog-information\n"
                        "DBusActivatable=true\nNoDisplay=true\n")
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
        shell_proc = procs[-1]
        manifest_path = os.path.join(overlay, "STARTUP-TEST.json")
        manifest = json.load(open(manifest_path)) if os.path.isfile(manifest_path) else None
        startup = {"overlay": os.path.abspath(overlay), "settings": settings,
                   "installed_nvr": installed, "overlay_stamp": stamped, "overlay_manifest": manifest,
                   "shell_pid": shell_proc.pid, "responses": [], "cpu": [], "responsive": False}
        report_path = os.path.join(out, "startup.json")
        def save_startup():
            with open(report_path, "w") as f: json.dump(startup, f, indent=2)
        def ticks():
            with open(f"/proc/{shell_proc.pid}/stat") as f:
                fields = f.read().rsplit(")", 1)[1].split()
            return int(fields[11]) + int(fields[12])
        def sample_response(timeout=2):
            began = time.monotonic()
            try:
                result = gdbus(env, "/org/gnome/Shell", "org.gnome.Shell.Eval",
                               "global.context.unsafe_mode", timeout=timeout)
                ok = result.stdout.startswith("(true, 'true')")
                detail = (result.stdout or result.stderr).strip()[:300]
            except subprocess.TimeoutExpired:
                ok, detail = False, "D-Bus timeout"
            startup["responses"].append({"elapsed": time.monotonic() - began, "ok": ok, "detail": detail})
            save_startup()
            return ok
        startup_ticks, startup_began = ticks(), time.monotonic()
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if shell_proc.poll() is not None:
                startup["exit_code"] = shell_proc.returncode; save_startup()
                raise RuntimeError("isolated Shell exited before startup; see shell.log and startup.json")
            ready = sample_response()
            wall = time.monotonic() - startup_began
            cpu = (ticks() - startup_ticks) / os.sysconf("SC_CLK_TCK")
            startup["startup_cpu"] = {"wall_seconds": wall, "cpu_seconds": cpu,
                                      "one_core_fraction": cpu / max(wall, .001)}
            save_startup()
            if ready: break
            time.sleep(.5)
        else:
            raise RuntimeError("isolated Shell startup did not answer D-Bus within 60 s; see shell.log and startup.json")
        time.sleep(float(shell.get("settle", 2.0)))
        for _ in range(3):
            before, began = ticks(), time.monotonic()
            time.sleep(2)
            cpu_seconds = (ticks() - before) / os.sysconf("SC_CLK_TCK")
            startup["cpu"].append({"wall_seconds": time.monotonic() - began,
                                   "cpu_seconds": cpu_seconds,
                                   "one_core_fraction": cpu_seconds / (time.monotonic() - began)})
            if not sample_response(timeout=3):
                raise RuntimeError("isolated Shell lost D-Bus responsiveness after startup")
        if shell.get("startup_only"):
            observed = gdbus(env, "/org/gnome/Shell", "org.gnome.Shell.Eval",
                "({saved:Main.shelf._settings.get_value('shelf-arrangement').recursiveUnpack(),"
                "groups:Main.shelf._groups.map(g=>({visible:g.visible,edge:g.placement?.edge,"
                "monitor:g.placement?.monitor,islands:g.placement?.islands ?? [],"
                "rowIslands:g.islands.map(a=>a.islandId)}))})", timeout=3)
            ok, payload = ast.literal_eval(observed.stdout.strip().replace("true", "True", 1))
            if not ok: raise RuntimeError(f"could not verify actual Shelf arrangement: {payload}")
            startup["observed_arrangement"] = json.loads(payload)
            actual = startup["observed_arrangement"]
            if shell.get("expect_notifications_top"):
                saved_top = any(g.get("edge") == "top" and g.get("anchor") == "center" and
                                "notifications" in g.get("islands", []) for g in actual["saved"])
                resolved_top = any(g.get("edge") == "top" and "notifications" in g.get("islands", [])
                                   for g in actual["groups"])
                if shell.get("expect_notifications_only"):
                    resolved_top = any(g.get("edge") == "top" and g.get("islands") == ["notifications"]
                                       for g in actual["groups"])
                if not (saved_top and resolved_top):
                    save_startup(); raise RuntimeError("private Shell did not resolve the saved top/center notifications group")
            elif actual["saved"]:
                save_startup(); raise RuntimeError("no-arrangement control has unexpected saved settings")
        startup["responsive"] = True
        save_startup()
        if shell.get("startup_only"):
            print("isolated startup: three settled D-Bus responses; CPU measurements in startup.json", flush=True)
            return 0
        if shell.get("protocol") or shell.get("gtk_protocol"):
            producer_name = "gtk-protocol-producer.js" if shell.get("gtk_protocol") else "protocol-producer.js"
            producer = os.path.join(os.path.dirname(os.path.abspath(__file__)), producer_name)
            procs.append(subprocess.Popen(["gjs", "-m", "/usr/share/gnome-shell/org.gnome.Shell.Notifications"],
                                          env=env, stdout=log, stderr=log, start_new_session=True))
            time.sleep(1)
            procs.append(subprocess.Popen(["gjs", "-m", producer, os.path.join(out, "protocol.json")],
                                          env=env, stdout=log, stderr=log, start_new_session=True))
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                status_file = os.path.join(out, "protocol.json")
                if os.path.isfile(status_file):
                    status = json.load(open(status_file))
                    if status.get("status") == "error": raise RuntimeError(f"producer failed: {status['error']}")
                    if status.get("status") == "published": break
                time.sleep(.1)
            else: raise RuntimeError("public notification producer did not publish")
        states = shell.get("states", [])
        if not states: raise RuntimeError("no states requested")
        count = 0
        bounds = []
        for state in states:
            time.sleep(state.get("before_wait", 0) / 1000)
            for js in shell.get("reset", DEFAULT_RESET) + state.get("eval", []):
                r = gdbus(env, "/org/gnome/Shell", "org.gnome.Shell.Eval", js)
                if not r.stdout.startswith("(true"):
                    raise RuntimeError(f"{state['name']}: eval failed: {js[:80]} -> {(r.stdout or r.stderr).strip()[:200]}")
            time.sleep(state.get("wait", 800) / 1000)
            png = os.path.join(out, f"{state['name']}.png")
            r = gdbus(env, "/org/gnome/Shell/Screenshot", "org.gnome.Shell.Screenshot.Screenshot", "false", "false", png)
            if not os.path.isfile(png): raise RuntimeError(f"missing screenshot: {state['name']}: {r.stdout or r.stderr}")
            result = gdbus(env, "/org/gnome/Shell", "org.gnome.Shell.Eval", f"global.lumaStudioFixture.surface({str(state.get('quick', False)).lower()})")
            ok, payload = ast.literal_eval(result.stdout.strip().replace("true", "True", 1))
            if not ok: raise RuntimeError(f"bounds failed: {payload}")
            rect = json.loads(payload)
            if min(rect['w'], rect['h']) <= 0: raise RuntimeError(f"empty actor: {state['name']}")
            surface = os.path.join(out, f"surface-{state['name']}.png")
            shot = gdbus(env, "/org/gnome/Shell/Screenshot", "org.gnome.Shell.Screenshot.ScreenshotArea", str(rect['x']), str(rect['y']), str(rect['w']), str(rect['h']), "false", surface)
            if not os.path.isfile(surface): raise RuntimeError(f"missing surface: {state['name']}: {shot.stdout or shot.stderr}")
            bounds.append(dict(state=state['name'], **rect)); count += 1
            print(f"shell: {state['name']} {rect['w']}x{rect['h']} ok", flush=True)
        if count != len(states): raise RuntimeError(f"expected {len(states)} screenshots, got {count}")
        if shell.get("gtk_protocol"):
            status = json.load(open(os.path.join(out, "protocol.json")))
            if status.get("status") != "published" or status.get("unexpectedHomeActivation"):
                raise RuntimeError("GTK producer failed or activated its home instead of the notification target")
            if status.get("activations") != 1 or status.get("openTarget") != "activation":
                raise RuntimeError(f"GTK default action target differs: {status}")
            if status.get("actions") != ["action"] or status.get("replies") != [["fixture-conversation", "GTK fixture reply"]]:
                raise RuntimeError(f"GTK named or parameterized reply action differs: {status}")
            print("GTK protocol: replacement, default target, named target and parameterized reply PASS", flush=True)
        if shell.get("protocol"):
            status = json.load(open(os.path.join(out, "protocol.json")))
            expected_id = status.get("firstId")
            if not expected_id or status.get("id") != expected_id:
                raise RuntimeError("public replacement did not retain its notification ID")
            if status.get("replies") != 1:
                raise RuntimeError(f"expected one acknowledged reply, got {status.get('replies')}")
            if status.get("rejectedReplies") != 1 or not status.get("retryRequest"):
                raise RuntimeError("expected one pre-dispatch rejection and a stable retry request")
            if status.get("actions") != [[status.get("activationId"), "default"], [status.get("actionId"), "update"]]:
                raise RuntimeError(f"public action signals differ: {status.get('actions')}")
            if status.get("closed") != [[expected_id, 2]]:
                raise RuntimeError(f"public dismissal signal differs: {status.get('closed')}")
            print("protocol: replacement, acknowledged reply, default activation, action and dismissal PASS", flush=True)
        with open(os.path.join(out, "bounds.json"), "w") as f: json.dump(bounds, f, indent=2)
    finally:
        for p in reversed(procs):
            stop(p)
        log.close()
        shutil.rmtree(t, ignore_errors=True)
        slot.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
