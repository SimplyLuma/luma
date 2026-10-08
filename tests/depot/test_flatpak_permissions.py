# SPDX-License-Identifier: Apache-2.0
"""Unit tests for scripts/depot/flatpak-permissions.py (ADR-028 section 7)."""

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "flatpak_permissions", ROOT / "scripts/depot/flatpak-permissions.py")
perms = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(perms)


def compute(text):
    return perms.compute(perms.read_metadata_text(text))


def keyed(entries):
    return {entry["key"]: entry for entry in entries}


HEADER = "[Application]\nname=org.example.App\nruntime=org.projectluma.Platform/x86_64/44\n"


class ComputeTests(unittest.TestCase):
    def test_minimal_portal_app_only_opens_chosen_files(self):
        result = compute(HEADER + "\n[Context]\nshared=ipc;\nsockets=wayland;fallback-x11;\ndevices=dri;\n")
        self.assertEqual(result, [{"key": "files.portal", "level": "standard"}])

    def test_network(self):
        result = keyed(compute(HEADER + "[Context]\nshared=network;ipc;\n"))
        self.assertEqual(result["network"]["level"], "standard")

    def test_x11_without_fallback_is_high(self):
        result = keyed(compute(HEADER + "[Context]\nsockets=x11;\n"))
        self.assertEqual(result["display.x11"]["level"], "high")

    def test_fallback_x11_is_not_shown(self):
        result = keyed(compute(HEADER + "[Context]\nsockets=x11;fallback-x11;wayland;\n"))
        self.assertNotIn("display.x11", result)

    def test_xdg_folders_read_only_and_read_write(self):
        result = keyed(compute(HEADER + "[Context]\nfilesystems=xdg-documents:ro;xdg-pictures;xdg-music:create;\n"))
        self.assertEqual(result["files.documents"], {"key": "files.documents", "level": "sensitive", "access": "read", "names": ["xdg-documents:ro"]})
        self.assertEqual(result["files.pictures"]["access"], "read-write")
        self.assertEqual(result["files.music"]["access"], "read-write")
        self.assertIn("files.portal", result)

    def test_xdg_subfolder_maps_to_the_folder(self):
        result = keyed(compute(HEADER + "[Context]\nfilesystems=xdg-download/Torrents;\n"))
        self.assertIn("files.downloads", result)

    def test_read_write_wins_over_read(self):
        result = keyed(compute(HEADER + "[Context]\nfilesystems=xdg-videos:ro;xdg-videos;\n"))
        self.assertEqual(result["files.videos"]["access"], "read-write")

    def test_home_and_host_are_high_and_drop_portal_baseline(self):
        result = keyed(compute(HEADER + "[Context]\nfilesystems=home;\n"))
        self.assertEqual(result["files.home"]["level"], "high")
        self.assertNotIn("files.portal", result)
        for host in ("host", "host-os", "host-etc", "/"):
            result = keyed(compute(HEADER + f"[Context]\nfilesystems={host}:ro;\n"))
            self.assertEqual(result["files.host"], {"key": "files.host", "level": "high", "access": "read", "names": [host + ":ro"]})

    def test_negated_filesystem_is_not_a_grant(self):
        result = keyed(compute(HEADER + "[Context]\nfilesystems=!home;!host;\n"))
        self.assertNotIn("files.home", result)
        self.assertNotIn("files.host", result)

    def test_other_paths_are_named(self):
        result = keyed(compute(HEADER + "[Context]\nfilesystems=~/.config/foo:ro;xdg-config/kdeglobals:ro;\n"))
        self.assertEqual(result["files.other"]["names"], ["xdg-config/kdeglobals:ro", "~/.config/foo:ro"])
        self.assertEqual(result["files.other"]["access"], "read")

    def test_removable_media(self):
        result = keyed(compute(HEADER + "[Context]\nfilesystems=/run/media;/media:ro;\n"))
        self.assertIn("files.removable", result)

    def test_audio(self):
        self.assertIn("devices.microphone", keyed(compute(HEADER + "[Context]\nsockets=pulseaudio;\n")))
        self.assertIn("devices.microphone", keyed(compute(HEADER + "[Context]\nfilesystems=xdg-run/pipewire-0;\n")))

    def test_devices_all_is_high_and_implies_camera(self):
        result = keyed(compute(HEADER + "[Context]\ndevices=all;\n"))
        self.assertEqual(result["devices.all"]["level"], "high")
        self.assertEqual(result["devices.camera"]["level"], "sensitive")

    def test_whole_session_bus_is_high(self):
        result = keyed(compute(HEADER + "[Context]\nsockets=session-bus;\n"))
        self.assertEqual(result["session.bus"], {"key": "session.bus", "level": "high", "names": ["*"]})

    def test_named_session_bus_is_sensitive_with_names(self):
        result = keyed(compute(HEADER + "[Session Bus Policy]\norg.freedesktop.secrets=talk\nca.desrt.dconf=talk\n"))
        self.assertEqual(result["session.bus"]["level"], "sensitive")
        self.assertEqual(result["session.bus"]["names"], ["ca.desrt.dconf", "org.freedesktop.secrets"])

    def test_notifications_portals_and_own_names(self):
        result = keyed(compute(HEADER + "[Session Bus Policy]\n"
                               "org.freedesktop.Notifications=talk\n"
                               "org.freedesktop.portal.Desktop=talk\n"
                               "org.example.App.Helper=own\n"
                               "org.mpris.MediaPlayer2.App=own\n"
                               "org.gtk.vfs.*=see\n"))
        self.assertEqual(set(result), {"notifications", "files.portal"})

    def test_flatpak_talk_is_sandbox_escape(self):
        result = keyed(compute(HEADER + "[Session Bus Policy]\norg.freedesktop.Flatpak=talk\n"))
        self.assertEqual(result["sandbox.escape"]["level"], "high")

    def test_system_bus(self):
        result = keyed(compute(HEADER + "[System Bus Policy]\norg.freedesktop.UPower=talk\norg.freedesktop.GeoClue2=talk\n"))
        self.assertEqual(result["system.bus"], {"key": "system.bus", "level": "high", "names": ["org.freedesktop.UPower"]})
        self.assertEqual(result["location"]["level"], "sensitive")
        self.assertIn("system.bus", keyed(compute(HEADER + "[Context]\nsockets=system-bus;\n")))

    def test_other_sockets_and_features(self):
        result = keyed(compute(HEADER + "[Context]\nsockets=ssh-auth;gpg-agent;pcsc;cups;\n"
                               "features=devel;bluetooth;multiarch;\ndevices=input;usb;kvm;\n"))
        for key, level in {"system.ssh-agent": "high", "system.gpg-agent": "high",
                           "devices.smartcard": "sensitive", "printing": "standard",
                           "sandbox.devel": "high", "devices.bluetooth": "sensitive",
                           "devices.input": "sensitive", "devices.usb": "sensitive",
                           "devices.kvm": "high"}.items():
            self.assertEqual(result[key]["level"], level, key)

    def test_output_is_ordered_by_vocabulary(self):
        result = compute(HEADER + "[Context]\nsockets=x11;\nshared=network;\nfilesystems=xdg-documents;\n")
        self.assertEqual([e["key"] for e in result],
                         ["network", "files.portal", "files.documents", "display.x11"])

    def test_every_emitted_key_is_in_the_vocabulary(self):
        for entry in compute(HEADER + "[Context]\nsockets=x11;pulseaudio;session-bus;\nfilesystems=host;home;\n"):
            self.assertIn(entry["key"], perms.VOCABULARY)


class ChangeTests(unittest.TestCase):
    def test_added_widened_removed(self):
        old = compute(HEADER + "[Context]\nfilesystems=xdg-documents:ro;\nsockets=pulseaudio;\n"
                      "[Session Bus Policy]\norg.freedesktop.secrets=talk\n")
        new = compute(HEADER + "[Context]\nfilesystems=xdg-documents;\nshared=network;\n"
                      "[Session Bus Policy]\norg.freedesktop.secrets=talk\nca.desrt.dconf=talk\n")
        change_list = perms.changes(old, new)
        by_key = {c["key"]: c for c in change_list}
        self.assertEqual(by_key["network"]["change"], "added")
        self.assertEqual(by_key["files.documents"]["change"], "widened")
        self.assertEqual(by_key["files.documents"]["access"], "read-write")
        self.assertEqual(by_key["session.bus"]["names"], ["ca.desrt.dconf"])
        self.assertEqual(by_key["devices.microphone"]["change"], "removed")
        self.assertTrue(perms.grew(change_list))

    def test_narrowing_only_does_not_grow(self):
        old = compute(HEADER + "[Context]\nfilesystems=home;\n")
        new = compute(HEADER + "[Context]\nfilesystems=xdg-documents;\n")
        change_list = perms.changes(old, new)
        # files.documents is new but the home grant it replaces was broader;
        # it still counts as an added key and is reviewed, which is the safe side.
        self.assertTrue(perms.grew(change_list))
        self.assertIn({"key": "files.home", "change": "removed", "level": "high"}, change_list)

    def test_identical_release_has_no_changes(self):
        text = HEADER + "[Context]\nshared=network;\n"
        self.assertEqual(perms.changes(compute(text), compute(text)), [])
        self.assertFalse(perms.grew([]))

    def test_cli_exit_status_and_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp, "old")
            new = Path(tmp, "new")
            old.write_text(HEADER + "[Context]\nshared=ipc;\n")
            new.write_text(HEADER + "[Context]\nshared=network;\n")
            out = io.StringIO()
            with redirect_stdout(out):
                status = perms.main([str(new), "--previous", str(old)])
            self.assertEqual(status, perms.EXIT_PERMISSIONS_GREW)
            document = json.loads(out.getvalue())
            self.assertEqual(document["permission_changes"][0]["key"], "network")
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(perms.main([str(old)]), 0)
            self.assertNotIn("permission_changes", json.loads(out.getvalue()))


if __name__ == "__main__":
    unittest.main()


class ClientScopeParity(unittest.TestCase):
    def test_publisher_and_client_hold_the_same_actual_scope_changes(self):
        from luma_installer.depot_permissions import from_metadata, diff, widens
        for before, after in (('xdg-download/project:ro', 'xdg-download:ro'),
                              ('xdg-documents/a:ro;xdg-documents/b', 'xdg-documents/a;xdg-documents/b'),
                              ('/media/drive-a:ro', '/media:ro'),
                              ('xdg-documents', 'xdg-documents/a:ro')):
            old = HEADER + '[Context]\nfilesystems=' + before + ';\n'
            new = HEADER + '[Context]\nfilesystems=' + after + ';\n'
            with self.subTest(before=before, after=after):
                published = perms.changes(compute(old), compute(new))
                client = diff(from_metadata(old, 'org.example.App'), from_metadata(new, 'org.example.App'))
                self.assertEqual({(c['key'], c['change']) for c in published}, {(c.key, c.change) for c in client})
                self.assertEqual(any(c['change'] in ('added', 'widened') and c['key'] != 'files.portal' for c in published), widens(client))
