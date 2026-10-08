# SPDX-License-Identifier: Apache-2.0
import unittest

from luma_displays import model

SCALES = [1.0, 1.25, 1.5, 2.0]


def monitor(connector, product, modes, *, builtin=False, name=None, serial="S"):
    return ((connector, "VND", product, serial), modes,
            {"is-builtin": builtin, "display-name": name or product})


def mode(w, h, rate, *, current=False, preferred=False, scale=1.0, variable=False):
    mid = f"{w}x{h}@{rate:.3f}" + ("+vrr" if variable else "")
    props = {"is-current": current, "is-preferred": preferred}
    if variable:
        props["refresh-rate-mode"] = "variable"
    return (mid, w, h, rate, scale, SCALES, props)


def home_state():
    laptop = monitor("eDP-1", "Panel", [mode(1920, 1200, 60.003, current=True, preferred=True, scale=1.25),
                                        mode(1920, 1200, 60.003, variable=True), mode(1920, 1080, 60.0), mode(1280, 800, 60.0)], builtin=True)
    wide = monitor("DP-5", "Wide 34", [mode(3440, 1440, 99.982, current=True, preferred=True),
                                       mode(3440, 1440, 59.973), mode(2560, 1440, 59.95), mode(1920, 1080, 60.0)],
                   name='Example Electronics Inc 34"')
    tall = monitor("DP-2", "Tall 27", [mode(2560, 1440, 119.998, current=True, preferred=True),
                                       mode(2560, 1440, 59.95), mode(1920, 1080, 60.0)])
    logical = [(4880, 1469, 1.25, 0, False, [("eDP-1", "VND", "Panel", "S")], {}),
               (0, 0, 1.0, 1, False, [("DP-2", "VND", "Tall 27", "S")], {}),
               (1440, 1120, 1.0, 0, True, [("DP-5", "VND", "Wide 34", "S")], {})]
    return model.parse_state((4, [laptop, tall, wide], logical, {"layout-mode": 1}))


XML = """<monitors version="2">
  <configuration>
    <logicalmonitor><x>0</x><y>0</y><scale>1</scale><primary>yes</primary>
      <monitor><monitorspec><connector>DP-5</connector><vendor>VND</vendor><product>Wide 34</product><serial>S</serial></monitorspec>
      <mode><width>3440</width><height>1440</height><rate>99.982</rate></mode></monitor></logicalmonitor>
    <logicalmonitor><x>3440</x><y>0</y><scale>1.25</scale>
      <monitor><monitorspec><connector>eDP-1</connector><vendor>VND</vendor><product>Panel</product><serial>S</serial></monitorspec>
      <mode><width>1920</width><height>1200</height><rate>60.003</rate></mode></monitor></logicalmonitor>
  </configuration>
</monitors>"""


class Familiarity(unittest.TestCase):
    def test_an_arrangement_mutter_remembers_is_familiar(self):
        state = home_state()
        remembered = model.parse_monitors_xml(XML)
        self.assertEqual(len(remembered), 1)
        self.assertFalse(model.is_familiar(state, remembered), "three displays were never arranged")
        two = model.parse_state((1, [m for m in [
            monitor("eDP-1", "Panel", [mode(1920, 1200, 60.003, current=True, preferred=True)], builtin=True),
            monitor("DP-5", "Wide 34", [mode(3440, 1440, 99.982, current=True, preferred=True)])]], [], {}))
        self.assertTrue(model.is_familiar(two, remembered))

    def test_a_closed_lid_matches_the_arrangement_without_the_panel(self):
        only_external = model.parse_monitors_xml(XML.replace(
            XML[XML.index('    <logicalmonitor><x>3440'):XML.index('  </configuration>')], ""))
        self.assertTrue(model.is_familiar(model.parse_state((1, [
            monitor("eDP-1", "Panel", [mode(1920, 1200, 60.0, current=True)], builtin=True),
            monitor("DP-5", "Wide 34", [mode(3440, 1440, 99.982, current=True)])], [], {})), only_external))

    def test_broken_xml_remembers_nothing(self):
        self.assertEqual(model.parse_monitors_xml("<monitors"), [])


class Names(unittest.TestCase):
    def test_displays_get_the_names_people_use(self):
        state = home_state()
        self.assertEqual(model.short_name(state.monitor("DP-5")), "Example 34″")
        asus = model.Monitor(model.Spec("DP-2", "AUS", "VG27", "S"), (), False, 'ASUSTek COMPUTER INC 27"')
        self.assertEqual(model.short_name(asus), "ASUS 27″")
        self.assertEqual(model.join_names(["ThinkPad", "ASUS 27″", "ASUS 34″"]), "ThinkPad, ASUS 27″ and ASUS 34″")


class Layouts(unittest.TestCase):
    def test_current_layout_is_read_with_rotation_and_primary(self):
        state = home_state()
        rects = model.rects(state, state.layout)
        self.assertEqual(rects["DP-2"], (0, 0, 1440, 2560), "rotated displays are tall")
        self.assertEqual(rects["eDP-1"], (4880, 1469, 1536, 960), "scaled displays are smaller")
        self.assertTrue(next(p for p in state.layout.placements if p.connector == "DP-5").primary)
        self.assertTrue(model.connected(list(rects.values())))

    def test_two_primaries_from_mutter_become_one_it_will_accept(self):
        laptop = monitor("eDP-1", "Panel", [mode(1920, 1200, 60.003, current=True)], builtin=True)
        wide = monitor("DP-5", "Wide 34", [mode(3440, 1440, 99.982, current=True)])
        logical = [(0, 0, 1.0, 0, True, [("DP-5", "VND", "Wide 34", "S")], {}),
                   (3440, 0, 1.25, 0, True, [("eDP-1", "VND", "Panel", "S")], {})]
        state = model.parse_state((4, [laptop, wide], logical, {"layout-mode": 1}))
        self.assertEqual([p.connector for p in state.layout.placements if p.primary], ["DP-5"])
        layout = state.layout.copy()
        for placement in layout.placements:
            placement.primary = True
        self.assertEqual(sum(arguments[4] for arguments in model.apply_arguments(state, layout)), 1)

    def test_a_dragged_display_lands_against_another_and_never_floats(self):
        state = home_state()
        layout = state.layout.copy()
        model.snap(state, layout, "eDP-1", 10000, 10000)
        boxes = list(model.rects(state, layout).values())
        self.assertTrue(model.connected(boxes))
        self.assertEqual(min(b[0] for b in boxes), 0)
        self.assertEqual(min(b[1] for b in boxes), 0)

    def test_rotating_a_display_keeps_the_layout_touching(self):
        state = home_state()
        layout = state.layout.copy()
        next(p for p in layout.placements if p.connector == "DP-2").transform = 0
        model.reflow_after_resize(state, layout, "DP-2")
        self.assertTrue(model.connected(list(model.rects(state, layout).values())))

    def test_mirror_extend_and_single(self):
        state = home_state()
        mirror = model.mirror_layout(state)
        self.assertTrue(mirror.mirror)
        self.assertEqual({(state.monitor(p.connector).mode(p.mode_id).width, state.monitor(p.connector).mode(p.mode_id).height)
                          for p in mirror.placements}, {(1920, 1080)}, "mirroring uses the largest size all share")
        args = model.apply_arguments(state, mirror)
        self.assertEqual(len(args), 1)
        self.assertEqual(len(args[0][5]), 3)
        single = model.single_layout(state, "DP-5")
        self.assertEqual(model.apply_arguments(state, single), [(0, 0, 1.0, 0, True, [("DP-5", "3440x1440@99.982", {})])])
        extend = model.extend_layout(model.parse_state((1, [
            monitor("eDP-1", "Panel", [mode(1920, 1200, 60.0, current=True, preferred=True, scale=1.25)], builtin=True),
            monitor("DP-5", "Wide 34", [mode(3440, 1440, 99.982, current=True, preferred=True)])],
            [(0, 0, 1.0, 0, True, [("eDP-1", "VND", "Panel", "S"), ("DP-5", "VND", "Wide 34", "S")], {})], {})))
        self.assertFalse(extend.mirror)
        self.assertEqual([p.primary for p in extend.placements], [False, True], "the external display leads")

    def test_displays_with_no_size_in_common_cannot_mirror(self):
        state = model.parse_state((1, [
            monitor("eDP-1", "Panel", [mode(1920, 1200, 60.0, current=True)], builtin=True),
            monitor("DP-5", "Wide 34", [mode(3440, 1440, 99.982, current=True)])], [], {}))
        self.assertIsNone(model.mirror_layout(state))

    def test_refresh_and_resolution_choices_leave_out_variable_duplicates(self):
        state = home_state()
        wide = state.monitor("DP-5")
        self.assertEqual([round(m.rate) for m in model.refresh_modes(wide, wide.current_mode)], [100, 60])
        self.assertEqual([(m.width, m.height) for m in model.resolutions(wide)][0], (3440, 1440))
        laptop = state.monitor("eDP-1")
        self.assertEqual(len(model.refresh_modes(laptop, laptop.current_mode)), 1)


if __name__ == "__main__":
    unittest.main()
