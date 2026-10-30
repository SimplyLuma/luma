# SPDX-License-Identifier: Apache-2.0
import base64
import importlib.util
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch

HERE = Path(__file__).parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


installer = load("install")
gadget = load("gadget")
KEY = "ssh-ed25519 " + base64.b64encode(b"\0\0\0\x0bssh-ed25519\0\0\0\x20" + bytes(range(32))).decode()


class AccessTests(unittest.TestCase):
    def test_key_options_and_multiple_keys_are_not_accepted(self):
        for value in ("command=evil " + KEY, KEY + "\n" + KEY, "ssh-rsa AAAA", "ssh-ed25519 AAAA"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                installer.public_key(value)

    def test_wrong_board_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            board = Path(temporary) / "compatible"
            board.write_bytes(b"unihertz,titan2\0")
            with self.assertRaises(RuntimeError):
                gadget.check_board(board)
            board.write_bytes(b"unihertz,titan2-elite\0mediatek,mt6878\0")
            gadget.check_board(board)

    def test_controller_ownership_is_not_taken_from_another_gadget(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            foreign = root / "existing"
            foreign.mkdir()
            (foreign / "UDC").write_text("controller0\n")
            with self.assertRaises(RuntimeError):
                gadget.check_available(root, "controller0")
            self.assertEqual((foreign / "UDC").read_text(), "controller0\n")

    def test_missing_and_ambiguous_controller_are_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(RuntimeError):
                gadget.select_controller(root)
            (root / "a").mkdir()
            self.assertEqual(gadget.select_controller(root), "a")
            (root / "b").mkdir()
            with self.assertRaises(RuntimeError):
                gadget.select_controller(root)

    def test_interface_name_obeys_gether_kernel_abi_and_socket_name(self):
        self.assertEqual(gadget.interface_name(gadget.IFNAME_TEMPLATE), "titanusb0")
        for invalid in ("titanusb0", "a%d%d", "bad/%d", "x" * 15 + "%d"):
            with self.subTest(name=invalid), self.assertRaises(ValueError):
                gadget.interface_name(invalid)

    def test_install_refuses_existing_configuration_before_writing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "etc/NetworkManager/system-connections/project-titan-usb.nmconnection"
            path.parent.mkdir(parents=True)
            path.write_text("original")
            with self.assertRaises(ValueError):
                installer.install(root, KEY)
            self.assertEqual(path.read_text(), "original")
            self.assertFalse((root / "etc/project-titan").exists())

    def test_failed_write_removes_partial_payload(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(installer.os, "fsync", side_effect=OSError("interrupted write")):
            root = Path(temporary)
            with self.assertRaises(OSError):
                installer.install(root, KEY)
            self.assertFalse((root / "etc/project-titan/gadget.py").exists())
            self.assertFalse((root / "etc/project-titan/installed.json").exists())

    def test_actual_ssh_and_systemd_parsers_accept_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(root / "hostkey")], check=True)
            data = installer.payloads((root / "hostkey.pub").read_text())
            config = data["etc/project-titan/sshd_config"][0].decode().replace("/etc/ssh/ssh_host_ed25519_key", str(root / "hostkey"))
            (root / "sshd_config").write_text(config)
            subprocess.run(["/usr/bin/sshd", "-t", "-f", str(root / "sshd_config")], check=True)
            effective = subprocess.check_output(["/usr/bin/sshd", "-T", "-f", str(root / "sshd_config")], text=True)
            for policy in ("passwordauthentication no", "kbdinteractiveauthentication no",
                           "authenticationmethods publickey", "allowtcpforwarding no",
                           "allowagentforwarding no", "x11forwarding no", "permittunnel no"):
                self.assertIn(policy + "\n", effective)
            units = []
            for name, (content, _) in data.items():
                if name.endswith((".service", ".socket")):
                    path = root / Path(name).name
                    path.write_bytes(content)
                    units.append(str(path))
            subprocess.run(["systemd-analyze", "verify", *units], check=True)

    def test_udev_override_is_valid_and_limited_to_the_admin_function(self):
        data, mode = installer.payloads(KEY)["etc/udev/rules.d/99-project-titan-usb.rules"]
        rule = data.decode()
        self.assertIn(f'ATTR{{address}}=="{installer.DEVICE_MAC}"', rule)
        self.assertIn('SUBSYSTEM=="net"', rule)
        self.assertIn('ENV{NM_UNMANAGED}="0"', rule)
        self.assertNotIn('ATTR{address}=="*"', rule)
        self.assertEqual(mode, 0o644)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "99-project-titan-usb.rules"
            path.write_bytes(data)
            subprocess.run(["udevadm", "verify", str(path)], check=True)

    def test_symlink_cannot_redirect_privileged_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "etc").mkdir()
            (root / "outside").mkdir()
            (root / "etc/project-titan").symlink_to(root / "outside")
            with self.assertRaises(ValueError):
                installer.install(root, KEY)
            self.assertEqual(list((root / "outside").iterdir()), [])

    def test_install_records_removable_files_and_preserves_normal_ssh(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(installer.os, "sync"):
            root = Path(temporary)
            original = root / "etc/ssh/sshd_config"
            original.parent.mkdir(parents=True)
            original.write_text("original policy")
            manifest = installer.install(root, KEY)
            self.assertEqual(original.read_text(), "original policy")
            self.assertEqual(len(manifest["files"]), 10)
            for relative in manifest["files"]:
                self.assertTrue((root / relative).is_file())
            self.assertEqual((root / "etc/project-titan/authorized_keys").stat().st_mode & 0o777, 0o600)
            self.assertIn('from="172.16.42.2"', (root / "etc/project-titan/authorized_keys").read_text())
            socket = (root / "etc/systemd/system/project-titan-sshd.socket").read_text()
            self.assertIn("ListenStream=172.16.42.1:2222", socket)
            self.assertIn("BindToDevice=titanusb0", socket)


if __name__ == "__main__":
    unittest.main()
