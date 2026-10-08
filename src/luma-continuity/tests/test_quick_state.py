from pathlib import Path
import tempfile
import unittest
from luma_continuity.quick_state import LinkIntent,quick_state
from luma_continuity.policy import Journal,Denied
from luma_continuity.local import ScopedExchange


class QuickStateTests(unittest.TestCase):
    def test_disabled_survives_restart_without_grant_or_receipt_loss(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'device'/'continuity.db'
            intent=LinkIntent(Path(root)/'enabled.json')
            journal=Journal(path)
            peer='a'*64;epoch='b'*32
            journal.approve(peer,epoch,['messages.send'])
            request=dict(version=1,epoch=epoch,id='c'*32,account=None,
                         capability='messages.send',expires=1100,payload={})
            calls=[]
            handler=lambda *args:calls.append(1) or {'ok':True}
            first=journal.dispatch(peer,request,handler,now=1000)
            intent.save(False);journal.set_enabled(False);journal.close()
            journal=Journal(path)
            try:
                self.assertFalse(LinkIntent(intent.path).load())
                with self.assertRaises(Denied):journal.dispatch(peer,request,handler,now=1000)
                exchange=ScopedExchange(path.parent,peer,epoch)
                with self.assertRaises(Denied):
                    with exchange._admit(request):pass
                journal.set_enabled(True);intent.save(True)
                self.assertEqual(journal.dispatch(peer,request,handler,now=1000),first)
                self.assertEqual(calls,[1])
            finally:journal.close()

    def test_no_connected_inference_and_status_priority(self):
        ready=dict(account_status='signed_in',service_status='ready',stale=False)
        self.assertEqual(quick_state(ready,True)['status'],'ready')
        self.assertEqual(quick_state(ready,True)['connected_peers'],0)
        self.assertEqual(quick_state(dict(ready,busy=True),False)['status'],'disabled')
        self.assertEqual(quick_state(dict(ready,account_status='locked'),True)['status'],'locked')
        self.assertEqual(quick_state(dict(ready,stale=True),True)['status'],'offline')

    def test_malformed_or_public_intent_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            intent=LinkIntent(Path(root)/'enabled.json')
            intent.save(False);intent.path.chmod(0o644)
            with self.assertRaises(PermissionError):intent.load()
            intent.path.chmod(0o600);intent.path.write_text('{}')
            with self.assertRaises(ValueError):intent.load()
