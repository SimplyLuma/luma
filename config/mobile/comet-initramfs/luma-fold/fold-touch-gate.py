#!/usr/bin/python3
# Fold gate: the hinge Hall sensor (SW_LID, 1 = folded) selects the panel that
# faces the user. Folded: outer DSI-1 on, inner off, inner touch inhibited.
# Unfolded: inner DSI-2 on, outer off, outer touch inhibited (it faces away and
# picks up fingers on the back, which GTK treats as multi-touch and then
# cancels taps). Outputs are switched through wlr-output-management
# (wlr-randr) on the session's Wayland socket when the hinge moves and when a
# new compositor appears. A blanked panel also reads as disabled there, so the
# state is never "repaired" in between: that would undo idle blanking. The posture goes to
# /run/luma/posture as one word for the session (luma-shell-state). With
# LUMA_FOLD_GATE_TOUCH=0 the touchscreens are left to the packaged
# luma-fold-gate and this only switches outputs and writes the posture.
import fcntl
import glob
import os
import select
import struct
import subprocess
import sys
import time

HINGE = "gpio-keys"
# (touchscreen input name, DRM connector, layout position)
OUTER = ("Synaptics TouchComm Touchscreen", "DSI-1", "0,0")
INNER = ("Goodix Berlin Capacitive TouchScreen", "DSI-2", "4000,0")
WLR_RANDR = "/usr/bin/wlr-randr"
POSTURE = "/run/luma/posture"
TOUCH = os.environ.get("LUMA_FOLD_GATE_TOUCH", "1") != "0"

EV_SW, SW_LID = 0x05, 0x00
# EVIOCGSW(len) = _IOC(_IOC_READ, 'E', 0x1b, len)
EVIOCGSW_8 = (2 << 30) | (8 << 16) | (ord("E") << 8) | 0x1B
EVENT = struct.Struct("llHHi")


def log(msg):
    print(msg, flush=True)


def input_dir(name):
    for d in glob.glob("/sys/class/input/input*"):
        try:
            with open(os.path.join(d, "name")) as f:
                if f.read().strip() == name:
                    return d
        except OSError:
            pass
    return None


def set_inhibited(name, inhibited):
    d = input_dir(name)
    if not d:
        return
    try:
        with open(os.path.join(d, "inhibited"), "w") as f:
            f.write("1" if inhibited else "0")
    except OSError as e:
        log(f"{name}: {e}")


def write_posture(folded):
    try:
        os.makedirs(os.path.dirname(POSTURE), mode=0o755, exist_ok=True)
        with open(POSTURE + ".new", "w") as f:
            f.write("folded\n" if folded else "unfolded\n")
        os.chmod(POSTURE + ".new", 0o644)
        os.replace(POSTURE + ".new", POSTURE)
    except OSError as e:
        log(f"posture: {e}")


def apply_touch(folded):
    write_posture(folded)
    if not TOUCH:
        log("folded" if folded else "unfolded")
        return
    set_inhibited(INNER[0], folded)
    set_inhibited(OUTER[0], not folded)
    log("folded: outer touch only" if folded else "unfolded: inner touch only")


def wayland_env():
    """The session compositor's socket: the user session first, then the greeter."""
    for uid in ("1000", "990"):
        for sock in sorted(glob.glob(f"/run/user/{uid}/wayland-*")):
            if not sock.endswith(".lock"):
                return {"XDG_RUNTIME_DIR": f"/run/user/{uid}",
                        "WAYLAND_DISPLAY": os.path.basename(sock)}
    return None


def randr(env, args, timeout):
    try:
        return subprocess.run([WLR_RANDR, *args], env=env, capture_output=True,
                              text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


def compositor_id(env):
    """Identity of the compositor's socket; changes when a compositor starts."""
    try:
        st = os.stat(os.path.join(env["XDG_RUNTIME_DIR"], env["WAYLAND_DISPLAY"]))
    except OSError:
        return None
    return (env["XDG_RUNTIME_DIR"], env["WAYLAND_DISPLAY"], st.st_ino, st.st_mtime_ns)


def apply_outputs(env, on, off):
    """Light the panel facing the user first, then turn the other one off, so
    the shell always has a monitor to move to (never zero outputs)."""
    r = randr(env, ["--output", on[1], "--on", "--pos", on[2]], 10)
    if r and r.returncode == 0:
        time.sleep(1.0)
        r = randr(env, ["--output", off[1], "--off"], 10)
    if r and r.returncode == 0:
        log(f"outputs: {on[1]} on, {off[1]} off")
        return True
    log("wlr-randr failed: " + ((r.stderr or r.stdout).strip() if r else "no result"))
    return False


def main():
    hinge = input_dir(HINGE)
    if not hinge:
        log("no hinge switch")
        return 1
    event = glob.glob(os.path.join(hinge, "event*"))[0]
    fd = os.open(os.path.join("/dev/input", os.path.basename(event)), os.O_RDONLY)

    state = bytearray(8)
    fcntl.ioctl(fd, EVIOCGSW_8, state)
    folded = bool(state[0] & (1 << SW_LID))
    apply_touch(folded)

    pending, failures, next_try, applied_to = True, 0, 0.0, None
    while True:
        ready, _, _ = select.select([fd], [], [], 3.0)
        if ready:
            data = os.read(fd, EVENT.size * 16)
            for off in range(0, len(data) - EVENT.size + 1, EVENT.size):
                _, _, etype, code, value = EVENT.unpack_from(data, off)
                if etype == EV_SW and code == SW_LID and bool(value) != folded:
                    folded = bool(value)
                    apply_touch(folded)
                    pending, failures = True, 0

        if time.monotonic() < next_try:
            continue
        env = wayland_env()
        if env is None:
            continue
        cid = compositor_id(env)
        if cid != applied_to:
            pending = True
        on, off = (OUTER, INNER) if folded else (INNER, OUTER)
        if pending:
            if apply_outputs(env, on, off):
                pending, failures, applied_to = False, 0, cid
            else:
                failures += 1
                next_try = time.monotonic() + min(3 * failures, 30)


if __name__ == "__main__":
    sys.exit(main())
