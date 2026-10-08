from pathlib import Path
import json
import tempfile
import unittest
from luma_continuity.bootstrap import create_identity,approve_peer
from luma_continuity.call_devices import CallDevices
from luma_continuity.policy import Journal


class CallDevicesTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.directory=self.root/'device';other=self.root/'other'
        create_identity(self.directory);self.peer=create_identity(other)
        self.account='synthetic-account';self.epoch='e'*32
        approve_peer(self.directory,other/'device.pem',self.peer,self.epoch,['messages.read'],account=self.account)
        self.devices=CallDevices(self.directory,self.root/'config/calls-phone.json',
            account=lambda:self.account,changed=lambda:None,receiver_factory=None)
        self.addCleanup(self.devices.stop)

    def test_separate_call_grants_preserve_messages(self):
        for capability in ('calls.read','calls.control','calls.audio'):
            self.devices.set_permission(self.peer,capability,False,True)
        self.devices.set_permission(self.peer,'calls.control',False,False)
        journal=Journal(self.directory/'continuity.db')
        try:incoming,outgoing=journal.db.execute('SELECT grants,outgoing_grants FROM peers').fetchone()
        finally:journal.close()
        self.assertEqual(set(json.loads(incoming)),{'messages.read'})
        self.assertEqual(set(json.loads(outgoing)),{'messages.read','calls.read','calls.audio'})

    def test_message_pairing_never_implies_call_selection(self):
        with self.assertRaises(PermissionError):self.devices.select(self.peer,'Phone','127.0.0.1',20001)
        self.assertFalse(self.devices.path.exists())

    def test_account_loss_denies_permission_and_selected_state(self):
        self.devices.set_permission(self.peer,'calls.read',False,True)
        self.assertTrue(self.devices.permission(self.peer,'calls.read'))
        self.account=None
        self.assertFalse(self.devices.permission(self.peer,'calls.read'))
        self.assertFalse(self.devices.state()['connected'])

    def test_non_call_capability_cannot_be_changed(self):
        with self.assertRaises(ValueError):self.devices.set_permission(self.peer,'messages.send',False,True)


if __name__=='__main__':unittest.main()
