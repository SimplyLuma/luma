# SPDX-License-Identifier: Apache-2.0
"""Surface probe, drills, role features, the tree model and the Lua plugin."""
import math
import os
import random
import shutil
import tempfile
import unittest

import _path  # noqa: F401
from touchlab import drills, gbdt, luart, plugin, rfeatures, rolemodel, roles, surfaces, synth, synth_roles
from touchlab.rfeatures import NAMES, Contact, Scene

LIBINPUT_QUIRK_SENSEL = """
[Dell laptop 14 Sensel touchpad]
MatchBus=i2c
MatchVendor=0x2C2F
MatchProduct=0x0034
AttrInputProp=+INPUT_PROP_PRESSUREPAD
"""


class Surfaces(unittest.TestCase):
    def test_litest_classes(self):
        rows = {r["file"]: r for r in surfaces.survey(surfaces.load_litest())}
        self.assertGreater(len(rows), 25)
        want = {
            "litest-device-synaptics-x1-carbon-3rd.c": "legacy-clickpad",
            "litest-device-synaptics-rmi4.c": "legacy-clickpad",
            "litest-device-alps-semi-mt.c": "legacy-clickpad",
            "litest-device-atmel-hover.c": "ptp-clickpad",
            "litest-device-magic-trackpad.c": "ptp-clickpad",
            "litest-device-synaptics-pressurepad.c": "force-pad",
            "litest-device-generic-pressurepad.c": "force-pad",
            "litest-device-nexus4-touch-screen.c": "touchscreen",
            "litest-device-touch-screen.c": "touchscreen",
        }
        for f, cls in want.items():
            self.assertEqual(rows[f]["class"], cls, f)
        # Force pads report no contact size; screens and legacy pads have no firmware palm flag.
        self.assertIn("size", rows["litest-device-generic-pressurepad.c"]["missing"])
        self.assertIn("confidence", rows["litest-device-synaptics-x1-carbon-3rd.c"]["missing"])

    def test_xps_from_recording_and_quirk(self):
        desc = synth.synthetic_evdev_description(synth.hid.TouchpadLayout(
            synth.hid.parse_descriptor(synth.load_descriptor())))
        caps = surfaces.from_description(desc)
        caps.props.discard("INPUT_PROP_PRESSUREPAD")          # what the kernel alone reports
        self.assertNotEqual(surfaces.classify(caps).cls, "force-pad")
        surfaces.apply_quirks(caps, surfaces.parse_quirks(LIBINPUT_QUIRK_SENSEL))
        s = surfaces.classify(caps)
        self.assertEqual(s.cls, "force-pad")
        self.assertEqual((s.mask["force"], s.mask["size"], s.mask["confidence"]), (1, 0, 1))
        self.assertAlmostEqual(s.pad_mm[0], 149.6, delta=3)

    def test_sysfs_probe(self):
        root = tempfile.mkdtemp()
        try:
            base = os.path.join(root, "class", "input", "event9", "device")
            os.makedirs(os.path.join(base, "id"))
            os.makedirs(os.path.join(base, "capabilities"))
            def bitmap(bits):
                width = 8 * __import__("struct").calcsize("l")
                words = [0] * (max(bits) // width + 1)
                for b in bits:
                    words[b // width] |= 1 << (b % width)
                return " ".join("%x" % w for w in reversed(words))
            abs_bits = [surfaces.ABS[n] for n in ("ABS_X", "ABS_Y", "ABS_MT_SLOT", "ABS_MT_TOUCH_MAJOR",
                                                  "ABS_MT_POSITION_X", "ABS_MT_POSITION_Y", "ABS_MT_TRACKING_ID")]
            files = {"name": "Test Touchscreen", "id/bustype": "0018", "id/vendor": "1234", "id/product": "5678",
                     "properties": "2", "capabilities/abs": bitmap(abs_bits),
                     "capabilities/key": bitmap([surfaces.KEYS["BTN_TOUCH"]]), "capabilities/msc": "0",
                     "capabilities/ff": "0"}
            for k, v in files.items():
                with open(os.path.join(base, k), "w") as fh:
                    fh.write(v + "\n")
            caps = surfaces.from_sysfs("event9", root)
            self.assertIn("INPUT_PROP_DIRECT", caps.props)
            self.assertIn("ABS_MT_TOUCH_MAJOR", caps.abs)
            self.assertEqual(surfaces.classify(caps).cls, "touchscreen")
        finally:
            shutil.rmtree(root)


class Drills(unittest.TestCase):
    def test_plan_and_text(self):
        for cls in ("force-pad", "touchscreen"):
            p = drills.plan(cls)
            self.assertEqual(len(p), 12)
            self.assertTrue(8 * 60 <= drills.session_seconds(cls) <= 12 * 60)
            for d in p:
                v = d.variant(cls)
                self.assertTrue(30 <= d.seconds <= 60)
                self.assertTrue(set(v.roles) <= set(roles.ROLES))
        self.assertNotIn("click", drills.BY_ID["thumb-click"].variant("touchscreen").text.lower())
        self.assertEqual(len(drills.plan("ptp-clickpad", {"button": 0})), 10)

    def test_rules_match_gold(self):
        scenes = synth_roles.corpus(n_users=1, seconds=12, seed=5)
        ok = n = 0
        for sc in scenes:
            if not sc.contacts:
                continue
            d = drills.BY_ID[sc.contacts[0].drill]
            trs = drills.tracks_from_contacts(sc.contacts)
            lab, _why = drills.assign(d.variant(sc.surface_class).rule, trs, sc.pad_mm, sc.surface_class)
            for c, tr in zip(sc.contacts, trs):
                n += 1
                ok += lab.get(tr.key) == c.role
        self.assertGreater(ok / n, 0.95)

    def test_follow_up(self):
        st = drills.RoundStats()
        for _ in range(5):
            st.add("point", 0.9)
        st.add("point", 0.55, corrected="thumb-rest")
        st.add("point", 0.5, corrected="thumb-rest")
        d = drills.follow_up(st, "force-pad")
        self.assertIn("thumb-rest", d.variant("force-pad").roles)
        self.assertIn("point", d.variant("force-pad").roles)


def _contact(key, t0, dur, x, y, step=8.0, dx=0.0, p=0.1, maj=10.0):
    c = Contact(key, t0, t0 + dur)
    t = t0
    while t <= t0 + dur:
        u = (t - t0) / max(dur, 1)
        c.add(t, x + dx * u, y, p * (1 + 0.2 * math.sin(u * 7)), maj, maj / 1.3, 1.0, 0)
        t += step
    return c


class Features(unittest.TestCase):
    def test_causal(self):
        mask = {"pressure": 1, "force": 1, "size": 1, "minor": 1, "confidence": 1, "button": 1, "keyboard": 1}
        a = _contact("a", 0, 400, 50, 60, dx=20)
        b = _contact("b", 60, 300, 80, 20, dx=-30)
        for w in rfeatures.WINDOWS_MS:
            full = rfeatures.contact_features(a, [b], [10.0, 300.0], (150, 75), mask, w)
            cut = Contact("a", a.t_down, None)
            for i, t in enumerate(a.t):
                if t <= a.t_down + w:
                    cut.add(t, a.x[i], a.y[i], a.p[i], a.maj[i], a.mn[i], a.conf[i], a.btn[i])
            bcut = Contact("b", b.t_down, None)
            for i, t in enumerate(b.t):
                if t <= a.t_down + w:
                    bcut.add(t, b.x[i], b.y[i], b.p[i], b.maj[i], b.mn[i], b.conf[i], b.btn[i])
            # The lift time is the only thing known later; drop it from the comparison.
            part = rfeatures.contact_features(cut, [bcut] if bcut.t else [], [10.0], (150, 75), mask, w)
            for k, (x, y) in enumerate(zip(full, part)):
                if NAMES[k] in ("lifted", "elapsed_ms", "keys_window", "others_now", "others_landed"):
                    continue
                self.assertTrue((x != x and y != y) or abs(x - y) < 1e-9, (w, NAMES[k], x, y))

    def test_mask(self):
        a = _contact("a", 0, 300, 50, 60)
        row = rfeatures.contact_features(a, [], [], (150, 75), {"pressure": 0, "size": 0, "keyboard": 0}, 100)
        f = dict(zip(NAMES, row))
        for k in rfeatures.MASKED["pressure"] + rfeatures.MASKED["size"] + rfeatures.MASKED["keyboard"]:
            self.assertTrue(math.isnan(f[k]), k)
        self.assertEqual(f["m_pressure"], 0)


class Trees(unittest.TestCase):
    def test_learns_with_missing(self):
        rng = random.Random(3)
        X, y = [], []
        for _ in range(600):
            a, b = rng.random(), rng.random()
            miss = rng.random() < 0.3
            X.append([a, float("nan") if miss else b])
            y.append(2 if miss else int((a > 0.5) != (b > 0.5)))
        m = gbdt.fit(X, y, ["a", "b", "c"], ["f0", "f1"], rounds=30, depth=3, colsample=1.0, subsample=1.0)
        acc = sum(max(range(3), key=lambda k: m.proba(x)[k]) == t for x, t in zip(X, y)) / len(y)
        self.assertGreater(acc, 0.9)
        m2 = gbdt.Model.from_json(m.to_json())
        self.assertEqual(m.proba(X[0]), m2.proba(X[0]))
        more = gbdt.fit(X[:100], y[:100], ["a", "b", "c"], ["f0", "f1"], rounds=2, init=m)
        self.assertEqual(len(more.trees), len(m.trees) + 6)


def _y_model():
    """Stub models: a contact landing in the bottom 20% is a resting thumb, anything else points."""
    out = {}
    for stage, names in (("stage1", rfeatures.STAGE1), ("stage2", NAMES)):
        m = gbdt.Model(list(roles.ROLES), names)
        for k, val in ((0, -3.0), (2, 3.0)):
            t = gbdt.Tree(k)
            r, lo, hi = t.node(), t.node(), t.node()
            t.feat[r], t.thr[r], t.left[r], t.right[r], t.miss_left[r] = names.index("y_rel"), 0.8, lo, hi, 1
            t.value[lo], t.value[hi] = -val, val
            m.trees.append(t)
        m.meta["synthetic"] = True
        out[stage] = m
    return out


EVDEV = {"ABS_MT_SLOT": 0x2F, "ABS_MT_TOUCH_MAJOR": 0x30, "ABS_MT_TOUCH_MINOR": 0x31, "ABS_MT_POSITION_X": 0x35,
         "ABS_MT_POSITION_Y": 0x36, "ABS_MT_TOOL_TYPE": 0x37, "ABS_MT_TRACKING_ID": 0x39, "ABS_MT_PRESSURE": 0x3A}
HARNESS = """
evdev = {SYN_REPORT = 0, BTN_LEFT = (1 << 16) | 0x110}
for k, v in pairs(%(abs)s) do evdev[k] = (3 << 16) | v end
local handlers, frame_fn, appended = {}, nil, {}
libinput = {register = function() end, log_info = function() end, now = function() return 0 end,
            timer_set_absolute = function() end,
            connect = function(_, name, fn) handlers[name] = fn end}
local device = {
  udev_properties = function() return {ID_INPUT_TOUCHPAD = "1", ID_INTEGRATION = "internal"} end,
  absinfos = function()
    return {[evdev.ABS_MT_POSITION_X] = {minimum = 0, maximum = 4723, resolution = 32},
            [evdev.ABS_MT_POSITION_Y] = {minimum = 0, maximum = 2298, resolution = 31},
            [evdev.ABS_MT_PRESSURE] = {minimum = 0, maximum = 32767, resolution = 0},
            [evdev.ABS_MT_TOOL_TYPE] = {minimum = 0, maximum = 2, resolution = 0}}
  end,
  usages = function() return {[evdev.BTN_LEFT] = true} end,
  info = function() return {bustype = 0x18, vid = 0x2c2f, pid = 0x34} end,
  name = function() return "test pad" end,
  connect = function(_, name, fn) frame_fn = fn end,
  append_frame = function(_, f) appended[#appended + 1] = f end,
  set_absinfo = function() end,
}
local chunk = assert(load(%(src)s))
chunk()
handlers["new-evdev-device"](device)
local log = {}
for _, fr in ipairs(%(frames)s) do
  local ev = {}
  for _, e in ipairs(fr[2]) do ev[#ev + 1] = {usage = (e[1] << 16) | e[2], value = e[3]} end
  ev[#ev + 1] = {usage = 0, value = 0}
  local out = frame_fn(device, ev, fr[1])
  if out then
    local slot = nil
    for _, e in ipairs(out) do
      if e.usage == evdev.ABS_MT_SLOT then slot = e.value end
      if e.usage == evdev.ABS_MT_TOOL_TYPE then log[#log + 1] = string.format("%%d:%%d@%%d", slot or -1, e.value, fr[1] // 1000) end
    end
  end
end
return table.concat(log, " ")
"""


def _frames():
    """A resting thumb (slot 0, bottom of the pad) and a pointing finger (slot 1); then a new finger
    lands in slot 0, which must not inherit the thumb's palm flag."""
    A = EVDEV
    frames = []
    for k in range(0, 2600, 8):
        ev = []
        t = k * 1000
        if k == 0:
            ev += [[3, A["ABS_MT_SLOT"], 0], [3, A["ABS_MT_TRACKING_ID"], 1], [3, A["ABS_MT_POSITION_X"], 2400],
                   [3, A["ABS_MT_POSITION_Y"], 2150], [3, A["ABS_MT_PRESSURE"], 4000]]
        if k == 304:
            ev += [[3, A["ABS_MT_SLOT"], 1], [3, A["ABS_MT_TRACKING_ID"], 2], [3, A["ABS_MT_POSITION_X"], 1500],
                   [3, A["ABS_MT_POSITION_Y"], 800]]
        if 304 < k < 1200:
            ev += [[3, A["ABS_MT_SLOT"], 1], [3, A["ABS_MT_POSITION_X"], 1500 + (k - 304) * 2]]
        if k == 1200:
            ev += [[3, A["ABS_MT_SLOT"], 1], [3, A["ABS_MT_TRACKING_ID"], -1]]
        if k == 1600:
            ev += [[3, A["ABS_MT_SLOT"], 0], [3, A["ABS_MT_TRACKING_ID"], -1]]
        if k == 2000:
            ev += [[3, A["ABS_MT_SLOT"], 0], [3, A["ABS_MT_TRACKING_ID"], 3], [3, A["ABS_MT_POSITION_Y"], 700]]
        if 2000 < k < 2400:
            ev += [[3, A["ABS_MT_SLOT"], 0], [3, A["ABS_MT_POSITION_X"], 2400 + (k - 2000) * 2]]
        if k == 2400:
            ev += [[3, A["ABS_MT_SLOT"], 0], [3, A["ABS_MT_TRACKING_ID"], -1]]
        frames.append([t, ev])
    return frames


@unittest.skipUnless(luart.available(), "liblua 5.4 not installed")
class Lua(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenes = synth_roles.corpus(n_users=2, seconds=6, seed=9, include_xps=True)

    def test_feature_parity(self):
        chunk = plugin.features_chunk()
        cases, expect = [], []
        for sc in self.scenes[::7][:12]:
            for ci, c in enumerate(sc.contacts[:6]):
                for w in rfeatures.WINDOWS_MS:
                    others = rfeatures.scene_others(sc, ci, w)
                    rl = {o.key: [0.1, 0.2, 0.3, 0.1, 0.2, 0.1] for o in others[::2]}
                    expect.append(rfeatures.contact_features(c, others, sc.keys, sc.pad_mm, sc.mask, w, rl))
                    cases.append((c, others, sc, w, rl))

        def ctab(c):
            return ("{key=%s,t_down=%r,t_up=%s,t=%s,x=%s,y=%s,p=%s,maj=%s,mn=%s,conf=%s,btn=%s}" % (
                luart.table(c.key), c.t_down, luart.table(c.t_up), luart.table(c.t), luart.table(c.x),
                luart.table(c.y), luart.table(c.p), luart.table(c.maj), luart.table(c.mn), luart.table(c.conf),
                luart.table(c.btn)))
        body = []
        for c, others, sc, w, rl in cases:
            mask = "{" + ",".join(f"{k}={int(bool(v))}" for k, v in sc.mask.items()) + "}"
            roles_t = "{" + ",".join(f"[{luart.table(k)}]={luart.table(v)}" for k, v in rl.items()) + "}"
            body.append("emit(contact_features(%s, {%s}, %s, %r, %r, %s, %r, %s))" % (
                ctab(c), ",".join(ctab(o) for o in others), luart.table(sc.keys), sc.pad_mm[0], sc.pad_mm[1],
                mask, float(w), roles_t))
        src = chunk + "\nlocal out = {}\nlocal function emit(r) for i = 1, #r do out[#out + 1] = " \
                      "string.format('%.17g', r[i]) end end\n" + "\n".join(body) + "\nreturn table.concat(out, ' ')\n"
        got = [float(v.replace("-nan", "nan")) for v in luart.run(src).split()]
        flat = [v for r in expect for v in r]
        self.assertEqual(len(got), len(flat))
        for i, (a, b) in enumerate(zip(flat, got)):
            same = (a != a and b != b) or abs(a - b) <= 1e-9 * max(1.0, abs(a))
            self.assertTrue(same, (NAMES[i % len(NAMES)], a, b))

    def test_model_parity(self):
        tr, _ = rolemodel.split_users(self.scenes, 0.5)
        models = rolemodel.train(tr, {"rounds": 4})
        rows = rolemodel.stage1_rows(self.scenes[:6])
        X = [rolemodel.select(r, rfeatures.STAGE1) for r in rows.X[:60]]
        src = gbdt.to_lua(models["stage1"], "M") + gbdt.LUA_EVAL + "\nlocal out = {}\n"
        src += "\n".join("for _, v in ipairs(softmax(model_raw(M, %s))) do out[#out+1] = string.format('%%.17g', v) end"
                         % luart.table(x) for x in X)
        src += "\nreturn table.concat(out, ' ')\n"
        got = [float(v) for v in luart.run(src).split()]
        want = [v for x in X for v in models["stage1"].proba(x)]
        for a, b in zip(want, got):
            self.assertAlmostEqual(a, b, places=12)

    def test_plugin_marks_resting_thumb(self):
        src, quirks = plugin.build(_y_model(), [])
        harness = HARNESS % {"abs": "{" + ",".join(f"{k}={v}" for k, v in EVDEV.items()) + "}",
                             "src": "[====[" + src + "]====]", "frames": luart.table(_frames())}
        log = luart.run(harness).split()
        palm = [e for e in log if ":2@" in e]
        self.assertTrue(palm and palm[0].startswith("0:2@"), log)
        self.assertLessEqual(int(palm[0].split("@")[1]), 40)          # the first frame after the 25 ms window
        self.assertFalse([e for e in log if e.startswith("1:2@")], log)  # the pointing finger is never held
        # The new finger in slot 0 starts as a finger again.
        self.assertTrue([e for e in log if e.startswith("0:0@") and int(e.split("@")[1]) >= 2000], log)


class EvdevAndLive(unittest.TestCase):
    def test_evdev_tracks_and_live_roles(self):
        from touchlab import contacts, live_roles
        absinfo = {str(EVDEV["ABS_MT_POSITION_X"]): {"minimum": 0, "maximum": 4723, "resolution": 32},
                   str(EVDEV["ABS_MT_POSITION_Y"]): {"minimum": 0, "maximum": 2298, "resolution": 31},
                   str(EVDEV["ABS_MT_PRESSURE"]): {"minimum": 0, "maximum": 32767, "resolution": 0}}
        eb = contacts.EvdevTrackBuilder(absinfo)
        for t_us, ev in _frames():
            eb.feed_evdev(t_us * 1000, ev)
        eb.finish(2_600_000_000)
        self.assertEqual(len(eb.tracks), 3)
        thumb = eb.tracks[0]
        self.assertAlmostEqual(thumb.samples[0].y, 2150 / 31, places=3)
        self.assertGreater(thumb.samples[0].force, 0.1)
        live = live_roles.LiveRoles(_y_model(), eb.pad_mm, {"pressure": 1, "button": 1, "keyboard": 1})
        live.update(eb.tracks, [], 2_600_000_000)
        self.assertEqual(live.role(thumb.key)[0], "thumb-rest")
        self.assertEqual(live.role(eb.tracks[1].key)[0], "point")


class EndToEnd(unittest.TestCase):
    def test_train_beats_baseline_and_exports(self):
        scenes = synth_roles.corpus(n_users=3, seconds=6, seed=4)
        tr, te = rolemodel.split_users(scenes, 0.34)
        models = rolemodel.train(tr, {"rounds": 8})
        ev = rolemodel.evaluate(models, te)
        a = ev["all"]["100"]
        self.assertGreater(a["vote_acc"], 0.8)
        self.assertGreater(a["track_ignore_acc"], a["baseline_track_ignore_acc"])
        out = tempfile.mkdtemp()
        try:
            path = os.path.join(out, "m.json")
            rolemodel.save(models, path)
            back = rolemodel.load(path)
            x = rolemodel.stage1_rows(te[:1]).X[0]
            self.assertEqual(back["stage1"].proba(rolemodel.select(x, rfeatures.STAGE1)),
                             models["stage1"].proba(rolemodel.select(x, rfeatures.STAGE1)))
            lua, quirks = plugin.build(models, [os.path.join(os.path.dirname(surfaces.HERE), "tests")])
            self.assertIn("trained on synthetic data only", lua)
            self.assertIn("local STAGE2", lua)
            personal = rolemodel.personalise(models, te[:4], rounds=2)
            self.assertGreater(len(personal["stage2"].trees), len(models["stage2"].trees))
        finally:
            shutil.rmtree(out)

    def test_force_pad_quirk(self):
        pads = plugin.force_pads(surfaces.parse_quirks(LIBINPUT_QUIRK_SENSEL))
        self.assertEqual(pads[0][:3], (0x18, 0x2C2F, 0x34))
        q = plugin.quirks_file(pads)
        self.assertIn("AttrInputProp=-INPUT_PROP_PRESSUREPAD", q)
        self.assertIn("AttrPressureRange=0:0", q)

    def test_plugin_keeps_no_key_codes(self):
        with open(plugin.TEMPLATE, encoding="utf-8") as fh:
            src = fh.read()
        kb = src[src.index('props.ID_INPUT_KEYBOARD == "1"'):src.index("local direct =")]
        self.assertIn("push_key(timestamp", kb)
        self.assertNotIn("e.usage &", kb)            # only the event type is looked at, never the code
        self.assertNotIn("0xffff", kb.lower())


if __name__ == "__main__":
    unittest.main()
