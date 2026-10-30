# SPDX-License-Identifier: Apache-2.0
"""The Messages background service imports and notifies received MMS."""

import importlib.machinery
import importlib.util
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/prairie-core"))

try:
    import gi  # noqa: F401
except ImportError:  # pragma: no cover - the daemon needs PyGObject
    gi = None


def load_daemon():
    loader = importlib.machinery.SourceFileLoader(
        "prairie_messages_daemon", str(ROOT / "src/prairie-core/bin/prairie-messages-daemon"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@unittest.skipIf(gi is None, "PyGObject is not installed")
class DaemonMmsTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        home = pathlib.Path(self.home.name)
        patcher = mock.patch.dict(os.environ, {"HOME": str(home), "XDG_DATA_HOME": str(home / "data")})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.daemon = load_daemon()
        mms = home / ".mms/modemmanager"
        mms.mkdir(parents=True)
        self.part = mms / "f37abc"
        self.part.write_bytes(b"\xff\xd8\xffJPEGDATA")
        self.store = self.daemon.MessageStore(home / "data/prairie/messages/messages.db")
        self.addCleanup(self.store.close)
        self.importer = object.__new__(self.daemon.MessageImporter)
        self.importer.store = self.store
        self.importer.mailbox = None
        self.importer.notifier = mock.Mock()
        self.addCleanup(lambda: self.importer.mailbox and self.importer.mailbox.close())

    def message(self, status="received"):
        return {
            "Status": status,
            "Date": "2026-10-30T14:18:12+0000",
            "Sender": "+15555550123",
            "Recipients": ["+15555550100"],
            "Modem Number": "+15555550100",
            "Attachments": [("photo.jpg", "image/jpeg", str(self.part), 0, self.part.stat().st_size)],
        }

    def test_received_mms_is_imported_and_notified_once(self):
        path = "/org/ofono/mms/modemmanager/f37abc"
        self.importer._mms_message(path, self.message())
        self.importer._mms_message(path, self.message())
        uid = self.importer.mailbox.uid_for_transport("mmsd:" + path)
        self.assertIsNotNone(uid)
        self.assertEqual(self.importer.notifier.publish.call_count, 1)
        address, _name, body = self.importer.notifier.publish.call_args.args
        self.assertEqual(body, "Photo")
        self.assertEqual(address, self.importer.mailbox.message(uid).address)

    def test_sent_and_read_mms_do_not_notify(self):
        self.importer._mms_message("/org/ofono/mms/modemmanager/a1", self.message("sent"))
        self.importer._mms_message("/org/ofono/mms/modemmanager/a2", self.message("read"))
        self.importer.notifier.publish.assert_not_called()

    def test_summary(self):
        self.assertEqual(self.daemon.mms_summary({"Attachments": [("a", "video/mp4", "", 0, 1)]}), "Video")
        self.assertEqual(self.daemon.mms_summary({"Attachments": [("a", "image/png", "", 0, 1),
                                                                  ("b", "text/vcard", "", 0, 1)]}), "Attachment")


if __name__ == "__main__":
    unittest.main()
