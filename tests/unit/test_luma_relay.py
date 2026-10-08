from __future__ import annotations

import json
import os
import struct
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from luma_relay.config import RelayConfig
from luma_relay.engine import WineEngine
from luma_relay.errors import RelayError
from luma_relay.native_notifications import NotificationBroker, validate_notification
from luma_relay.package import host_supports, inspect_windows_package
from luma_relay.registry import list_manifests, read_manifest, write_manifest
from luma_relay.sandbox import sandbox_command
from luma_relay.session import _create_notification_broker
from luma_relay.wine_integration import sync_relay_bridge


def write_pe(path: Path, machine: int = 0x8664, *, dotnet: bool = False) -> None:
    payload = bytearray(1024)
    payload[:2] = b"MZ"
    struct.pack_into("<I", payload, 0x3C, 0x80)
    payload[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", payload, 0x84, machine)
    struct.pack_into("<H", payload, 0x94, 240)
    struct.pack_into("<H", payload, 0x98, 0x20B)
    if dotnet:
        payload[700:711] = b"mscoree.dll"
    path.write_bytes(payload)


class PackageTests(unittest.TestCase):
    def test_pe_is_inspected_without_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "Useful Portable.exe"
            write_pe(path, dotnet=True)
            package = inspect_windows_package(path, 4096)
            self.assertEqual(package.package_format, "exe")
            self.assertEqual(package.processor, "x86_64")
            self.assertEqual(package.suggested_action, "open")
            self.assertTrue(package.dotnet_metadata_present)
            self.assertEqual(len(package.sha256), 64)

    def test_setup_name_selects_install_flow(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "Product Setup.exe"
            write_pe(path)
            package = inspect_windows_package(path, 4096)
            self.assertEqual(package.suggested_action, "install")

    def test_msi_requires_ole_container(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "Product.msi"
            path.write_bytes(bytes.fromhex("d0cf11e0a1b11ae1") + bytes(512))
            package = inspect_windows_package(path, 4096)
            self.assertEqual(package.package_format, "msi")

    def test_symlinks_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target.exe"
            link = root / "link.exe"
            write_pe(target)
            link.symlink_to(target)
            with self.assertRaises(RelayError):
                inspect_windows_package(link, 4096)

    def test_architecture_gate_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "arm.exe"
            write_pe(path, 0xAA64)
            package = inspect_windows_package(path, 4096)
            supported, explanation = host_supports(package, "x86_64")
            self.assertFalse(supported)
            self.assertIn("not available", explanation)


class RegistryTests(unittest.TestCase):
    def test_manifest_is_atomic_and_private(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            env = {"XDG_DATA_HOME": temporary}
            with mock.patch.dict(os.environ, env):
                write_manifest("sample-app-12345678", {"name": "Sample"})
                payload = read_manifest("sample-app-12345678")
                self.assertEqual(payload["name"], "Sample")
                self.assertEqual(len(list_manifests()), 1)
                mode = (Path(temporary) / "luma-relay/windows/apps/sample-app-12345678/manifest.json").stat().st_mode
                self.assertEqual(mode & 0o777, 0o600)

    def test_folder_grant_can_be_revoked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary) / "data"
            shared = Path(temporary) / "shared"
            shared.mkdir()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(data)}):
                app_id = "sample-app-12345678"
                write_manifest(app_id, {"name": "Sample", "grants": []})
                engine = WineEngine(RelayConfig())
                granted = engine.grant(app_id, shared, "read-write")
                self.assertEqual(granted["grants"][0]["drive"], "D:")
                drive = data / "luma-relay/windows/apps" / app_id / "prefix/dosdevices/d:"
                self.assertEqual(str(drive.readlink()), "/shared/1")
                revoked = engine.revoke(app_id, shared)
                self.assertEqual(revoked["grants"], [])
                self.assertFalse(drive.exists())


class SandboxTests(unittest.TestCase):
    @mock.patch("luma_relay.sandbox.shutil.which", return_value="/usr/bin/bwrap")
    def test_home_is_not_exposed(self, _which: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            command, environment = sandbox_command(root, ["/usr/bin/wine", "app.exe"])
            rendered = " ".join(command)
            self.assertNotIn(str(Path.home()), rendered)
            self.assertIn("--unshare-all", command)
            self.assertIn("--share-net", command)
            self.assertEqual(environment["WINEPREFIX"], "/relay/prefix")
            self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", environment)

    @mock.patch("luma_relay.sandbox.shutil.which", return_value="/usr/bin/bwrap")
    def test_only_private_notification_socket_is_exposed(self, _which: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            command, environment = sandbox_command(
                Path(temporary),
                ["/usr/bin/wine", "app.exe"],
                notification_socket="/relay-notify/notify.sock",
            )
            self.assertEqual(
                environment["LUMA_RELAY_NOTIFY_SOCKET"],
                "/relay-notify/notify.sock",
            )
            self.assertEqual(environment["LUMA_RELAY_NOTIFICATIONS"], "1")
            self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", environment)

    @mock.patch("luma_relay.sandbox.shutil.which", return_value="/usr/bin/bwrap")
    def test_notification_socket_mount_is_one_private_endpoint(
        self, _which: mock.Mock
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            socket_path = Path(temporary) / "notify.sock"
            socket_path.touch(mode=0o600)
            command, _environment = sandbox_command(
                Path(temporary) / "capsule",
                ["/usr/bin/wine", "app.exe"],
                notification_socket="/relay-notify/notify.sock",
                notification_socket_host=socket_path,
            )
            rendered = " ".join(command)
            self.assertIn(str(socket_path.parent), rendered)
            self.assertIn("/relay-notify", rendered)
            self.assertNotIn(str(Path.home()), rendered)

    @mock.patch("luma_relay.sandbox.shutil.which", return_value="/usr/bin/bwrap")
    def test_xauthority_is_read_only_and_limited_to_runtime(
        self, _which: mock.Mock
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            authority = runtime / ".mutter-Xwaylandauth.test"
            authority.touch(mode=0o600)
            with mock.patch.dict(
                os.environ,
                {"XDG_RUNTIME_DIR": str(runtime), "XAUTHORITY": str(authority)},
            ):
                command, environment = sandbox_command(
                    runtime / "capsule", ["/usr/bin/wine", "app.exe"]
                )
            rendered = " ".join(command)
            self.assertIn(f"--ro-bind {authority} {authority}", rendered)
            self.assertEqual(environment["XAUTHORITY"], str(authority))

    @mock.patch("luma_relay.sandbox.shutil.which", return_value="/usr/bin/bwrap")
    def test_xauthority_outside_runtime_is_not_exposed(
        self, _which: mock.Mock
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / "runtime"
            runtime.mkdir()
            authority = Path(temporary) / "outside"
            authority.touch(mode=0o600)
            with mock.patch.dict(
                os.environ,
                {"XDG_RUNTIME_DIR": str(runtime), "XAUTHORITY": str(authority)},
            ):
                command, environment = sandbox_command(
                    runtime / "capsule", ["/usr/bin/wine", "app.exe"]
                )
            self.assertNotIn(str(authority), " ".join(command))
            self.assertNotIn("XAUTHORITY", environment)

    @mock.patch("luma_relay.sandbox.Path.is_dir", return_value=True)
    @mock.patch("luma_relay.sandbox.Path.is_file", return_value=True)
    @mock.patch("luma_relay.sandbox.shutil.which", return_value="/usr/bin/bwrap")
    def test_relay_explorer_is_overlaid_only_inside_capsule(
        self, _which: mock.Mock, _is_file: mock.Mock, _is_dir: mock.Mock
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            command, _environment = sandbox_command(
                Path(temporary), ["/usr/bin/wine", "app.exe"]
            )
            rendered = " ".join(command)
            self.assertIn(
                "/usr/libexec/luma-relay/wine/x86_64-windows/explorer.exe",
                rendered,
            )
            self.assertIn(
                "/usr/lib64/wine-wow64/wine/x86_64-windows/explorer.exe",
                rendered,
            )

    @mock.patch("luma_relay.sandbox.shutil.which", return_value="/usr/bin/bwrap")
    def test_network_can_be_removed(self, _which: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            command, _environment = sandbox_command(
                Path(temporary), ["/usr/bin/wine", "app.exe"], network=False
            )
            self.assertNotIn("--share-net", command)

    @mock.patch("luma_relay.sandbox.shutil.which", return_value="/usr/bin/bwrap")
    def test_source_mount_parent_is_created_first(self, _which: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "sample.exe"
            source.write_bytes(b"MZ")
            command, _environment = sandbox_command(
                root, ["/usr/bin/wine", "/source/input"], source=source
            )
            source_dir = command.index("/source")
            source_target = command.index("/source/input")
            self.assertLess(source_dir, source_target)

    def test_trusted_bridge_replaces_capsule_copy_without_following_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            runtime = base / "runtime"
            capsule = base / "capsule"
            outside = base / "outside.dll"
            outside.write_bytes(b"outside")
            for architecture, payload in (
                ("x86_64-windows", b"trusted-64"),
                ("i386-windows", b"trusted-32"),
            ):
                directory = runtime / architecture
                directory.mkdir(parents=True)
                (directory / "luma_relay.dll").write_bytes(payload)
            for directory in ("system32", "syswow64"):
                (capsule / "prefix/drive_c/windows" / directory).mkdir(parents=True)
            destination = capsule / "prefix/drive_c/windows/system32/luma_relay.dll"
            destination.symlink_to(outside)
            with (
                mock.patch(
                    "luma_relay.wine_integration.relay_bridge_root",
                    return_value=runtime,
                ),
                mock.patch(
                    "luma_relay.wine_integration.native_notification_files_available",
                    return_value=True,
                ),
            ):
                bindings = sync_relay_bridge(capsule)
            self.assertFalse(destination.is_symlink())
            self.assertEqual(destination.read_bytes(), b"trusted-64")
            self.assertEqual(outside.read_bytes(), b"outside")
            self.assertEqual(len(bindings), 2)

    def test_trusted_bridge_refuses_symlinked_windows_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            runtime = base / "runtime"
            capsule = base / "capsule"
            outside = base / "outside"
            outside.mkdir()
            for architecture in ("x86_64-windows", "i386-windows"):
                directory = runtime / architecture
                directory.mkdir(parents=True)
                (directory / "luma_relay.dll").write_bytes(b"trusted")
            windows = capsule / "prefix/drive_c/windows"
            windows.mkdir(parents=True)
            (windows / "system32").symlink_to(outside, target_is_directory=True)
            (windows / "syswow64").mkdir()
            with (
                mock.patch(
                    "luma_relay.wine_integration.relay_bridge_root",
                    return_value=runtime,
                ),
                mock.patch(
                    "luma_relay.wine_integration.native_notification_files_available",
                    return_value=True,
                ),
            ):
                with self.assertRaises(RelayError):
                    sync_relay_bridge(capsule)

    @mock.patch("luma_relay.sandbox.Path.exists", return_value=True)
    @mock.patch("luma_relay.sandbox.shutil.which", return_value="/usr/bin/bwrap")
    def test_ntsync_device_is_available_inside_capsule(
        self, _which: mock.Mock, _exists: mock.Mock
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            command, _environment = sandbox_command(
                Path(temporary), ["/usr/bin/wine", "app.exe"]
            )
        index = command.index("/dev/ntsync")
        self.assertEqual(command[index - 1], "--dev-bind")
        self.assertEqual(command[index + 1], "/dev/ntsync")


class DoctorTests(unittest.TestCase):
    @mock.patch("luma_relay.engine.platform.machine", return_value="x86_64")
    @mock.patch("luma_relay.engine.subprocess.run")
    @mock.patch("luma_relay.engine.shutil.which")
    @mock.patch("luma_relay.engine.Path.rglob")
    @mock.patch("luma_relay.engine.Path.is_dir", return_value=True)
    def test_fedora_wine_dxvk_layout_is_detected(
        self,
        _is_dir: mock.Mock,
        rglob: mock.Mock,
        which: mock.Mock,
        run: mock.Mock,
        _machine: mock.Mock,
    ) -> None:
        which.side_effect = lambda name: f"/usr/bin/{name}" if name in {"wine", "bwrap", "winetricks"} else None
        rglob.side_effect = lambda pattern: iter(
            [Path("/usr/lib64/wine-wow64/wine/x86_64-windows/dxvk-d3d11.dll")]
            if pattern == "dxvk-d3d11.dll"
            else []
        )
        run.return_value = mock.Mock(stdout="wine-11.0\n", stderr="")
        doctor = WineEngine(RelayConfig()).doctor()
        self.assertTrue(doctor["dxvk_available"])


class FakePublisher:
    def __init__(self) -> None:
        self.added: list[tuple[str, str, str, int]] = []
        self.removed: list[str] = []

    def add(self, notification_id: str, title: str, body: str, urgency: int) -> None:
        self.added.append((notification_id, title, body, urgency))

    def remove(self, notification_id: str) -> None:
        self.removed.append(notification_id)


class NotificationTests(unittest.TestCase):
    @mock.patch("luma_relay.session.NotificationBroker", side_effect=RelayError("unavailable"))
    def test_missing_desktop_notification_service_does_not_block_launch(
        self, _broker: mock.Mock
    ) -> None:
        self.assertIsNone(
            _create_notification_broker(
                Path("/tmp/relay-notifications.sock"),
                {"app_id": "sample-app-12345678", "name": "Sample"},
            )
        )

    def test_notification_contract_strips_controls_and_bounds_content(self) -> None:
        payload = validate_notification(
            {
                "schema": 1,
                "operation": "add",
                "id": "tray.42",
                "title": "Message\x00",
                "body": "Hello\nworld",
                "urgency": 2,
            }
        )
        self.assertEqual(payload["title"], "Message")
        self.assertEqual(payload["body"], "Hello\nworld")
        self.assertEqual(payload["urgency"], 2)

    def test_invalid_identity_is_rejected(self) -> None:
        with self.assertRaises(RelayError):
            validate_notification(
                {"schema": 1, "operation": "add", "id": "../other-app", "body": "x"}
            )

    def test_notification_rate_limit_is_per_running_app_session(self) -> None:
        broker = NotificationBroker(
            Path("/tmp/relay-notifications.sock"),
            {"app_id": "sample-app-12345678", "name": "Sample"},
            FakePublisher(),
        )
        self.assertTrue(all(not broker._rate_limited() for _attempt in range(30)))
        self.assertTrue(broker._rate_limited())

    def test_private_socket_delivers_only_validated_messages(self) -> None:
        socket_root = "/private/tmp" if Path("/private/tmp").is_dir() else None
        with tempfile.TemporaryDirectory(dir=socket_root) as temporary:
            path = Path(temporary) / "notify.sock"
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                probe.bind(str(path))
            except PermissionError:
                self.skipTest("the test sandbox does not permit Unix socket creation")
            finally:
                probe.close()
                path.unlink(missing_ok=True)
            stop = threading.Event()
            publisher = FakePublisher()
            broker = NotificationBroker(
                path,
                {"app_id": "sample-app-12345678", "name": "Sample"},
                publisher,
            )
            thread = threading.Thread(target=broker.serve, args=(stop,), daemon=True)
            thread.start()
            for _attempt in range(100):
                if path.exists() and path.stat().st_mode & 0o777 == 0o600:
                    break
                threading.Event().wait(0.01)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.connect(str(path))
            client.sendall(
                b'{"schema":1,"operation":"add","id":"tray.7",'
                b'"title":"Sample","body":"Ready","urgency":1}'
            )
            client.close()
            for _attempt in range(100):
                if publisher.added:
                    break
                threading.Event().wait(0.01)
            stop.set()
            thread.join(timeout=2)
            self.assertEqual(publisher.added, [("tray.7", "Sample", "Ready", 1)])
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
