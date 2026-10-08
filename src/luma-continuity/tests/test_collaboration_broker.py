# SPDX-License-Identifier: Apache-2.0
import json
import unittest
from luma_continuity.collaboration_broker import route, Refused, PREFIX


class Routes(unittest.TestCase):
    def test_real_application_capability_routes(self):
        note = 'org.projectluma.Notes'
        task = 'org.projectluma.Tasks'
        contact = 'org.projectluma.Contacts'
        document = '4096a1fb-5b5a-410f-9c4d-2843f8d835f3'
        self.assertEqual(route(task, 'POST', PREFIX+'/documents/'+document+'/comments', {'body':'hello'}), document)
        route(note, 'POST', PREFIX+'/documents', {'kind':'note', 'content':{}})
        route(contact, 'POST', '/api/hub/sync/identity/discover', {'hashes':[]})
        route(note, 'GET', '/api/hub/sync/people/owned.bob', {})
        for app, method, path, payload in [
            (contact,'GET',PREFIX+'/documents',{}),
            (note,'POST',PREFIX+'/documents',{'kind':'list'}),
            (task,'POST',PREFIX+'/documents',{'kind':'note'}),
            (note,'POST','/api/hub/sync/identity/discover',{}),
            (note,'GET','https://attacker.invalid/anything',{}),
            (note,'GET',PREFIX+'/documents?host=attacker',{}),
            (note,'GET',PREFIX+'/documents',{'token':'attacker'}),
            (note,'POST',PREFIX+'/documents/'+document,{}),
            (note,'GET',PREFIX+'/documents/'+document+'/revoke',{}),
            (note,'POST',PREFIX+'/documents',{'content':'x'*1048577}),
            ('unregistered','GET',PREFIX+'/documents',{}),
            (note,'DELETE',PREFIX+'/documents',{}),
            (note,'GET','/api/hub/sync/people/%2f',{})]:
            with self.subTest(path=path,app=app):
                with self.assertRaises(Refused): route(app,method,path,payload)


class WorkerBoundary(unittest.TestCase):
    def test_four_worker_bound_busy_and_authentication_before_dispatch(self):
        import threading,time
        from gi.repository import GLib
        from luma_continuity.collaboration_broker import CollaborationBroker
        broker=CollaborationBroker();release=threading.Event();started=[];calls=[]
        broker.authenticate=lambda connection,sender: 'org.projectluma.Notes'
        def request(*args):
            started.append(threading.current_thread().ident)
            self.assertTrue(release.wait(3),'test must release owned workers')
            return {'transport':'owned controlled response'}
        broker.request=request
        class Reply:
            value=None;error=None
            def return_value(self,value):self.value=value.unpack()[0]
            def return_dbus_error(self,name,message):self.error=(name,message)
        replies=[Reply() for _ in range(5)]
        values=('GET',PREFIX+'/documents','{}')
        for reply in replies:broker.dispatch(None,':owned','CollaborationRequest',values,reply,GLib)
        deadline=time.monotonic()+1
        while len(started)<4 and time.monotonic()<deadline:time.sleep(.005)
        self.assertEqual(len(set(started)),4)
        self.assertEqual(replies[4].error[0],'org.projectluma.Connect1.Error.Busy')
        self.assertIsNone(replies[4].value)
        release.set();context=GLib.MainContext.default();deadline=time.monotonic()+3
        while any(reply.value is None for reply in replies[:4]) and time.monotonic()<deadline:
            while context.pending():context.iteration(False)
            time.sleep(.005)
        self.assertTrue(all(json.loads(reply.value)['result']['transport']=='owned controlled response' for reply in replies[:4]))
        def reject(*_):raise Refused('private bearer should never be reflected')
        broker.authenticate=reject;last=Reply();broker.dispatch(None,':foreign','CollaborationRequest',values,last,GLib)
        self.assertIn('Refused',last.error[0]);self.assertNotIn('bearer',last.error[1])
        self.assertEqual(len(started),4,'refused caller must not start a worker')

    def test_current_authorization_and_removed_type_gate(self):
        from unittest.mock import patch
        from prairie_apps.connect_sync import DeviceIdentity,HubResponseError
        from luma_continuity.collaboration_broker import CollaborationBroker
        broker=CollaborationBroker();broker.identity=lambda:DeviceIdentity('owned','private-test-bearer','http://127.0.0.1:3000','Owned','')
        calls=[]
        class HTTP:
            def __init__(self,**_):pass
            def get_json(self,url,*,token):
                calls.append(url)
                if url.endswith('/account'):raise HubResponseError(401,url,'signed out')
                return {'kind':'list','id':'4096a1fb-5b5a-410f-9c4d-2843f8d835f3'}
            def post_json(self,*_,**__):raise AssertionError('cross-kind restore must not be sent')
        with patch('prairie_apps.connect_sync.HubClient',HTTP):
            with self.assertRaises(HubResponseError):broker.context('org.projectluma.Notes')
            with self.assertRaises(Refused):broker.request('org.projectluma.Notes','POST',PREFIX+'/documents/4096a1fb-5b5a-410f-9c4d-2843f8d835f3/restore',{'revision':1})
        self.assertTrue(calls[0].endswith('/account'))
        self.assertTrue(calls[1].endswith('/removed'))

    def test_favorites_export_hints_only_after_current_authorization(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        from luma_continuity.collaboration_broker import CollaborationBroker
        broker=CollaborationBroker();calls=[];broker.context=lambda app:calls.append(app)
        contact=SimpleNamespace(favourite=True,handle='Owned.Bob',phone='private phone',email='private email',luma_account='private account')
        with patch('prairie_apps.eds_backend.load_contacts',return_value=[contact]):
            self.assertEqual(broker.favorites('org.projectluma.Notes'),{'handles':['owned.bob']})
        self.assertEqual(calls,['org.projectluma.Notes'])
        def reject(app):raise Refused('signed out')
        broker.context=reject
        with patch('prairie_apps.eds_backend.load_contacts') as loader:
            with self.assertRaises(Refused):broker.favorites('org.projectluma.Notes')
            loader.assert_not_called()

if __name__ == '__main__': unittest.main()
