# SPDX-License-Identifier: Apache-2.0
"""The Luma remote's configuration: both image layouts, and set_remote_url's edge cases."""

import stat
import unittest

import fakes
from luma_update import preview

CREDENTIAL = "B" * 32
PUBLIC = "https://dl.simplyluma.com/os/repo"
PREVIEW = f"https://dl.simplyluma.com/os/preview/{CREDENTIAL}/repo"

try:
    import gi
    gi.require_version("GLib", "2.0")
    from gi.repository import GLib
except (ImportError, ValueError):  # no GObject introspection here
    GLib = None


class Layouts(unittest.TestCase):
    def setUp(self):
        self.rig = fakes.Rig()
        self.paths, self.settings = self.rig.paths, self.rig.settings
        self.remotes = self.paths.ostree_remotes_dir
        self.remotes.mkdir(parents=True)
        self.conf = self.remotes / "luma.conf"

    def tearDown(self):
        self.rig.close()

    def mode(self, path):
        return stat.S_IMODE(path.stat().st_mode)

    def test_mirrorlist_layout_changes_only_the_root_only_list(self):
        image = ('# Luma OS\n[remote "luma"]\nurl=mirrorlist=file://%s\ngpg-verify=true\n'
                 'gpg-verify-summary=true\ncollection-id=org.projectluma.OS\n' % self.paths.update_mirrorlist)
        self.conf.write_text(image)
        self.conf.chmod(0o644)
        self.paths.update_mirrorlist.parent.mkdir(parents=True, exist_ok=True)
        self.paths.update_mirrorlist.write_text(PUBLIC + "\n")
        self.paths.update_mirrorlist.chmod(0o600)
        self.assertTrue(preview.mirrorlist_layout(self.paths, self.settings))

        preview.install_credential(self.paths, self.settings, CREDENTIAL, "beta")
        self.assertEqual(self.conf.read_text(), image)
        self.assertEqual(self.paths.update_mirrorlist.read_text(), PREVIEW + "\n")
        self.assertEqual(self.mode(self.paths.update_mirrorlist), 0o600)
        self.assertFalse(self.paths.preview_mirrorlist.exists())
        self.assertEqual(self.mode(self.paths.preview_credential), 0o600)

        preview.remove_credential(self.paths, self.settings)
        self.assertEqual(self.conf.read_text(), image)
        self.assertEqual(self.paths.update_mirrorlist.read_text(), PUBLIC + "\n")
        self.assertEqual(self.mode(self.paths.update_mirrorlist), 0o600)
        self.assertFalse(self.paths.preview_credential.exists())

    def test_url_layout_still_switches_the_url_line(self):
        image = '[remote "luma"]\nurl=%s\ngpg-verify=true\n' % PUBLIC
        self.conf.write_text(image)
        self.assertFalse(preview.mirrorlist_layout(self.paths, self.settings))
        preview.install_credential(self.paths, self.settings, CREDENTIAL, "beta")
        self.assertIn(f"url=mirrorlist=file://{self.paths.preview_mirrorlist}\n", self.conf.read_text())
        self.assertEqual(self.paths.preview_mirrorlist.read_text(), PREVIEW + "\n")
        self.assertFalse(self.paths.update_mirrorlist.exists())
        preview.remove_credential(self.paths, self.settings)
        self.assertEqual(self.conf.read_text(), image)
        self.assertFalse(self.paths.preview_mirrorlist.exists())

    def test_leaving_never_rewrites_a_mirror_list_the_agent_does_not_own(self):
        custom = '[remote "luma"]\nurl=mirrorlist=file:///etc/company/luma-mirrors\n'
        self.conf.write_text(custom)
        preview.remove_credential(self.paths, self.settings)
        self.assertEqual(self.conf.read_text(), custom)

    def test_no_luma_remote_writes_nothing(self):
        self.conf.write_text('[remote "fedora"]\nurl=https://ostree.fedoraproject.org\n')
        with self.assertRaises(preview.PreviewError):
            preview.install_credential(self.paths, self.settings, CREDENTIAL, "beta")
        self.assertFalse(self.paths.preview_credential.exists())


class SetRemoteUrl(unittest.TestCase):
    def setUp(self):
        self.rig = fakes.Rig()
        self.paths, self.settings = self.rig.paths, self.rig.settings
        self.paths.ostree_remotes_dir.mkdir(parents=True)
        self.conf = self.paths.ostree_remotes_dir / "luma.conf"

    def tearDown(self):
        self.rig.close()

    def set(self, text, url="mirrorlist=file:///etc/luma/update-preview-mirrorlist"):
        self.conf.write_bytes(text.encode())
        preview.set_remote_url(self.paths, self.settings, url)
        return self.conf.read_bytes().decode()

    def keyfile(self, text):
        keyfile = GLib.KeyFile.new()
        keyfile.load_from_data(text, len(text.encode()), GLib.KeyFileFlags.NONE)
        return keyfile

    def test_contenturl_and_other_keys_are_kept(self):
        out = self.set('[remote "luma"]\nurl=https://a.example/repo\ncontenturl=mirrorlist=https://b.example/list\n'
                       'gpg-verify=true\n')
        self.assertEqual(out, '[remote "luma"]\nurl=mirrorlist=file:///etc/luma/update-preview-mirrorlist\n'
                              'contenturl=mirrorlist=https://b.example/list\ngpg-verify=true\n')

    def test_crlf_file_keeps_its_line_endings(self):
        out = self.set('[remote "luma"]\r\nurl=https://a.example/repo\r\ngpg-verify=true\r\n')
        self.assertEqual(out, '[remote "luma"]\r\nurl=mirrorlist=file:///etc/luma/update-preview-mirrorlist\r\n'
                              'gpg-verify=true\r\n')

    def test_control_characters_in_the_value_are_refused(self):
        original = '[remote "luma"]\nurl=https://a.example/repo\n'
        for bad in ("https://x\r[remote \"evil\"]\rurl=https://evil", "https://x\nurl=https://evil",
                    "https://x\tgpg-verify=false", "https://x\x00", ""):
            with self.assertRaises(preview.PreviewError, msg=repr(bad)):
                self.set(original, bad)
            self.assertEqual(self.conf.read_text(), original)

    def test_repeated_luma_groups_end_with_one_url_and_other_remotes_untouched(self):
        text = ('[remote "fedora"]\nurl=https://fedora.example/repo\n'
                '[remote "luma"]\nurl=https://a.example/repo\ngpg-verify=true\n'
                '[remote "luma"]\ncollection-id=org.projectluma.OS\nurl=https://b.example/repo\n')
        out = self.set(text)
        self.assertEqual(out.count("url="), 2)
        self.assertIn("url=https://fedora.example/repo\n", out)
        self.assertEqual(preview.remote_url(self.paths, self.settings),
                         "mirrorlist=file:///etc/luma/update-preview-mirrorlist")
        if GLib is not None:
            self.assertEqual(self.keyfile(out).get_string('remote "luma"', "url"),
                             "mirrorlist=file:///etc/luma/update-preview-mirrorlist")

    def test_remote_url_reads_the_last_value_as_libostree_does(self):
        self.conf.write_text('[remote "luma"]\nurl=https://a.example/repo\n[remote "luma"]\nurl=https://b.example/repo\n')
        self.assertEqual(preview.remote_url(self.paths, self.settings), "https://b.example/repo")

    def test_a_group_without_url_gets_one_inside_it(self):
        out = self.set('[remote "luma"]\ngpg-verify=true\n\n[remote "other"]\nurl=https://o.example\n')
        self.assertEqual(out, '[remote "luma"]\ngpg-verify=true\n\nurl=mirrorlist=file:///etc/luma/update-preview-mirrorlist\n'
                              '[remote "other"]\nurl=https://o.example\n')
        if GLib is not None:
            keyfile = self.keyfile(out)
            self.assertEqual(keyfile.get_string('remote "other"', "url"), "https://o.example")
            self.assertEqual(keyfile.get_string('remote "luma"', "url"),
                             "mirrorlist=file:///etc/luma/update-preview-mirrorlist")


if __name__ == "__main__":
    unittest.main()
