# SPDX-License-Identifier: MPL-2.0
"""The WirePlumber policy's pure Lua helpers, run with a plain Lua 5.4.

WirePlumber 0.5 embeds Lua 5.4; the helpers use nothing beyond the standard
library, so the system interpreter checks them against the same vectors the
Python classifier uses."""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import _paths

LUA = shutil.which("lua") or shutil.which("lua5.4")
LIB = next(p for p in (
    _paths.COMPONENT / "data" / "wireplumber" / "scripts" / "lib" / "luma-output-policy.lua",
    Path("/usr/share/wireplumber/scripts/lib/luma-output-policy.lua"),
) if p.is_file())


def lua_literal(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        # Raw UTF-8: Lua 5.4 spells \u escapes as \u{...}, unlike JSON.
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "{" + ", ".join(lua_literal(v) for v in value) + "}"
    return "{" + ", ".join(f"[{json.dumps(k)}] = {lua_literal(v)}" for k, v in value.items()) + "}"


@unittest.skipUnless(LUA, "lua is not installed")
class LuaPolicy(unittest.TestCase):
    def run_lua(self, body: str) -> str:
        script = f'package.path = {json.dumps(str(LIB.parent) + "/?.lua;")} .. package.path\n' \
                 f'local policy = require("luma-output-policy")\n{body}\n'
        with tempfile.NamedTemporaryFile("w", suffix=".lua", delete=False) as handle:
            handle.write(script)
        try:
            run = subprocess.run([LUA, handle.name], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            return run.stdout
        finally:
            Path(handle.name).unlink()

    def test_device_keys_match_python(self):
        vectors = json.loads((_paths.FIXTURES / "device-keys.json").read_text(encoding="utf-8"))
        lines = []
        for vector in vectors:
            lines.append(f"print(policy.device_key({lua_literal(vector['props'])}, "
                         f"{lua_literal(vector.get('route') or {})}))")
            lines.append(f"print(tostring(policy.is_network({lua_literal(vector['props'])})))")
        output = self.run_lua("\n".join(lines)).splitlines()
        for index, vector in enumerate(vectors):
            with self.subTest(vector["case"]):
                self.assertEqual(output[2 * index], vector["key"])
                self.assertEqual(output[2 * index + 1], "true" if vector["network"] else "false")

    def test_filter_candidates(self):
        output = self.run_lua('''
local nodes = {
  { ["node.name"] = "speaker", ["device.api"] = "alsa" },
  { ["node.name"] = "raop_sink.mac", ["node.network"] = "true" },
  { ["node.name"] = "luma_airplay.aabb", ["node.network"] = "true", ["luma.airplay.id"] = "aabb" },
  { ["node.name"] = "dac", ["device.bus"] = "usb", ["device.vendor.id"] = "0x1", ["device.product.id"] = "0x2" },
}
-- A retired "Don't ask again" list no longer filters anything.
local kept = policy.filter_candidates (nodes, {
  armed = { ["luma_airplay.aabb"] = true },
  manual_only = { ["usb:0x1:0x2"] = true },
})
for _, n in ipairs (kept) do print (n ["node.name"]) end
local kept2 = policy.filter_candidates (nodes, { armed = { ["dac"] = true } })
print ("--")
for _, n in ipairs (kept2) do print (n ["node.name"]) end
''')
        self.assertEqual(output.split(), ["speaker", "luma_airplay.aabb", "dac", "--", "speaker", "dac"])

    def test_keep_current(self):
        output = self.run_lua('''
local present = { speaker = true, dac = true }
print (policy.keep_current ("dac", 1500, { "speaker" }, present))          -- priority only: stay
print (policy.keep_current ("dac", 20500, { "speaker" }, present))         -- remembered choice: go
print (policy.keep_current ("dac", 1500, { "airplay", "speaker" }, present)) -- default left: previous
print (policy.keep_current ("dac", 1500, {}, present))                     -- nothing before: best
print (table.concat (policy.push_recent ({ "a", "b", "c" }, "b", 3), ","))
print (table.concat (policy.push_recent ({ "a", "b", "c" }, "d", 3), ","))
''')
        self.assertEqual(output.split(), ["speaker", "dac", "speaker", "dac", "b,a,c", "d,a,b"])

    def test_route_info_shapes(self):
        output = self.run_lua('''
local a = policy.route_info_table ({ 2, "port.type", "hdmi", "device.product.name", "LG" })
local b = policy.route_info_table ({ ["port.type"] = "speaker" })
print (a ["port.type"], a ["device.product.name"], b ["port.type"])
''')
        self.assertEqual(output.split(), ["hdmi", "LG", "speaker"])


if __name__ == "__main__":
    unittest.main()
