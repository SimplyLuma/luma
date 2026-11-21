# SPDX-License-Identifier: Apache-2.0
"""The bubblewrap launcher for application trees (ADR-052): what the namespace holds."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from luma_installer import closure_launcher as launcher
from luma_installer import session_boundary

RECORD = {"application_id": "vendor-app", "id": "vendor-app", "name": "Vendor", "format": "deb"}


def pairs(arguments: list[str], flag: str) -> list[tuple[str, str]]:
    return [(arguments[i + 1], arguments[i + 2]) for i, value in enumerate(arguments) if value == flag]


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        base = Path(self.folder.name).resolve()
        self.home = base / "home/person"
        self.runtime = base / "run"
        self.checkout = base / "checkout"
        for path in (self.home / "Documents", self.runtime / "pulse", self.checkout / "usr/share/zoneinfo",
                     self.checkout / "usr/bin", base / "data", base / "instance/tmp"):
            path.mkdir(parents=True)
        for name in ("wayland-0", "pipewire-0", "pipewire-0-application", "pulse/native", "keyring-ssh", "accessibility.a11y"):
            (self.runtime / name).touch()
        self.data, self.instance = base / "data", base / "instance"
        self.bus = self.runtime / "proxy.bus"
        self.bus.touch()
        patcher = patch.object(Path, "home", return_value=self.home)
        patcher.start()
        self.addCleanup(patcher.stop)

    def build(self, **overrides) -> tuple[list[str], dict[str, str]]:
        environment = {"WAYLAND_DISPLAY": "wayland-0", "LANG": "en_US.UTF-8", "XDG_CURRENT_DESKTOP": "GNOME",
                       "SSH_AUTH_SOCK": "/run/user/1000/keyring/ssh", "HOME": "/elsewhere", "DISPLAY": ":0",
                       "XAUTHORITY": str(self.runtime / "xauth")}
        session = session_boundary.view(self.runtime, Path(f"/run/user/{os.getuid()}"), environment,
                                        accessibility=self.runtime / "accessibility.a11y", bus_socket=self.bus)
        options = {"data": self.data, "instance": self.instance, "session": session,
                   "level": "s0:c10,c20", "folders": [self.home / "Documents"], "environment": environment}
        record = {**RECORD, **overrides.pop("record", {})}
        options.update(overrides)
        return launcher.sandbox_arguments(self.checkout, record, **options)

    def test_no_container_engine_and_the_tree_is_the_read_only_root(self):
        arguments, _env = self.build()
        self.assertEqual(arguments[0], "bwrap")
        self.assertNotIn("podman", " ".join(arguments))
        self.assertIn((str(self.checkout), "/"), pairs(arguments, "--ro-bind"))
        self.assertNotIn((str(self.checkout), "/"), pairs(arguments, "--bind"))

    def test_nested_user_namespaces_stay_allowed(self):
        arguments, _env = self.build()
        self.assertNotIn("--disable-userns", arguments)
        self.assertNotIn("--unshare-user", arguments)
        self.assertIn("--unshare-pid", arguments)

    def test_the_private_home_sits_at_the_real_home_path_with_the_persons_folders_over_it(self):
        arguments, env = self.build()
        binds = pairs(arguments, "--bind")
        self.assertIn((str(self.data), str(self.home)), binds)
        documents = str(self.home / "Documents")
        self.assertIn((documents, documents), binds)
        self.assertLess(binds.index((str(self.data), str(self.home))), binds.index((documents, documents)))
        self.assertEqual(env["HOME"], str(self.home))
        self.assertNotIn((str(self.home), str(self.home)), binds)

    def test_the_session_reaches_the_application_through_named_sockets_only(self):
        arguments, env = self.build()
        uid = os.getuid()
        read_only = pairs(arguments, "--ro-bind")
        self.assertIn((str(self.runtime / "wayland-0"), f"/run/user/{uid}/wayland-0"), read_only)
        # The socket on which an application can only play, never PipeWire's own.
        self.assertIn((str(self.runtime / "pipewire-0-application"), f"/run/user/{uid}/pipewire-0"), read_only)
        self.assertNotIn(str(self.runtime / "pipewire-0"), [source for source, _inside in read_only])
        self.assertIn((str(self.runtime / "accessibility.a11y"), f"/run/user/{uid}/at-spi/bus"), read_only)
        self.assertIn((str(self.bus), f"/run/user/{uid}/proxy.bus"), pairs(arguments, "--bind"))
        self.assertEqual(env["DBUS_SESSION_BUS_ADDRESS"], f"unix:path=/run/user/{uid}/proxy.bus")
        # The session's X display and its cookie stay outside.
        self.assertNotIn("XAUTHORITY", env)
        self.assertNotIn("DISPLAY", env)
        self.assertNotIn("keyring-ssh", " ".join(arguments))
        self.assertEqual(env["PULSE_SERVER"], f"unix:/run/user/{uid}/pulse/native")
        self.assertNotIn((str(self.runtime), f"/run/user/{uid}"), read_only + pairs(arguments, "--bind"))
        self.assertNotIn("SSH_AUTH_SOCK", env)
        self.assertEqual(env["XDG_CURRENT_DESKTOP"], "GNOME")
        self.assertEqual(env["GTK_USE_PORTAL"], "1")

    def test_the_application_runs_as_its_own_selinux_identity(self):
        arguments, _env = self.build()
        label = arguments[arguments.index("--exec-label") + 1]
        self.assertEqual(label, "system_u:system_r:luma_application.process:s0:c10,c20")
        self.assertEqual(arguments[arguments.index("--file-label") + 1], "system_u:object_r:container_file_t:s0:c10,c20")
        self.assertLess(arguments.index("--file-label"), arguments.index("--tmpfs"))
        without, _env = self.build(level="")
        self.assertNotIn("--exec-label", without)
        self.assertNotIn("--file-label", without)

    def test_a_per_application_tmp_is_shared_between_starts(self):
        arguments, _env = self.build()
        self.assertIn((str(self.instance / "tmp"), "/tmp"), pairs(arguments, "--bind"))
        self.assertIn((str(self.instance), "/run/luma"), pairs(arguments, "--ro-bind"))

    def test_x_is_a_socket_of_the_applications_own(self):
        from luma_installer import capsule_display
        relay = capsule_display.DisplayRelay(self.runtime / "app.x11", Path("/tmp/.X11-unix/X0"), b"cookie")
        session = session_boundary.view(self.runtime, Path("/run/user/1"), {"DISPLAY": ":0"}, display=relay)
        arguments, env = self.build(session=session)
        self.assertIn((str(self.runtime / "app.x11"), "/tmp/.X11-unix/X724"), pairs(arguments, "--bind"))
        self.assertEqual(env["DISPLAY"], ":724")
        self.assertNotIn("XAUTHORITY", env)

    def test_the_hidden_start_has_no_network_and_a_moved_capsule_keeps_its_old_home_name(self):
        arguments, _env = self.build(network=False, record={"legacy_home": True})
        self.assertIn("--unshare-net", arguments)
        self.assertIn(("person", str(self.home.parent / "luma")), pairs(arguments, "--symlink"))
        plain, _env = self.build()
        self.assertNotIn("--unshare-net", plain)
        self.assertEqual(pairs(plain, "--symlink"), [])

    def test_a_console_application_keeps_its_terminal(self):
        windowed, _env = self.build()
        self.assertIn("--new-session", windowed)
        arguments, _env = self.build(record={"terminal": True})
        self.assertNotIn("--new-session", arguments)


class Payload(unittest.TestCase):
    def test_chromium_gets_wayland_and_text_input(self):
        record = {"id": "vendor-app", "exports": {"command": "/opt/vendor/app --flag", "chromium": True}}
        with patch.object(launcher.runtime_policy, "flag_exceptions", return_value=set()), \
                patch.dict("os.environ", {"LUMA_ENERGY_FLAGS": ""}):
            arguments = launcher.payload(record, True, ["https://example.org"])
        self.assertEqual(arguments[:2], ["/opt/vendor/app", "--flag"])
        self.assertIn("--ozone-platform=wayland", arguments)
        self.assertIn("--enable-wayland-ime", arguments)
        # A file to open follows a switch with a value, never a bare one an application's own parser would give it to.
        self.assertIn("=", arguments[-2])
        self.assertEqual(arguments[-1], "https://example.org")
        plain = launcher.payload({"id": "x", "exports": {"command": "mousepad"}}, True, [])
        self.assertEqual(plain, ["mousepad"])

    def test_a_tree_without_a_launcher_is_refused(self):
        with self.assertRaises(launcher.InstallerError):
            launcher.payload({"id": "x", "exports": {}}, True, [])

    def test_the_scope_is_named_for_the_application(self):
        with patch("shutil.which", return_value="/usr/bin/systemd-run"), patch.dict("os.environ", {"XDG_RUNTIME_DIR": "/run/user/1"}):
            prefix = launcher.scope_prefix("google-chrome.stable")
        self.assertIn("--scope", prefix)
        self.assertIn("--slice=app.slice", prefix)
        self.assertIn("--expand-environment=no", prefix)
        unit = next(value for value in prefix if value.startswith("--unit="))
        self.assertRegex(unit, r"^--unit=app-luma-google_chrome_stable-\d+\.scope$")


class SharedPolicy(unittest.TestCase):
    """The session rules are today's launcher's, used by import, so they change
    here when they change there. This pins only how this launcher calls them."""

    def test_the_functions_this_launcher_borrows_can_be_called_the_way_it_calls_them(self):
        import inspect
        borrowed = launcher.capsule_launcher
        self.assertEqual(list(inspect.signature(borrowed._start_bus_proxy).parameters)[:3], ["runtime", "identity", "own"])
        inspect.signature(borrowed._start_bus_proxy).bind(Path("/run/user/1"), "identity", [])
        inspect.signature(borrowed._stop_bus_proxy).bind(None, None)
        inspect.signature(borrowed._guarded).bind({}, [], {})
        inspect.signature(borrowed._person_folders).bind()
        inspect.signature(borrowed._desktop_id).bind({})
        self.assertTrue(callable(borrowed.host_time_zone))
        policy = launcher.runtime_policy
        for name in ("payload_arguments", "own_bus_arguments", "login_environment", "is_chromium", "deb_package_candidates"):
            self.assertTrue(callable(getattr(policy, name)), name)
        self.assertIn("XDG_CURRENT_DESKTOP", policy.DESKTOP_IDENTITY)

    def test_the_identity_and_the_confinement_are_the_capsules(self):
        tree = {"format": "deb", "exports": {"name": "Vendor", "bus_names": ["org.vendor.App"], "command": "vendor"}}
        described = launcher.guard_record("vendor-app", tree, {"application_id": "deb-vendor-1", "sha256": "ab" * 32,
                                                               "mcs_level": "s0:c5,c9"})
        self.assertEqual(described["application_id"], "deb-vendor-1")
        self.assertEqual(described["id"], "vendor-app")
        self.assertIn("--own=org.vendor.App", launcher.runtime_policy.own_bus_arguments(described))
        with patch.object(launcher, "selinux_enabled", return_value=True), patch.object(Path, "exists", return_value=True):
            self.assertEqual(launcher.application_level(described), "s0:c5,c9")
        with patch.object(launcher.capsule_launcher.capsule_scope, "supported", return_value=True), \
                patch.object(launcher.capsule_launcher.sandbox_seccomp, "program", return_value=b"filter"):
            front, options = launcher.capsule_launcher._confinement(described)
            self.assertEqual(front[-4:], ["--abstract", "--signal", "--seccomp", "--"])
            self.assertEqual(options, ["--seccomp", launcher.capsule_launcher.capsule_scope.SECCOMP_PLACEHOLDER])
            front, options = launcher.capsule_launcher._confinement({**described, "seccomp": False, "scope": False})
            self.assertEqual((front, options), ([], []))


class Integration(unittest.TestCase):
    def test_the_checkout_points_its_changing_files_at_the_runtime_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "root"
            for relative, text in (("etc/passwd", "root:x:0:0:root:/root:/bin/bash\n_apt:x:42:65534::/nonexistent:/usr/sbin/nologin\n"),
                                   ("etc/group", "root:x:0:\n"), ("etc/fonts/conf.d/60-latin.conf", "<fontconfig/>"),
                                   ("usr/share/luma/closure.json", "{}"), ("usr/bin/true", "")):
                (root / relative).parent.mkdir(parents=True, exist_ok=True)
                (root / relative).write_text(text)
            store_object = Path(folder) / "object"
            store_object.write_text("127.0.0.1 localhost\n")
            os.link(store_object, root / "etc/hosts")
            description = launcher.integrate(root, {"packages": []}, [])
            self.assertEqual(description["graphics"], [])
            self.assertEqual(os.readlink(root / "etc/passwd"), "/run/luma/passwd")
            self.assertEqual(os.readlink(root / "etc/localtime"), "/run/luma/localtime")
            self.assertEqual(os.readlink(root / "etc/hosts"), "/run/luma/hosts")
            self.assertEqual(store_object.read_text(), "127.0.0.1 localhost\n")
            self.assertIn("/run/host/fonts", (root / "etc/fonts/conf.d/50-luma-host-fonts.conf").read_text())
            self.assertIn("_apt", (root / "usr/share/luma/passwd.tree").read_text())
            self.assertTrue((root / "usr/bin/xdg-open").exists())
            self.assertTrue((root / "var/home").is_dir())

    def test_home_is_spelled_the_way_the_device_spells_it(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base / "root/home").mkdir(parents=True)
            (base / "root/var/home").mkdir(parents=True)
            os.symlink("var/home", base / "host-home")
            launcher.alias_home(base / "root", base / "host-home")
            self.assertEqual(os.readlink(base / "root/home"), "var/home")
            (base / "plain/home/someone").mkdir(parents=True)
            launcher.alias_home(base / "plain", base / "host-home")
            self.assertFalse((base / "plain/home").is_symlink())

    def test_the_person_is_added_to_the_trees_accounts(self):
        import pwd
        with tempfile.TemporaryDirectory() as folder:
            checkout, instance = Path(folder) / "checkout", Path(folder) / "instance"
            (checkout / "usr/share/luma").mkdir(parents=True)
            me = pwd.getpwuid(os.getuid())
            (checkout / "usr/share/luma/passwd.tree").write_text(
                f"root:x:0:0:root:/root:/bin/bash\n_apt:x:42:65534::/nonexistent:/usr/sbin/nologin\nclash:x:{me.pw_uid}:1::/x:/bin/sh\n")
            (checkout / "usr/share/luma/group.tree").write_text("root:x:0:\n")
            with patch.object(launcher.capsule_launcher, "host_time_zone", return_value="America/Chicago"):
                launcher.runtime_files(instance, checkout, "vendor-app")
            lines = (instance / "passwd").read_text().splitlines()
            self.assertIn("_apt:x:42:65534::/nonexistent:/usr/sbin/nologin", lines)
            self.assertEqual(sum(1 for line in lines if line.split(":")[2] == str(me.pw_uid)), 1)
            self.assertTrue(lines[-1].startswith(f"{me.pw_name}:x:{me.pw_uid}:"))
            self.assertEqual(os.readlink(instance / "localtime"), "/usr/share/zoneinfo/America/Chicago")
            first = (instance / "machine-id").read_text()
            self.assertRegex(first, r"^[0-9a-f]{32}\n$")
            self.assertEqual(first.strip(), launcher.application_machine_id("vendor-app"))
            self.assertNotEqual(first.strip(), launcher.application_machine_id("other-app"))
            self.assertTrue((instance / "tmp").is_dir())


if __name__ == "__main__":
    unittest.main()
