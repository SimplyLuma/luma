# SPDX-License-Identifier: Apache-2.0
import os
import unittest

os.environ.setdefault("LUMA_SETUP_FIXTURE", "1")

from luma_setup import catalog  # noqa: E402
from luma_setup.model import (Setup, host_name, mismatch, slug, strength,  # noqa: E402
                              username_problem)


class Names(unittest.TestCase):
    def test_username_follows_the_first_name(self):
        s = Setup()
        s.set_name("Nick McMillan")
        self.assertEqual(s.username, "nick")
        s.set_name("Zoë Ångström")
        self.assertEqual(s.username, "zoe")

    def test_an_edited_username_stays(self):
        s = Setup()
        s.set_name("Nick")
        self.assertEqual(s.set_username("N.Mc-Millan"), "nmcmillan")
        s.set_name("Nicholas")
        self.assertEqual(s.username, "nmcmillan")

    def test_device_name_and_host(self):
        s = Setup()
        self.assertEqual(s.device_name, "Luma")
        s.set_name("Nick McMillan")
        self.assertEqual(s.device_name, "Nick’s Luma")
        self.assertEqual(host_name(s.device_name), "nicks-luma")
        s.set_device("Living Room TV")
        s.set_name("Someone Else")
        self.assertEqual(host_name(s.device_name), "living-room-tv")
        self.assertEqual(host_name("   "), "luma")
        self.assertEqual(host_name("Café's Tablet!"), "cafes-tablet")

    def test_slug(self):
        self.assertEqual(slug("José"), "jose")
        self.assertEqual(slug("O'Brien 2"), "obrien2")

    def test_reserved_and_taken(self):
        self.assertTrue(username_problem("root"))
        self.assertTrue(username_problem("luma"))
        self.assertTrue(username_problem("9lives"))
        self.assertTrue(username_problem("nick", taken=lambda n: n == "nick"))
        self.assertEqual(username_problem("nick", taken=lambda n: False), "")


class Passwords(unittest.TestCase):
    def test_strength_is_a_hint(self):
        self.assertEqual(strength("")[0], 0)
        self.assertEqual(strength("abc"), (1, "Weak. Try 8 or more characters."))
        self.assertEqual(strength("abcdefgh1")[0], 2)
        self.assertEqual(strength("abcdefgh1!")[0], 3)
        self.assertEqual(strength("Abcdefghijk1!")[1], "Strong.")

    def test_mismatch_shows_once_it_diverges(self):
        self.assertFalse(mismatch("secret", ""))
        self.assertFalse(mismatch("secret", "sec"))
        self.assertTrue(mismatch("secret", "sex"))

    def test_ready_gates(self):
        s = Setup()
        self.assertFalse(s.ready("you", taken=lambda n: False))
        s.set_name("Nick")
        s.password = "x"
        s.again = "x"
        self.assertTrue(s.ready("you", taken=lambda n: False))
        s.again = "y"
        self.assertFalse(s.ready("you", taken=lambda n: False))
        self.assertTrue(s.ready("hello"))
        self.assertFalse(s.ready("wifi"))
        s.network = "Home"
        self.assertTrue(s.ready("wifi"))


class Pages(unittest.TestCase):
    def test_wifi_only_without_a_cable_and_with_wifi(self):
        s = Setup()
        self.assertEqual(s.pages(wired=False, wifi=True), ["hello", "wifi", "you", "device"])
        self.assertEqual(s.pages(wired=True, wifi=True), ["hello", "you", "device"])
        self.assertEqual(s.pages(wired=False, wifi=False), ["hello", "you", "device"])

    def test_installer_answers_leave_pages_out(self):
        s = Setup(answered=frozenset({"hello"}))
        self.assertEqual(s.pages(wired=True, wifi=True), ["you", "device"])

    def test_payload(self):
        s = Setup()
        s.set_name("Nick McMillan")
        s.password = s.again = "pw"
        p = s.payload()
        self.assertEqual(p["username"], "nick")
        self.assertEqual(p["static_hostname"], "nicks-luma")
        self.assertEqual(p["pretty_hostname"], "Nick’s Luma")
        self.assertFalse(p["ntp"])


class Catalog(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(catalog.languages()[0].name, "English (US)")
        self.assertEqual(catalog.keyboards("en_US.UTF-8")[0].layout, "us")
        self.assertEqual(catalog.keyboards("es_ES.UTF-8")[0].layout, "es")
        self.assertEqual(catalog.greeting("es_ES.UTF-8"), "Hola.")
        self.assertEqual(catalog.greeting("xx_YY.UTF-8"), "Hello.")

    def test_zones(self):
        self.assertEqual(catalog.zone("America/Chicago").name, "Central Time")
        self.assertEqual(catalog.zone("America/Chicago").city, "Chicago")
        self.assertEqual(len(catalog.search_zones("")), 9)
        self.assertEqual(catalog.search_zones("new york")[0].id, "America/New_York")
        self.assertEqual(catalog.search_zones("zzzz"), [])


if __name__ == "__main__":
    unittest.main()
