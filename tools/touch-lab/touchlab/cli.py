# SPDX-License-Identifier: Apache-2.0
"""touchlab: the user-side command.

    touchlab sudo-command          print the one command that starts the recorder
    touchlab status                is it recording, how much is on disk
    touchlab mark wrong|almost     label the last 3 seconds (bound to shortcuts)
    touchlab pause | resume | stop
    touchlab sync-settings         copy the touchpad settings libinput should mirror
    touchlab synth [--root DIR]    write a synthetic session
    touchlab report [--root DIR] [--session ID ...] [--out FILE]
    touchlab features [--root DIR] [--out DIR]
    touchlab export-record SESSION --out FILE.yml   libinput-record format, for libinput replay
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

from .labels import LabelLog, MARKS, data_root, now_ns

UNIT = "luma-touch-lab"


def app_dir() -> str:
    return os.path.join(data_root(), "app")


def sudo_command(user: str | None = None) -> str:
    import pwd
    pw = pwd.getpwnam(user or os.environ.get("USER") or pwd.getpwuid(os.getuid()).pw_name)
    home = os.path.realpath(pw.pw_dir)
    root = os.path.join(home, ".local/share/luma-touch-lab")
    props = [
        "Nice=10", "IOSchedulingClass=idle", "MemoryMax=256M", "CPUQuota=20%", "OOMScoreAdjust=500",
        "NoNewPrivileges=yes", "ProtectSystem=strict", f"ReadWritePaths={root}",
        f"ReadWritePaths=-/run/user/{pw.pw_uid}", "PrivateNetwork=yes", "PrivateTmp=yes",
        "ProtectKernelTunables=yes", "ProtectKernelModules=yes", "ProtectControlGroups=yes",
        "RestrictAddressFamilies=AF_UNIX", "'CapabilityBoundingSet=CAP_SETUID CAP_SETGID CAP_DAC_READ_SEARCH'",
        "TimeoutStopSec=5",
    ]
    return (f"sudo systemd-run --unit={UNIT} --collect " + " ".join(f"-p {p}" for p in props) +
            f" /usr/bin/python3 -I -B {root}/app/touchlab/recorder.py --user {pw.pw_name}")


def read_status(root: str) -> dict | None:
    try:
        with open(os.path.join(root, "control", "status.json"), encoding="utf-8") as fh:
            st = json.load(fh)
    except (OSError, ValueError):
        return None
    age = (time.clock_gettime_ns(time.CLOCK_MONOTONIC) - st.get("updated_monotonic_ns", 0)) / 1e9
    st["age_s"] = age
    st["running"] = 0 <= age < 10
    return st


def notify(text: str) -> None:
    try:
        subprocess.Popen(
            ["gdbus", "call", "--session", "--dest", "org.freedesktop.Notifications",
             "--object-path", "/org/freedesktop/Notifications", "--method",
             "org.freedesktop.Notifications.Notify", "Touch Lab", "0", "", text, "", "[]",
             "{'transient': <true>, 'urgency': <byte 0>}", "1500"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def gsettings_touchpad() -> dict:
    def get(key: str, schema: str = "org.gnome.desktop.peripherals.touchpad"):
        try:
            out = subprocess.run(["gsettings", "get", schema, key], capture_output=True, text=True,
                                 timeout=5).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return None
        if out in ("true", "false"):
            return out == "true"
        if out.startswith("'"):
            return out.strip("'")
        try:
            return float(out.split()[-1])
        except ValueError:
            return None
    tp: dict = {}
    for key in ("tap-to-click", "tap-and-drag", "tap-and-drag-lock", "natural-scroll", "disable-while-typing",
                "middle-click-emulation", "speed"):
        v = get(key)
        if v is not None:
            tp[key] = v
    click = get("click-method")
    if click in ("fingers", "areas", "none"):
        tp["click-method"] = click
    tap_map = get("tap-button-map")
    if tap_map in ("lrm", "lmr"):
        tp["tap-button-map"] = tap_map
    accel = get("accel-profile")
    if accel in ("flat", "adaptive"):
        tp["accel-profile"] = accel
    if get("two-finger-scrolling-enabled"):
        tp["scroll-method"] = "two-finger"
    elif get("edge-scrolling-enabled"):
        tp["scroll-method"] = "edge"
    timeout = get("disable-while-typing-timeout")
    extra = {"disable-while-typing-timeout": timeout} if timeout is not None else {}
    return {"touchpad": tp, "not_mirrored": extra, "source": "gsettings",
            "updated": time.strftime("%Y-%m-%dT%H:%M:%S%z")}


def cmd_status(args) -> int:
    root = args.root or data_root()
    st = read_status(root)
    if not st or not st["running"]:
        print("Not recording.")
        print("Start it with:\n  " + sudo_command())
        return 1
    used = st.get("used_bytes", 0) / (1 << 20)
    print(f"Recording session {st['session']} ({used:.0f} MiB of {st.get('cap_bytes', 0) / (1 << 30):.0f} GiB)."
          + (f" Paused: {st['paused']}." if st.get("paused") else ""))
    print("Records: " + ", ".join(f"{k} {v}" for k, v in sorted(st.get("counts", {}).items())))
    return 0


def control_file(root: str, name: str, present: bool) -> None:
    d = os.path.join(root, "control")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, name)
    if present:
        open(path, "w").close()
    else:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def resolve_sessions(root: str, ids: list[str] | None) -> list[str]:
    from .session import find_sessions
    found = find_sessions(root)
    if ids:
        found = [p for p in found if os.path.basename(p) in ids or p in ids]
    return found


def cmd_report(args) -> int:
    from . import dataset, report
    from .session import Session
    root = args.root or data_root()
    paths = resolve_sessions(root, args.session)
    joined = [dataset.join(Session(p, root)) for p in paths]
    out = args.out or os.path.join(root, "reports", time.strftime("report-%Y%m%d-%H%M%S.html"))
    report.render(joined, out)
    print(out)
    return 0


def cmd_features(args) -> int:
    from . import dataset, features
    from .session import Session
    root = args.root or data_root()
    joined = [dataset.join(Session(p, root)) for p in resolve_sessions(root, args.session)]
    for p in features.write(joined, args.out or os.path.join(root, "features")):
        print(p)
    return 0


def cmd_synth(args) -> int:
    from . import synth
    root = args.root or os.path.join(data_root(), "synthetic")
    print(synth.build_session(root, seed=args.seed, trials_per=args.trials, free_minutes=args.free_minutes))
    return 0


def cmd_export_record(args) -> int:
    from .record_export import export
    from .session import Session
    root = args.root or data_root()
    path = os.path.join(root, "sessions", args.session)
    if not os.path.isdir(path):
        path = args.session
    export(Session(path, root), args.out)
    print(args.out)
    return 0


def cmd_probe(args) -> int:
    from . import surfaces
    found = surfaces.probe_all()
    if args.json:
        print(json.dumps([s.to_json() for s in found], indent=1))
        return 0
    if not found:
        print("No touch surfaces found.")
    for s in found:
        w, h = s.pad_mm
        size = f"{w:.0f} x {h:.0f} mm" if w and h else "size unknown"
        print(f"{s.caps.source}: {s.caps.name}\n  class {s.cls} ({'; '.join(s.reasons)}), {size}\n"
              f"  has: {', '.join(b for b, v in s.mask.items() if v)}\n"
              f"  missing: {', '.join(s.missing()) or 'nothing'}")
    return 0


def cmd_mine_litest(args) -> int:
    from . import surfaces
    doc = surfaces.write_litest(args.source)
    print(f"{len(doc['devices'])} touch devices from libinput {doc['libinput_commit']} -> {surfaces.LITEST_JSON}")
    return 0


def cmd_train(args) -> int:
    from . import train
    train.run(args.out, users=args.users, seconds=args.seconds, rounds=args.rounds, depth=args.depth, seed=args.seed,
              root=args.root if args.recorded else None)
    return 0


def cmd_export_plugin(args) -> int:
    from . import plugin, rolemodel
    for p in plugin.write(rolemodel.load(args.model), args.out, ["/usr/share/libinput", "/etc/libinput"]):
        print(p)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="touchlab", description="Luma Touch Lab")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("mark")
    s.add_argument("which", choices=sorted(MARKS))
    s.add_argument("--quiet", action="store_true")
    for name in ("status", "pause", "resume", "stop", "sync-settings", "sudo-command"):
        sub.add_parser(name)
    for name in ("report", "features"):
        s = sub.add_parser(name)
        s.add_argument("--session", action="append")
        s.add_argument("--out")
    s = sub.add_parser("synth")
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--trials", type=int, default=4)
    s.add_argument("--free-minutes", type=float, default=1.0)
    s = sub.add_parser("export-record")
    s.add_argument("session")
    s.add_argument("--out", required=True)
    s = sub.add_parser("probe", help="class every touch surface on this machine")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("mine-litest", help="refresh data/litest-surfaces.json from a libinput source tree")
    s.add_argument("source")
    s = sub.add_parser("train", help="train the contact-role model and write the plugin")
    s.add_argument("--out", required=True)
    s.add_argument("--users", type=int, default=12)
    s.add_argument("--seconds", type=float, default=20.0)
    s.add_argument("--rounds", type=int, default=40)
    s.add_argument("--depth", type=int, default=3)
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("--recorded", action="store_true", help="also use recorded active sessions under --root")
    s = sub.add_parser("export-plugin", help="write the Lua plugin and quirks for a trained model")
    s.add_argument("--model", required=True)
    s.add_argument("--out", required=True)
    for s in sub.choices.values():
        s.add_argument("--root", help="data directory (default ~/.local/share/luma-touch-lab)")
    args = p.parse_args(argv)
    root = args.root or data_root()
    if args.cmd == "mark":
        t = now_ns()                                   # the moment of the shortcut, before anything slow
        rec = LabelLog(root).mark(args.which, t)
        st = read_status(root)
        if not args.quiet:
            notify(f"Marked: {MARKS[args.which].lower()}" + ("" if st and st["running"] else " (not recording)"))
        return 0 if rec else 1
    if args.cmd == "status":
        return cmd_status(args)
    if args.cmd in ("pause", "resume"):
        control_file(root, "pause", args.cmd == "pause")
        print("Paused." if args.cmd == "pause" else "Recording.")
        return 0
    if args.cmd == "stop":
        control_file(root, "stop", True)
        print("Stopping. Start again with:\n  " + sudo_command())
        return 0
    if args.cmd == "sudo-command":
        print(sudo_command())
        return 0
    if args.cmd == "sync-settings":
        data = gsettings_touchpad()
        os.makedirs(root, exist_ok=True)
        tmp = os.path.join(root, ".settings.json")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)
        os.replace(tmp, os.path.join(root, "settings.json"))
        print(json.dumps(data["touchpad"]))
        return 0
    return {"report": cmd_report, "features": cmd_features, "synth": cmd_synth,
            "export-record": cmd_export_record, "probe": cmd_probe, "mine-litest": cmd_mine_litest,
            "train": cmd_train, "export-plugin": cmd_export_plugin}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
