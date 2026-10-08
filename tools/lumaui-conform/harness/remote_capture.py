#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""lumaui-conform, GTK side: runs where the app runs (the build server's
throwaway container, or the ThinkPad), started by gtk_capture.sh.

    remote_capture.py <run-dir> <plan.json>

Starts a private D-Bus (dbus-broker) and a headless mutter with a virtual
monitor, then runs the preview's bin/run once per state with the harness
loaded (sitecustomize.py in <run-dir>/preview/python). Nothing is drawn on a
screen, and nothing reaches the user's session bus, address book, settings
or XDG dirs: config, data, cache and state point into <run-dir>/xdg, the
system data dirs are seen without their applications/ (installed apps must not
change a capture), and the font is pinned (plan "font", default Figtree 11), so the same tree renders
the same on every machine. Everything it starts is stopped by PID.

Rationing (environment, set by gtk_capture.sh):
  LUMAUI_CONFORM_SLOTS       concurrent captures on this machine (flock on
                             ~/.cache/lumaui-conform/slot-N.lock); 0 = the
                             caller rations (the server's container route).
  LUMAUI_CONFORM_LOAD_GUARD  1 = wait up to 60 s for the 1-minute load to fall
                             below the CPU count, then give up (the ThinkPad).
The app, mutter and the bus are stopped on any exit: failure, SIGTERM/SIGINT/
SIGHUP, or the ssh session going away (the parent PID changes).
"""
from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time

SLOTS = int(os.environ.get("LUMAUI_CONFORM_SLOTS", "1"))
LOAD_GUARD = os.environ.get("LUMAUI_CONFORM_LOAD_GUARD", "1") == "1"
SLOT_DIR = os.path.expanduser("~/.cache/lumaui-conform")

BUS_CONF = ('<busconfig><type>session</type><auth>EXTERNAL</auth><policy context="default">'
            '<allow own="*"/><allow send_destination="*"/><allow receive_sender="*"/></policy></busconfig>')


def stop(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(5)


def _terminate(signum, _frame):
    raise SystemExit(128 + signum)


def watch_parent() -> None:
    """ssh without a tty sends no signal when the caller goes away; the parent changing is the sign."""
    parent = os.getppid()

    def loop() -> None:
        while os.getppid() == parent:
            time.sleep(0.5)
        os.kill(os.getpid(), signal.SIGTERM)
    threading.Thread(target=loop, daemon=True).start()


def wait_for_load() -> bool:
    if not LOAD_GUARD:
        return True
    cpus = os.cpu_count() or 1
    for i in range(61):
        load = os.getloadavg()[0]
        if load <= cpus:
            return True
        if i == 0:
            print(f"lumaui-conform: this machine is busy (1-minute load {load:.2f} > {cpus} CPUs); waiting up to 60 s",
                  file=sys.stderr, flush=True)
        time.sleep(1)
    print("lumaui-conform: still busy, skipped; try again shortly, or use the server (LUMAUI_CONFORM_HOST=server)", file=sys.stderr)
    return False


def take_slot():
    if SLOTS <= 0:
        return None
    os.makedirs(SLOT_DIR, exist_ok=True)
    said = False
    while True:
        for n in range(1, SLOTS + 1):
            f = open(os.path.join(SLOT_DIR, f"slot-{n}.lock"), "w")
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return f
            except OSError:
                f.close()
        if not said:
            print("lumaui-conform: waiting for a capture slot", file=sys.stderr, flush=True)
            said = True
        time.sleep(1)


PRELOAD = "/usr/local/lib64/lumaui-conform-preload.so"
C_ROOT = "/opt/conform"


def app_command(run_dir: str, plan: dict, base_env: dict) -> tuple[list[str], dict]:
    """How to start the app, and what it needs on top of the base environment.

    Python: bin/run <prairie module>, or bin/run --module <dotted> with the scenario's pythonpath after
    the kit (sitecustomize.py loads the harness). C/C++ (plan "binary", built by c_build.sh into
    /opt/conform): the binary with the harness preloaded (lumaui-conform-preload.so starts Python in it).
    """
    if plan.get("binary"):
        binary = plan["binary"] if plan["binary"].startswith("/") else os.path.join(C_ROOT, "app", plan["binary"])
        libs = [f"{C_ROOT}/app/lib64", f"{C_ROOT}/lumaui/lib64"]
        env = {"LD_PRELOAD": PRELOAD, "LUMAUI_CONFORM_HARNESS_DIR": os.path.join(run_dir, "preview", "python"),
               "LD_LIBRARY_PATH": ":".join(libs), "GI_TYPELIB_PATH": ":".join(f"{d}/girepository-1.0" for d in libs),
               "XDG_DATA_DIRS": f"{C_ROOT}/app/share:{C_ROOT}/lumaui/share:{base_env['XDG_DATA_DIRS']}",
               # on PATH, so the app's own desktop entries (TryExec) resolve
               "PATH": f"{C_ROOT}/app/bin:{base_env.get('PATH', '/usr/bin:/bin')}"}
        schemas = f"{C_ROOT}/app/share/glib-2.0/schemas"
        if os.path.exists(os.path.join(schemas, "gschemas.compiled")):
            env["GSETTINGS_SCHEMA_DIR"] = schemas
        return [binary, *plan.get("args", [])], env
    run = os.path.join(run_dir, "preview", "bin", "run")
    paths = [os.path.join(run_dir, "src", p) for p in plan.get("pythonpath", [])]
    if paths or "." in plan["module"]:
        return [run, "--module", plan["module"]], {"LUMAUI_EXTRA_PYTHONPATH": ":".join(paths)}
    return [run, plan["module"]], {}


def set_form_factor(env: dict, plan: dict) -> None:
    """Pin device capability for this variant, independent of window width."""
    phone = plan.get("phone", False)
    if type(phone) is not bool:
        raise ValueError("gtk capture plan phone must be a boolean")
    env["LUMA_FORM_FACTOR"] = "phone" if phone else "desktop"


def main() -> int:
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, _terminate)
    watch_parent()
    if not wait_for_load():
        return 5
    slot = take_slot()  # held until this process exits
    if slot is not None and not wait_for_load():
        return 5
    run_dir, plan_path = sys.argv[1], sys.argv[2]
    plan = json.load(open(plan_path))
    out = os.path.join(run_dir, "out")
    os.makedirs(out, exist_ok=True)
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    work = tempfile.mkdtemp(prefix="bus.", dir=run_dir)
    bus_path = os.path.join(work, "bus")
    sysbus_path = os.path.join(work, "system_bus")
    display = f"lumaui-conform-{os.getpid()}"
    drop = ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "DBUS_SYSTEM_BUS_ADDRESS", "GSK_RENDERER", "GDK_DEBUG", "GTK_THEME", "GTK_DEBUG", "PYTHONPATH")
    base_env = {k: v for k, v in os.environ.items() if k not in drop and not k.startswith("XDG_")}
    xdg = os.path.join(run_dir, "xdg")
    for sub in ("config", "data", "cache", "state", "share"):
        os.makedirs(os.path.join(xdg, sub), exist_ok=True)
    # The system data dirs minus applications/: which apps a machine has installed (and so which
    # app a file card ranks first) must not change a capture. A fixture brings its own (XDG_DATA_HOME).
    # ...except the app under test's own entry (its identity: name and icon).
    os.makedirs(os.path.join(xdg, "share", "applications"), exist_ok=True)
    for top in ("/usr/local/share", "/usr/share"):
        entry = os.path.join(top, "applications", f"{plan.get('app_id') or '-'}.desktop")
        link = os.path.join(xdg, "share", "applications", os.path.basename(entry))
        if os.path.exists(entry) and not os.path.lexists(link):
            os.symlink(entry, link)
    for top in ("/usr/share", "/usr/local/share"):
        for name in (os.listdir(top) if os.path.isdir(top) else []):
            link = os.path.join(xdg, "share", name)
            if name != "applications" and not os.path.lexists(link):
                os.symlink(os.path.join(top, name), link)
    # A private system bus too: with no services on it, apps see the same (empty) system everywhere.
    base_env.update(XDG_RUNTIME_DIR=runtime, DBUS_SESSION_BUS_ADDRESS=f"unix:path={bus_path}",
                    DBUS_SYSTEM_BUS_ADDRESS=f"unix:path={sysbus_path}",
                    XDG_CONFIG_HOME=f"{xdg}/config", XDG_DATA_HOME=f"{xdg}/data", XDG_CACHE_HOME=f"{xdg}/cache",
                    XDG_STATE_HOME=f"{xdg}/state", XDG_DATA_DIRS=f"{xdg}/share", XDG_CONFIG_DIRS="/etc/xdg",
                    PYTHONNOUSERSITE="1", XDG_SESSION_TYPE="wayland", LANG="en_US.UTF-8", TZ=plan.get("tz", "America/Chicago"))
    base_env.pop("LC_ALL", None)
    font = plan.get("font") or "Figtree 11"
    bus = sysbus = mutter = app = None
    log = open(os.path.join(out, "remote.log"), "w")

    def start_bus(path: str, name: str) -> subprocess.Popen:
        conf = os.path.join(work, f"{name}.conf")
        if os.path.exists("/run/systemd/journal/socket"):
            with open(conf, "w") as f:
                f.write(BUS_CONF)
            cmd = ["systemd-socket-activate", "-E", "DBUS_SESSION_BUS_ADDRESS", "-E", "XDG_RUNTIME_DIR", "-l", path,
                   "dbus-broker-launch", "--scope=user", f"--config-file={conf}"]
        else:  # a container: dbus-broker-launch needs the journal
            with open(conf, "w") as f:
                f.write(BUS_CONF.replace("<auth>", f"<listen>unix:path={path}</listen><auth>"))
            cmd = ["dbus-daemon", "--nofork", "--nopidfile", f"--config-file={conf}"]
        proc = subprocess.Popen(cmd, env=base_env, stdout=log, stderr=log, start_new_session=True)
        for _ in range(50):
            if os.path.exists(path):
                break
            time.sleep(0.05)
        return proc

    try:
        bus = start_bus(bus_path, "bus")
        sysbus = start_bus(sysbus_path, "system_bus")
        monitor = plan.get("monitor", "1920x1200")
        mutter = subprocess.Popen(["mutter", "--headless", "--wayland", "--no-x11", f"--wayland-display={display}",
                                   "--virtual-monitor", monitor], env=base_env, stdout=log, stderr=log, start_new_session=True)
        sock = os.path.join(runtime, display)
        for _ in range(100):
            if os.path.exists(sock):
                break
            time.sleep(0.1)
        else:
            print("lumaui-conform: headless mutter did not start (see remote.log)", file=sys.stderr)
            return 4
        command, launch_env = app_command(run_dir, plan, base_env)
        timeouts = 0
        for state in plan["states"]:
            if timeouts >= 2:
                print(f"gtk: {state['name']} skipped (two states in a row did not finish; see remote.log)", flush=True)
                continue
            env = dict(base_env)
            env.update(launch_env)
            env["LUMAUI_KEEP_APP_ID"] = "1"  # a private bus: the real app ID is safe, and the identity needs it
            env.update(WAYLAND_DISPLAY=display, GDK_BACKEND="wayland", GTK_A11Y="none", GSETTINGS_BACKEND="memory",
                       LUMAUI_CONFORM_DUMP=out, LUMAUI_CONFORM_STATE=state["name"], LUMAUI_CONFORM_SIZE=state.get("size") or plan["size"],
                       LUMAUI_CONFORM_THEME=plan["theme"], LUMAUI_CONFORM_FONT=font,
                       LUMAUI_CONFORM_ACTIONS=json.dumps([a for a in state.get("actions", []) if "env" not in a]))
            for key, value in plan.get("env", {}).items():
                env[key] = value.replace("{run}", run_dir)
            for action in state.get("actions", []):
                for key, value in action.get("env", {}).items():
                    env[key] = value.replace("{run}", run_dir)
            if plan.get("renderer"):
                env["GSK_RENDERER"] = plan["renderer"]
            # Set this after inherited, scenario and per-state env. A narrow
            # desktop keeps controls; only the phone variant folds them.
            set_form_factor(env, plan)
            app = subprocess.Popen(command, env=env, stdout=log, stderr=log, start_new_session=True)
            try:
                app.wait(int(os.environ.get("LUMAUI_CONFORM_STATE_TIMEOUT", "90")))
                timeouts = 0
            except subprocess.TimeoutExpired:
                timeouts += 1
                print(f"lumaui-conform: {state['name']} timed out", file=sys.stderr)
            stop(app)
            app = None
            print(f"gtk: {state['name']} {'ok' if os.path.exists(os.path.join(out, 'gtk-' + state['name'] + '.json')) else 'FAILED (see remote.log)'}",
                  flush=True)
    finally:
        stop(app)
        stop(mutter)
        stop(bus)
        stop(sysbus)
        log.close()
        if slot is not None:
            slot.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
