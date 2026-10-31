#!/usr/bin/python3
"""Call audio for the Fold's aoc-audio card.

A cellular call's audio never crosses the AP: the CP hands the downlink to
the AOC and takes the uplink back from it. What the AP does is open the
AOC's telephony endpoint (hw:aocaudio,4), whose setup puts the AOC's DSP in
telephony mode, starts the mic into the modem's input and binds the modem
downlink to the speaker sink, and whose start sends the telephony start
command ("Voice Call Start": Modem by default); and it sets the amplifiers
for the earpiece (left amp alone, DSP bypassed, receiver gain) or the
speakers. Nothing written to the endpoint reaches the AOC; this holds it open
with silence for the life of the call.

The call state comes from ModemManager (any call dialing, ringing out,
active or held; ringing in is the ringtone's business until it is answered)
and from callaudiod's AudioMode (dialers that only talk to callaudiod), plus
the "sim" trigger used by the self-test. The speaker/earpiece choice is
callaudiod's SpeakerState. The card keeps one PipeWire profile (HiFi:
Speaker, Mic) for media; the earpiece is an amplifier setup on top of it.

  no call                  speakers (media), telephony endpoint closed
  call, speaker off        earpiece, telephony endpoint open
  call, speaker on         speakers, telephony endpoint open

Knobs (/etc/luma-fold/callaudio.conf, KEY=value; the kernel command line
luma.fold_callstart= / luma.fold_callmic= / luma.fold_calldsp= /
luma.fold_calldspafter= override):
  START=modem|voip|rtp|off   telephony start (off: open the path only)
  MIC_INPUT=0..3             modem input index of the call mic (0; 3 = INCALL)
  MIC_GAIN_CB=0..400         call mic preamp gain (130, comet stock)
  DSP_MODE=telephony|apmg3   AOC DSP mode for the call (telephony)
  DSP_AFTER=keep|previous|ambient|ambient_plus
                             DSP mode at hang-up (keep: the AOC reports heap
                             corruption on the switch back to Ambient)
  RATE=16000 CHANNELS=1      shape the endpoint is opened at

Usage: luma-fold-callaudio.py monitor | sim start [speaker] [mute] [start=X] [dsp=X] [after=X]
       | sim stop
       | amp speaker|earpiece | status
"""
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time

CARD = "aocaudio"
VOICE_PCM = "hw:%s,4" % CARD
PCM_STATUS = "/proc/asound/%s/pcm4p/sub0/status" % CARD
CAD_BUS = "org.mobian_project.CallAudio"
CAD_PATH = "/org/mobian_project/CallAudio"
MM_BUS = "org.freedesktop.ModemManager1"
MM_PATH = "/org/freedesktop/ModemManager1"
CONF = "/etc/luma-fold/callaudio.conf"
# The modem bring-up's call test masks this name at system level to keep the
# amplifiers and the telephony endpoint out of its call.
SYSTEM_MASK = "/run/systemd/system/luma-fold-callaudio.service"
RUNDIR = os.environ.get("XDG_RUNTIME_DIR") or "/run/user/%d" % os.getuid()
CTL = os.path.join(RUNDIR, "luma-fold-callaudio.ctl")
STATE = os.path.join(RUNDIR, "luma-fold-callaudio.state")

# ModemManager MMCallState
MM_STATES = {0: "unknown", 1: "dialing", 2: "ringing-out", 3: "ringing-in",
             4: "active", 5: "held", 6: "waiting", 7: "terminated"}
MM_IN_CALL = {1, 2, 4, 5}
MM_RINGING_OUT, MM_ACTIVE = 2, 4

START_NAMES = {"off": "Off", "modem": "Modem", "voip": "VoIP", "rtp": "RTP"}
DSP_NAMES = {"telephony": "Telephony", "apmg3": "Telephony_APMG3"}
AFTER_NAMES = {"keep": "Keep", "previous": "Previous", "ambient": "Ambient", "ambient_plus": "Ambient_Plus"}

# The vendor mixer paths: speakers through the protection DSP at the media
# gain (digital capped at -12 dB for bring-up); the earpiece is the left amp
# alone with the DSP bypassed at the in-call receiver gain (speaker-earpiece
# (VOICE): AMP PCM Gain 10), the right amp muted.
AMP = {
    "speaker": [("PCM Source", "DSP"), ("Analog PCM Volume", "17"),
                ("Digital PCM Volume", "721"), ("R Digital PCM Volume", "721")],
    "earpiece": [("R Digital PCM Volume", "0"), ("PCM Source", "ASP"),
                 ("Analog PCM Volume", "10"), ("Digital PCM Volume", "817")],
}
# Self-test: both amplifiers' digital volume at 0, which is mute.
MUTE = [("Digital PCM Volume", "0"), ("R Digital PCM Volume", "0")]


def log(msg):
    print("luma-callaudio: " + msg, flush=True)


def config():
    c = {"START": "modem", "MIC_INPUT": "0", "MIC_GAIN_CB": "130", "RATE": "16000", "CHANNELS": "1",
         "DSP_MODE": "telephony", "DSP_AFTER": "keep"}
    try:
        with open(CONF) as f:
            for line in f:
                m = re.match(r"\s*([A-Z_]+)\s*=\s*(\S+)", line)
                if m and m.group(1) in c:
                    c[m.group(1)] = m.group(2)
    except OSError:
        pass
    try:
        cmdline = open("/proc/cmdline").read()
    except OSError:
        cmdline = ""
    m = re.search(r"luma\.fold_callstart=(\w+)", cmdline)
    if m:
        c["START"] = m.group(1)
    m = re.search(r"luma\.fold_callmic=(\d)", cmdline)
    if m:
        c["MIC_INPUT"] = m.group(1)
    m = re.search(r"luma\.fold_calldsp=(\w+)", cmdline)
    if m:
        c["DSP_MODE"] = m.group(1)
    c["DSP_MODE"] = c["DSP_MODE"].lower()
    if c["DSP_MODE"] not in DSP_NAMES:
        log("unknown DSP_MODE %s, using telephony" % c["DSP_MODE"])
        c["DSP_MODE"] = "telephony"
    m = re.search(r"luma\.fold_calldspafter=(\w+)", cmdline)
    if m:
        c["DSP_AFTER"] = m.group(1)
    c["DSP_AFTER"] = c["DSP_AFTER"].lower()
    if c["DSP_AFTER"] not in AFTER_NAMES:
        log("unknown DSP_AFTER %s, using keep" % c["DSP_AFTER"])
        c["DSP_AFTER"] = "keep"
    c["START"] = c["START"].lower()
    if c["START"] not in START_NAMES:
        log("unknown START %s, using modem" % c["START"])
        c["START"] = "modem"
    return c


def cset(name, value):
    r = subprocess.run(["amixer", "-q", "-c", CARD, "cset", "name=%s" % name, value],
                       capture_output=True, text=True)
    if r.returncode:
        log("cset %s=%s failed: %s" % (name, value, (r.stdout + r.stderr).strip()))
    return r.returncode == 0


def cget(name):
    r = subprocess.run(["amixer", "-c", CARD, "cget", "name=%s" % name], capture_output=True, text=True)
    v = re.search(r": values=(.*)", r.stdout)
    return v.group(1) if v else "(%s)" % (r.stderr.strip() or "missing")


def amp(which, mute=False):
    ok = all([cset(n, v) for n, v in AMP[which]])
    if mute:
        ok = all([cset(n, v) for n, v in MUTE]) and ok
    log("amplifiers: %s%s%s" % (which, " (muted)" if mute else "", "" if ok else " (with errors)"))
    return ok


def pcm_status():
    try:
        with open(PCM_STATUS) as f:
            s = f.read()
    except OSError as e:
        return "unreadable (%s)" % e.strerror
    m = re.search(r"state:\s*(\S+)", s)
    return m.group(1) if m else s.strip().split("\n")[0]


class Voice:
    """The telephony endpoint, held open with silence."""

    def __init__(self):
        self.p = None
        self.error = ""

    def is_open(self):
        return self.p is not None and self.p.poll() is None

    def open(self, c):
        if self.is_open():
            return True
        # Knobs first: the kernel reads them at the endpoint's setup and start.
        for name, value in (("Voice Call DSP Mode", DSP_NAMES[c["DSP_MODE"]]),
                            ("Voice Call DSP Mode After", AFTER_NAMES[c["DSP_AFTER"]]),
                            ("Voice Call Start", START_NAMES[c["START"]]),
                            ("Voice Call Mic Input", c["MIC_INPUT"]),
                            ("Voice Call Mic HW Gain (cB)", c["MIC_GAIN_CB"])):
            cset(name, value)
        log("telephony endpoint: opening %s at %s Hz %s ch, DSP %s (after: %s), start %s, mic input %s, mic gain %s cB" %
            (VOICE_PCM, c["RATE"], c["CHANNELS"], DSP_NAMES[c["DSP_MODE"]], AFTER_NAMES[c["DSP_AFTER"]],
             START_NAMES[c["START"]],
             c["MIC_INPUT"], c["MIC_GAIN_CB"]))
        self.p = subprocess.Popen(["aplay", "-q", "-D", VOICE_PCM, "-t", "raw", "-f", "S16_LE",
                                   "-r", c["RATE"], "-c", c["CHANNELS"], "/dev/zero"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        # hw_params runs the DSP mode switch (up to 700 ms) and the mic and
        # bind commands; give it time before judging.
        for _ in range(30):
            time.sleep(0.1)
            if self.p.poll() is not None or pcm_status() == "RUNNING":
                break
        if self.p.poll() is not None:
            self.error = self.p.stderr.read().strip() or "aplay rc=%d" % self.p.returncode
            log("telephony endpoint open failed: %s" % self.error)
            self.p = None
            return False
        self.error = ""
        log("telephony endpoint open (pid %d), pcm4p %s" % (self.p.pid, pcm_status()))
        return True

    def close(self):
        if self.p is None:
            return
        self.p.terminate()
        try:
            self.p.wait(5)
        except subprocess.TimeoutExpired:
            self.p.kill()
            try:
                self.p.wait(5)
            except subprocess.TimeoutExpired:
                # stuck in the driver: say so rather than hang the daemon
                log("telephony endpoint: aplay %d does not exit (stuck in the driver?), pcm4p %s" %
                    (self.p.pid, pcm_status()))
                self.p = None
                return
        err = self.p.stderr.read().strip()
        log("telephony endpoint closed (aplay rc=%s%s), pcm4p %s" %
            (self.p.returncode, ", " + err if err else "", pcm_status()))
        self.p = None


# North American ringback: 440 + 480 Hz, 2 s on, 4 s off. The network sends no
# early media on these calls, so the handset plays its own while the far end
# rings (ModemManager ringing-out), as Android does on ALERTING. It goes to
# the media sink, which the call route has on the earpiece or the speakers.
RINGBACK_RATE = 8000
RINGBACK_CYCLES = 20


class Ringback:
    def __init__(self):
        self.p = None
        self.path = os.path.join(RUNDIR, "luma-fold-ringback.wav")
        self.unavailable = False

    def wav(self):
        if not os.path.exists(self.path):
            import math
            import struct
            import wave
            w = [2 * math.pi * f / RINGBACK_RATE for f in (440, 480)]
            on = [int(3000 * (math.sin(w[0] * i) + math.sin(w[1] * i))) for i in range(2 * RINGBACK_RATE)]
            cycle = struct.pack("<%dh" % len(on), *on) + bytes(2 * 4 * RINGBACK_RATE)
            with wave.open(self.path + ".new", "wb") as f:
                f.setnchannels(1)
                f.setsampwidth(2)
                f.setframerate(RINGBACK_RATE)
                f.writeframes(cycle * RINGBACK_CYCLES)
            os.replace(self.path + ".new", self.path)
        return self.path

    def set(self, on):
        playing = self.p is not None and self.p.poll() is None
        if on and not playing and not self.unavailable:
            for player in (["pw-play", "--media-role", "Phone"], ["paplay"]):
                try:
                    self.p = subprocess.Popen(player + [self.wav()], stdout=subprocess.DEVNULL,
                                              stderr=subprocess.DEVNULL)
                except OSError:
                    continue
                log("ringback: playing (%s)" % player[0])
                return
            log("ringback: no player (pw-play, paplay)")
            self.unavailable = True
        elif not on and self.p is not None:
            if playing:
                self.p.terminate()
                try:
                    self.p.wait(5)
                except subprocess.TimeoutExpired:
                    self.p.kill()
                log("ringback: stopped")
            self.p = None


def gdbus_call(bus, dest, path, method, *args, timeout=10):
    r = subprocess.run(["gdbus", "call", "--" + bus, "--dest", dest, "--object-path", path,
                        "--method", method] + list(args), capture_output=True, text=True, timeout=timeout)
    return r.stdout if r.returncode == 0 else ""


def mm_calls():
    """ModemManager's calls and their states, {path: state}."""
    calls = {}
    try:
        objs = gdbus_call("system", MM_BUS, MM_PATH, "org.freedesktop.DBus.ObjectManager.GetManagedObjects")
        for modem in sorted(set(re.findall(r"objectpath '(/org/freedesktop/ModemManager1/Modem/\d+)'", objs))):
            out = gdbus_call("system", MM_BUS, modem, "org.freedesktop.DBus.Properties.Get",
                             "org.freedesktop.ModemManager1.Modem.Voice", "Calls")
            for call in re.findall(r"(/org/freedesktop/ModemManager1/Call/\d+)", out):
                st = gdbus_call("system", MM_BUS, call, "org.freedesktop.DBus.Properties.Get",
                                "org.freedesktop.ModemManager1.Call", "State")
                m = re.search(r"<(-?\d+)>", st)
                calls[call] = int(m.group(1)) if m else 0
    except subprocess.SubprocessError as e:
        log("ModemManager query failed: %s" % e)
    return calls


def cad_get():
    try:
        out = gdbus_call("session", CAD_BUS, CAD_PATH, "org.freedesktop.DBus.Properties.GetAll", CAD_BUS)
    except subprocess.SubprocessError:
        out = ""
    mode = re.search(r"'AudioMode': <uint32 (\d+)>", out)
    spk = re.search(r"'SpeakerState': <uint32 (\d+)>", out)
    return (int(mode.group(1)) if mode else 0, int(spk.group(1)) if spk else 0)


def speaker_profile():
    """Make sure the card runs its speaker profile; nothing should change it."""
    try:
        objs = json.loads(subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=10).stdout)
    except (subprocess.SubprocessError, ValueError) as e:
        log("pw-dump failed: %s" % e)
        return
    for o in objs:
        info = o.get("info") or {}
        if o.get("type") != "PipeWire:Interface:Device" or \
           info.get("props", {}).get("device.name") != "alsa_card.platform-aoc-sound":
            continue
        params = info.get("params", {})
        current = (params.get("Profile") or [{}])[0].get("name")
        for p in params.get("EnumProfile", []):
            if p.get("name") == "HiFi (Mic, Speaker)" and current != p["name"]:
                r = subprocess.run(["wpctl", "set-profile", str(o["id"]), str(p["index"])],
                                   capture_output=True, text=True)
                log("profile %s -> %s: rc=%d" % (current, p["name"], r.returncode))
        log("card profile %s" % current)
        return
    log("aoc-audio card not in PipeWire yet")


def reader(cmd, tag, q):
    """Run a gdbus monitor (restarting it if it ends) and queue its lines."""
    while True:
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            for line in p.stdout:
                q.put((tag, line.strip()))
            p.wait()
        except OSError as e:
            log("%s: %s" % (tag, e))
        time.sleep(3)


def ctl_reader(q):
    try:
        try:
            os.unlink(CTL)
        except FileNotFoundError:
            pass
        os.mkfifo(CTL, 0o600)
        # O_RDWR: the FIFO never reports EOF between writers
        with os.fdopen(os.open(CTL, os.O_RDWR), "r") as f:
            for line in f:
                q.put(("ctl", line.strip()))
    except OSError as e:
        log("control FIFO %s: %s" % (CTL, e))


class Daemon:
    def __init__(self):
        self.voice = Voice()
        self.ringback = Ringback()
        self.cfg = config()
        self.calls = {}
        self.mode, self.speaker = 0, 0
        self.sim = None            # None, or {"speaker": bool, "mute": bool}
        self.applied = None        # (in_call, route, mute)
        self.masked_logged = False

    def masked(self):
        return os.path.realpath(SYSTEM_MASK) == "/dev/null"

    def write_state(self, in_call, route, mute):
        st = {"in_call": in_call, "route": route, "muted": mute, "start": self.cfg["START"],
              "dsp": self.cfg["DSP_MODE"], "dsp_after": self.cfg["DSP_AFTER"],
              "pcm": "open" if self.voice.is_open() else "closed", "pcm4p": pcm_status(),
              "error": self.voice.error, "sim": self.sim is not None,
              "mm_calls": {k.rsplit("/", 1)[1]: MM_STATES.get(v, v) for k, v in self.calls.items()},
              "callaudiod": {"mode": self.mode, "speaker": self.speaker}, "time": time.time()}
        try:
            with open(STATE + ".new", "w") as f:
                json.dump(st, f)
            os.replace(STATE + ".new", STATE)
        except OSError as e:
            log("state file: %s" % e)

    def apply(self, why):
        if self.masked():
            if not self.masked_logged:
                log("masked at system level (modem call test): leaving the amplifiers and the endpoint alone")
                self.masked_logged = True
            self.ringback.set(False)
            self.voice.close()
            return
        self.masked_logged = False
        states = set(self.calls.values())
        self.ringback.set(MM_RINGING_OUT in states and MM_ACTIVE not in states and self.sim is None)
        mm = any(s in MM_IN_CALL for s in self.calls.values())
        in_call = mm or self.mode == 1 or self.sim is not None
        speaker = bool(self.sim["speaker"]) if self.sim is not None else bool(self.speaker)
        mute = bool(self.sim and self.sim["mute"])
        route = "speaker" if (not in_call or speaker) else "earpiece"
        want = (in_call, route, mute)
        if want == self.applied and (not in_call or self.voice.is_open()):
            return
        log("%s: call %s (ModemManager %s, callaudiod mode %d speaker %d%s) -> %s" %
            (why, "on" if in_call else "off",
             ",".join(MM_STATES.get(s, str(s)) for s in self.calls.values()) or "no calls",
             self.mode, self.speaker, ", sim" if self.sim is not None else "", route))
        if in_call:
            # amplifiers before the endpoint, as the vendor HAL routes the
            # device before it opens the call's PCM
            amp(route, mute)
            if not self.voice.is_open():
                self.cfg = config()
                if self.sim is not None and self.sim.get("start"):
                    self.cfg["START"] = self.sim["start"]
                if self.sim is not None and self.sim.get("dsp"):
                    self.cfg["DSP_MODE"] = self.sim["dsp"]
                if self.sim is not None and self.sim.get("after"):
                    self.cfg["DSP_AFTER"] = self.sim["after"]
                self.voice.open(self.cfg)
        else:
            self.voice.close()
            amp("speaker")
        self.applied = want
        self.write_state(in_call, route, mute)

    def handle(self, tag, line):
        if tag == "mm":
            if "ModemManager1.Call" in line or "Modem.Voice" in line or "InterfacesAdded" in line or \
               "InterfacesRemoved" in line or "owned by" in line or "an owner" in line:
                self.calls = mm_calls()
                self.apply("modemmanager")
        elif tag == "cad":
            if "PropertiesChanged" not in line:
                return
            m = re.search(r"'AudioMode': <uint32 (\d+)>", line)
            s = re.search(r"'SpeakerState': <uint32 (\d+)>", line)
            if m:
                self.mode = int(m.group(1))
            if s:
                self.speaker = int(s.group(1))
            if m or s:
                self.apply("callaudiod")
        elif tag == "ctl":
            w = line.split()
            if w[:2] == ["sim", "start"]:
                st = [x.split("=", 1)[1].lower() for x in w if x.startswith("start=")]
                dsp = [x.split("=", 1)[1].lower() for x in w if x.startswith("dsp=")]
                self.sim = {"speaker": "speaker" in w, "mute": "mute" in w,
                            "start": st[0] if st and st[0] in START_NAMES else None,
                            "dsp": dsp[0] if dsp and dsp[0] in DSP_NAMES else None,
                            "after": next((x.split("=", 1)[1].lower() for x in w if x.startswith("after=")
                                           and x.split("=", 1)[1].lower() in AFTER_NAMES), None)}
                self.applied = None
                self.apply("sim start" + "".join(" " + x for x in w[2:]))
            elif w[:2] == ["sim", "stop"]:
                self.sim = None
                self.apply("sim stop")
            elif w[:1] == ["state"]:
                self.write_state(*(self.applied or (False, "speaker", False)))

    def run(self):
        log("monitor: start %s, mic input %s, endpoint %s" % (self.cfg["START"], self.cfg["MIC_INPUT"], VOICE_PCM))
        speaker_profile()
        q = queue.Queue()
        threading.Thread(target=reader, daemon=True, args=(
            ["gdbus", "monitor", "--system", "--dest", MM_BUS], "mm", q)).start()
        threading.Thread(target=reader, daemon=True, args=(
            ["gdbus", "monitor", "--session", "--dest", CAD_BUS, "--object-path", CAD_PATH], "cad", q)).start()
        threading.Thread(target=ctl_reader, daemon=True, args=(q,)).start()
        self.calls = mm_calls()
        self.mode, self.speaker = cad_get()
        self.apply("startup")
        try:
            while True:
                try:
                    tag, line = q.get(timeout=30)
                except queue.Empty:
                    # a missed signal must not leave a call silent or the endpoint open
                    self.calls = mm_calls()
                    self.apply("poll")
                    continue
                self.handle(tag, line)
        finally:
            self.ringback.set(False)
            self.voice.close()


def sim(args):
    msg = " ".join(["sim"] + args) + "\n"
    try:
        fd = os.open(CTL, os.O_WRONLY | os.O_NONBLOCK)
    except OSError as e:
        print("luma-callaudio: daemon not listening on %s: %s" % (CTL, e.strerror), file=sys.stderr)
        return 1
    os.write(fd, msg.encode())
    os.close(fd)
    return 0


def status():
    for name in ["PCM Source", "Analog PCM Volume", "Digital PCM Volume", "R Digital PCM Volume",
                 "Voice Call DSP Mode", "Voice Call DSP Mode After", "Voice Call Start", "Voice Call Mic Input", "Voice Call Mic HW Gain (cB)"]:
        print("%-28s %s" % (name, cget(name)))
    print("pcm4p                        %s" % pcm_status())
    try:
        print("callaudiod                   mode %d speaker %d" % cad_get())
    except subprocess.SubprocessError as e:
        print("callaudiod                   %s" % e)
    calls = mm_calls()
    print("ModemManager calls           %s" % (", ".join("%s %s" % (k.rsplit("/", 1)[1], MM_STATES.get(v, v))
                                                         for k, v in calls.items()) or "none"))
    try:
        print("daemon state                 %s" % open(STATE).read().strip())
    except OSError:
        print("daemon state                 (none)")
    return 0


if __name__ == "__main__":
    a = sys.argv[1:] or ["status"]
    if a[0] == "monitor":
        sys.exit(Daemon().run())
    if a[0] == "sim" and len(a) > 1 and a[1] in ("start", "stop"):
        sys.exit(sim(a[1:]))
    if a[0] == "amp" and len(a) > 1 and a[1] in AMP:
        sys.exit(0 if amp(a[1]) else 1)
    sys.exit(status())
