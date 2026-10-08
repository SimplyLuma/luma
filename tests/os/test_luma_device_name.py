# SPDX-License-Identifier: MPL-2.0
"""Unit tests for the image's device-name helper (no root, no network).

Run: python3 -m unittest discover -s tests/os -p 'test_*.py'
"""

import importlib.machinery
import importlib.util
import os
import pwd
import struct
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
HELPER = os.path.join(ROOT, "image", "luma-desktop", "rootfs", "usr", "libexec", "luma-os", "luma-device-name")
UNITS = os.path.join(ROOT, "image", "luma-desktop", "rootfs", "usr", "lib", "systemd", "system")

_loader = importlib.machinery.SourceFileLoader("luma_device_name", HELPER)
_spec = importlib.util.spec_from_loader("luma_device_name", _loader)
ldn = importlib.util.module_from_spec(_spec)
_loader.exec_module(ldn)

APOS = "’"
FREE = lambda _name: False  # noqa: E731

THINKPAD_X1 = {"sys_vendor": "LENOVO", "product_name": "21NUCTO1WW", "product_family": "",
               "product_version": "ThinkPad X1 2-in-1 Gen 10", "chassis_type": "31"}
LATITUDE = {"sys_vendor": "Dell Inc.", "product_name": "Latitude 5590", "product_family": "Latitude",
            "product_version": "", "chassis_type": "10"}
QEMU = {"sys_vendor": "QEMU", "product_name": "Standard PC (Q35 + ICH9, 2009)", "product_family": "",
        "product_version": "pc-q35-9.2", "chassis_type": "1"}
MACBOOK = {"sys_vendor": "Apple Inc.", "product_name": "MacBookPro16,1", "product_family": "MacBook Pro",
           "product_version": "1.0", "chassis_type": "9"}


class ModelTests(unittest.TestCase):
    def test_thinkpad_model_in_product_version(self):
        self.assertEqual(ldn.model_name(THINKPAD_X1), "ThinkPad")

    def test_dell_latitude(self):
        self.assertEqual(ldn.model_name(LATITUDE), "Latitude")
        self.assertEqual(ldn.model_name(dict(LATITUDE, product_family="")), "Latitude")

    def test_qemu_is_computer_or_its_chassis(self):
        self.assertEqual(ldn.model_name(QEMU), "Computer")
        self.assertEqual(ldn.model_name(dict(QEMU, chassis_type="3")), "Desktop")
        self.assertEqual(ldn.model_name(dict(QEMU, chassis_type="")), "Computer")

    def test_macbook(self):
        self.assertEqual(ldn.model_name(MACBOOK), "MacBook Pro")
        self.assertEqual(ldn.model_name(dict(MACBOOK, product_family="")), "MacBook Pro")
        self.assertEqual(ldn.model_name({"product_name": "MacBookAir10,1"}), "MacBook Air")
        self.assertEqual(ldn.model_name({"product_family": "MacBook"}), "MacBook")

    def test_chassis_types(self):
        self.assertEqual(ldn.model_name({"chassis_type": "10"}), "Laptop")
        self.assertEqual(ldn.model_name({"chassis_type": "30"}), "Tablet")
        self.assertEqual(ldn.model_name({"chassis_type": "2"}), "Computer")

    def test_no_false_product_lines(self):
        self.assertEqual(ldn.model_name({"product_name": "System Product Name", "chassis_type": "3"}), "Desktop")
        self.assertEqual(ldn.model_name({"product_name": "To be filled by O.E.M."}), "Computer")


class NameTests(unittest.TestCase):
    def test_full_name(self):
        self.assertEqual(ldn.derive(THINKPAD_X1, "nick", "Nick McMillan", FREE),
                         (f"Nick{APOS}s ThinkPad", "nicks-thinkpad"))

    def test_gecos_with_extra_fields(self):
        self.assertEqual(ldn.derive(LATITUDE, "shashank", "Shashank Rao,,,", FREE)[1], "shashanks-latitude")

    def test_no_gecos_uses_capitalised_username(self):
        self.assertEqual(ldn.derive(LATITUDE, "shashank", "", FREE),
                         (f"Shashank{APOS}s Latitude", "shashanks-latitude"))

    def test_non_ascii_name(self):
        pretty, static = ldn.derive(MACBOOK, "jgarcia", "José García", FREE)
        self.assertEqual(pretty, f"José{APOS}s MacBook Pro")
        self.assertEqual(static, "joses-macbook-pro")

    def test_name_without_latin_letters_uses_username(self):
        pretty, static = ldn.derive(QEMU, "lilei", "李雷", FREE)
        self.assertEqual(pretty, f"李雷{APOS}s Computer")
        self.assertEqual(static, "lileis-computer")

    def test_apostrophes(self):
        self.assertEqual(ldn.derive(THINKPAD_X1, "dangelo", "D'Angelo Russell", FREE),
                         (f"D'Angelo{APOS}s ThinkPad", "dangelos-thinkpad"))
        self.assertEqual(ldn.slugify(f"O{APOS}Brien{APOS}s XPS"), "obriens-xps")

    def test_slug_is_a_valid_hostname(self):
        long_name = "Maximilianus" * 8
        static = ldn.derive(THINKPAD_X1, "max", long_name, lambda name: not name.endswith("-3"))[1]
        self.assertLessEqual(len(static), 63)
        self.assertRegex(static, r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?-3$")

    def test_taken_names_get_a_suffix(self):
        taken = {"nicks-thinkpad", "nicks-thinkpad-2"}
        self.assertEqual(ldn.derive(THINKPAD_X1, "nick", "Nick", lambda name: name in taken)[1], "nicks-thinkpad-3")
        self.assertEqual(ldn.derive(THINKPAD_X1, "nick", "Nick", lambda name: name in {"nicks-thinkpad"})[1],
                         "nicks-thinkpad-2")

    def test_default_names(self):
        for static in ("", "fedora", "localhost", "localhost-live", "localhost.localdomain", "luma", "LUMA"):
            self.assertTrue(ldn.is_default(static, ""), static)
        self.assertFalse(ldn.is_default("luma", "Nick’s ThinkPad"))
        self.assertFalse(ldn.is_default("nicks-thinkpad", ""))
        self.assertFalse(ldn.is_default("workstation", ""))


class MdnsTests(unittest.TestCase):
    def response(self, name, rtype=1):
        header = struct.pack("!HHHHHH", 0, 0x8400, 0, 1, 0, 0)
        rdata = bytes([192, 168, 1, 9]) if rtype == 1 else bytes(16)
        return header + ldn._encode_name(name) + struct.pack("!HHIH", rtype, 0x8001, 120, len(rdata)) + rdata

    def test_answer_matches_name(self):
        self.assertTrue(ldn.answers_for(self.response("nicks-thinkpad.local"), "nicks-thinkpad.local"))
        self.assertTrue(ldn.answers_for(self.response("Nicks-ThinkPad.local", 28), "nicks-thinkpad.local"))
        self.assertFalse(ldn.answers_for(self.response("other.local"), "nicks-thinkpad.local"))
        self.assertFalse(ldn.answers_for(b"\0" * 5, "nicks-thinkpad.local"))

    def test_compressed_answer(self):
        question = ldn._encode_name("nicks-thinkpad.local")
        packet = struct.pack("!HHHHHH", 0, 0x8400, 1, 1, 0, 0) + question + struct.pack("!HH", 1, 1)
        packet += b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 120, 4) + bytes(4)
        self.assertTrue(ldn.answers_for(packet, "nicks-thinkpad.local"))


def account(uid, name="nick", gecos="Nick McMillan", shell="/bin/bash"):
    return pwd.struct_passwd((name, "x", uid, uid, gecos, "/home/" + name, shell))


class LoginTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.stamp = os.path.join(self.tmp.name, "device-name", "named")
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def run_login(self, uid, static, pretty, who=None):
        properties = {"StaticHostname": static, "PrettyHostname": pretty}
        return ldn.login(uid, stamp=self.stamp, getter=properties.__getitem__,
                         setter=lambda method, value: self.calls.append((method, value)),
                         dmi=THINKPAD_X1, taken=FREE, lookup=lambda _uid: who or account(uid))

    def test_default_name_is_replaced_once(self):
        self.run_login(1000, "luma", "")
        self.assertEqual(self.calls, [("SetStaticHostname", "nicks-thinkpad"),
                                      ("SetPrettyHostname", f"Nick{APOS}s ThinkPad")])
        self.assertTrue(os.path.exists(self.stamp))
        self.run_login(1000, "luma", "")
        self.assertEqual(len(self.calls), 2)

    def test_never_renames_a_named_machine(self):
        for static, pretty in (("workstation", ""), ("luma", "Office PC"), ("nicks-thinkpad", f"Nick{APOS}s ThinkPad")):
            self.calls.clear()
            if os.path.exists(self.stamp):
                os.remove(self.stamp)
            self.run_login(1000, static, pretty)
            self.assertEqual(self.calls, [], static)
            self.assertTrue(os.path.exists(self.stamp))

    def test_system_accounts_are_ignored(self):
        self.run_login(42, "", "", who=account(42, "gdm", "GNOME Display Manager", "/usr/sbin/nologin"))
        self.run_login(0, "", "", who=account(0, "root", "root"))
        self.run_login(1001, "", "", who=account(1001, "svc", "", "/sbin/nologin"))
        self.assertEqual(self.calls, [])
        self.assertFalse(os.path.exists(self.stamp))

    def test_hostnamed_failure_retries_next_login(self):
        def refuse(method, value):
            raise RuntimeError("no")
        with self.assertRaises(RuntimeError):
            ldn.login(1000, stamp=self.stamp, getter={"StaticHostname": "", "PrettyHostname": ""}.__getitem__,
                      setter=refuse, dmi=THINKPAD_X1, taken=FREE, lookup=lambda _uid: account(1000))
        self.assertFalse(os.path.exists(self.stamp))


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        os.makedirs(os.path.join(self.root, "etc"))

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, path, text):
        with open(os.path.join(self.root, path), "w", encoding="utf-8") as handle:
            handle.write(text)

    def read(self, path):
        with open(os.path.join(self.root, path), encoding="utf-8") as handle:
            return handle.read()

    def passwd(self, *lines):
        self.write("etc/passwd", "root:x:0:0:root:/root:/bin/bash\ngdm:x:42:42:GDM:/var/lib/gdm:/usr/sbin/nologin\n"
                   + "".join(line + "\n" for line in lines))

    def install(self, user=None):
        return ldn.install(self.root, user, dmi=THINKPAD_X1, taken=FREE)

    def test_names_a_new_install(self):
        self.passwd("nick:x:1000:1000:Nick McMillan:/home/nick:/bin/bash")
        self.write("etc/machine-info", 'CHASSIS="convertible"\nPRETTY_HOSTNAME=""\n')
        self.install()
        self.assertEqual(self.read("etc/hostname"), "nicks-thinkpad\n")
        self.assertEqual(self.read("etc/machine-info"), f'CHASSIS="convertible"\nPRETTY_HOSTNAME="Nick{APOS}s ThinkPad"\n')
        for path in ("etc/hostname", "etc/machine-info"):
            self.assertEqual(os.stat(os.path.join(self.root, path)).st_mode & 0o777, 0o644)

    def test_placeholder_is_replaced(self):
        self.passwd("nick:x:1000:1000::/home/nick:/bin/bash")
        self.write("etc/hostname", "localhost-live\n")
        self.install()
        self.assertEqual(self.read("etc/hostname"), "nicks-thinkpad\n")

    def test_name_the_install_set_is_kept(self):
        self.passwd("nick:x:1000:1000:Nick:/home/nick:/bin/bash")
        self.write("etc/hostname", "lab-7\n")
        self.install()
        self.assertEqual(self.read("etc/hostname"), "lab-7\n")
        self.assertFalse(os.path.exists(os.path.join(self.root, "etc/machine-info")))

    def test_pretty_name_the_install_set_is_kept(self):
        self.passwd("nick:x:1000:1000:Nick:/home/nick:/bin/bash")
        self.write("etc/machine-info", 'PRETTY_HOSTNAME="Studio"\n')
        self.install()
        self.assertFalse(os.path.exists(os.path.join(self.root, "etc/hostname")))

    def test_no_account_leaves_the_name_unset(self):
        self.passwd()
        self.write("etc/hostname", "localhost-live\n")
        self.install()
        self.assertFalse(os.path.exists(os.path.join(self.root, "etc/hostname")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "etc/machine-info")))

    def test_named_user_wins(self):
        self.passwd("admin:x:1000:1000:Admin:/home/admin:/bin/bash", "ana:x:1001:1001:Ana Lima:/home/ana:/bin/bash")
        self.install("ana")
        self.assertEqual(self.read("etc/hostname"), "anas-thinkpad\n")


class UnitFileTests(unittest.TestCase):
    def test_login_does_not_wait(self):
        with open(os.path.join(UNITS, "user@.service.d", "50-luma-device-name.conf"), encoding="utf-8") as handle:
            dropin = handle.read()
        self.assertIn("Wants=luma-device-name@%i.service", dropin)
        self.assertNotRegex(dropin, r"(?m)^(After|Before|Requires)=")

    def test_helper_is_executable(self):
        self.assertTrue(os.access(HELPER, os.X_OK))


if __name__ == "__main__":
    unittest.main()
