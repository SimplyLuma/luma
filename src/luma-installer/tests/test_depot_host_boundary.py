# SPDX-License-Identifier: Apache-2.0
"""Real GI view models and controlled transport admission; not signed install proof."""
import json
from pathlib import Path
import sys
import unittest
from unittest import mock
from types import SimpleNamespace as NS
import time

HERE=Path(__file__).resolve()
if (HERE.parents[2]/'luma-depot').is_dir():sys.path.insert(0,str(HERE.parents[2]/'luma-depot'))
from gi.repository import Gio, GLib
from luma_depot import host_wire as wire, native, providers
from luma_depot.host_client import Client, Installation
from luma_depot.providers import App, InstalledApp, Permission, Progress, Result
from luma_installer import depot_host as host, depot_autoupdate as policy

OLD,NEW='a'*64,'b'*64
ID='catalog:notes'
RID='a'*32
GROW=Permission('files.music','Music','Access music',True,'sensitive','widened')

class Wire(unittest.TestCase):
    def test_actual_public_models_roundtrip(self):
        value=(App(ID,'Notes','Write notes',permissions=(GROW,)),InstalledApp(ID,'1',24,app=App(ID,'Notes',''),commit=OLD))
        self.assertEqual(wire.loads(wire.dumps(value)),value)
    def test_unknown_dataclass_rejected(self):
        from dataclasses import dataclass
        @dataclass
        class Secret:token:str
        with self.assertRaises(ValueError):wire.dumps(Secret('not-exported'))
    def test_object_tags_and_fields_are_strict(self):
        for value in ({'$type':'Path','fields':{}},{'$type':'App','fields':{}},{'$tuple':[],'extra':1},{'$arbitrary':'x'}):
            with self.subTest(value=value),self.assertRaises(ValueError):wire.loads(json.dumps(value))
    def test_total_budget_refuses_many_individually_valid_strings_before_final_dump(self):
        called=[]
        original=wire.json.dumps
        def observed(value,*a,**k):
            if type(value) is dict:called.append(value)
            return original(value,*a,**k)
        with mock.patch.object(wire.json,'dumps',side_effect=observed),self.assertRaises(ValueError):
            wire.dumps({'texts':['x'*65536]*4096})
        self.assertEqual(called,[],'must refuse before constructing a huge final JSON buffer')
    def test_structure_depth_and_length_bounds(self):
        value='x'
        for _ in range(20):value=[value]
        for item in (value,['x']*4097,'x'*65537,float('nan'),2**64):
            with self.subTest(kind=type(item)),self.assertRaises(ValueError):wire.dumps(item)
    def test_oversized_tuple_rejected_before_conversion_and_on_decode(self):
        class NeverIterated(tuple):
            def __iter__(self):raise AssertionError('oversized tuple was copied before refusal')
        with self.assertRaises(ValueError):wire.dumps(NeverIterated(range(wire.MAX_ITEMS+1)))
        with self.assertRaises(ValueError):wire.loads(json.dumps({'$tuple':[None]*(wire.MAX_ITEMS+1)}))
    def test_request_shapes_no_url_path_command_or_boolean_coercion(self):
        for operation,values in [('Install',{'app_id':ID,'url':'https://example.com'}),
             ('Install',{'app_id':'../../bin/sh'}),('SetSettings',{'install_events':1,'countme':True,'app_updates':True}),
             ('Update',{'app_id':ID,'commit':NEW,'baseline':'old','approve':False}),
             ('ReviewChannel',{'app_id':ID,'branch':'stable'}),('SystemChange',{'app_id':ID,'action':'run'}),
             ('SaveReview',{'slug':'notes','rating':True,'title':'','body':''})]:
            with self.subTest(operation=operation),self.assertRaises(ValueError):wire.request(operation,json.dumps(values))
    def test_duplicate_fields_unknown_operations_and_oversize_rejected(self):
        for op,text in [('Launch','{"app_id":"one","app_id":"two"}'),('Spawn','{}'),('Catalogue','x'*8193)]:
            with self.subTest(op=op),self.assertRaises(ValueError):wire.request(op,text)
    def test_valid_request_preserves_baseline_and_explicit_consent(self):
        value={'app_id':ID,'commit':NEW,'baseline':OLD,'approve':False}
        self.assertEqual(wire.request('Update',json.dumps(value)),value)

class Admission(unittest.TestCase):
    def setUp(self):
        self.b=host.Broker.__new__(host.Broker)
        self.b.jobs={};self.b.waiting=[];self.b.firmware=None;self.b.reviews={};self.b.last_request=0
        self.b._watch=mock.Mock();self.b._dispatch=mock.Mock()
        self.connection=mock.Mock()
    def call(self,rid=RID,op='Installed',values=None,sender=':1.7',app='org.projectluma.Depot'):
        inv=mock.Mock();args=GLib.Variant('(sss)',(rid,op,json.dumps(values if values is not None else {'refresh':False})))
        with mock.patch.object(host,'authenticate',return_value=app):
            self.b._call(self.connection,sender,'',host.BUS,'Request',args,inv)
        return inv
    def test_other_signed_app_refused_before_dispatch(self):
        inv=self.call(app='org.projectluma.Notes')
        inv.return_dbus_error.assert_called_once();self.b._dispatch.assert_not_called();self.assertFalse(self.b.jobs)
    def test_invalid_request_has_no_auth_or_dispatch(self):
        with mock.patch.object(host,'authenticate',side_effect=AssertionError('should not authenticate')):
            inv=mock.Mock()
            self.b._call(self.connection,':1.7','',host.BUS,'Request',GLib.Variant('(sss)',(RID,'Launch','{"app_id":"/tmp/x"}')),inv)
        inv.return_dbus_error.assert_called_once();self.b._dispatch.assert_not_called()
    def test_max_four_reserved_provider_calls(self):
        for i in range(4):self.call(rid=f'{i:032x}')
        self.assertEqual(len(self.b.jobs),4)
        inv=self.call(rid='f'*32)
        inv.return_dbus_error.assert_called_once();self.assertEqual(self.b._dispatch.call_count,4)
    def test_one_transaction_runs_and_the_next_waits_instead_of_failing(self):
        self.call(op='Install',values={'app_id':ID})
        inv=self.call(rid='b'*32,op='Remove',values={'app_id':ID,'keep_data':True})
        inv.return_dbus_error.assert_not_called();self.assertEqual(self.b._dispatch.call_count,1)
        self.assertEqual(self.b.waiting,[(':1.7','b'*32)])
    def test_duplicate_and_foreign_cancel_refused(self):
        self.call();inv=self.call();inv.return_dbus_error.assert_called_once()
        own=mock.Mock();other=mock.Mock()
        self.b._call(self.connection,':1.8','',host.BUS,'Cancel',GLib.Variant('(s)',(RID,)),other)
        other.return_dbus_error.assert_called_once();self.assertFalse(self.b.jobs[(':1.7',RID)]['cancel'].is_cancelled())
        self.b._call(self.connection,':1.7','',host.BUS,'Cancel',GLib.Variant('(s)',(RID,)),own)
        own.return_value.assert_called_once();self.assertTrue(self.b.jobs[(':1.7',RID)]['cancel'].is_cancelled())
    def test_real_terminal_callback_releases_cancelled_reservation(self):
        inv=self.call();done=self.b._dispatch.call_args.args[3]
        self.b.jobs[(':1.7',RID)]['cancel'].cancel()
        self.assertTrue(done._deliver_cancelled)
        done(Result(error=providers.ProviderError('Cancelled')))
        self.assertFalse(self.b.jobs);inv.return_value.assert_called_once()
        payload=wire.loads(inv.return_value.call_args.args[0].unpack()[0]);self.assertFalse(payload['ok'])

class ClientAdmission(unittest.TestCase):
    def setUp(self):
        self.client=Client.__new__(Client)
        self.client.connection=mock.Mock()
        self.client.connection.call_finish.side_effect=lambda reply:reply
        self.client.pending={};self.client.queue=[];self.client.inflight=set()
        self.client.closed=False;self.client.subscription=1
        self.replies=[]
    def calls(self):
        return [x for x in self.client.connection.call.call_args_list if x.args[3]=='Request']
    def complete(self,index=0):
        call=self.calls()[index]
        call.args[-1](self.client.connection,GLib.Variant('(s)',(wire.dumps({'ok':True,'value':False}),)))
    def drain(self):
        context=GLib.MainContext.default()
        for _ in range(100):
            if not context.pending():break
            context.iteration(False)
    def test_cold_window_reads_are_bounded_and_all_complete(self):
        for _ in range(7):self.client.call('ReviewsEnrolled',{},self.replies.append)
        self.assertEqual(len(self.calls()),2);self.assertEqual(len(self.client.inflight),2)
        for index in range(7):
            self.complete(index)
            self.assertLessEqual(len(self.client.inflight),2)
        self.assertEqual(len(self.replies),7);self.assertTrue(all(r.ok for r in self.replies))
        self.assertFalse(self.client.pending);self.assertFalse(self.client.queue)
    def test_queued_cancel_does_not_start_a_host_operation(self):
        self.client.call('ReviewsEnrolled',{},self.replies.append)
        self.client.call('ReviewsEnrolled',{},self.replies.append)
        cancel=Gio.Cancellable()
        self.client.call('ReviewsEnrolled',{},self.replies.append,cancellable=cancel)
        cancel.cancel();self.drain()
        self.assertEqual(len(self.calls()),2);self.assertEqual(len(self.replies),1)
        self.assertFalse(self.replies[0].ok);self.assertFalse(self.client.queue)
    def test_active_cancel_waits_for_actual_terminal_reply(self):
        cancel=Gio.Cancellable()
        self.client.call('ReviewsEnrolled',{},self.replies.append,cancellable=cancel)
        cancel.cancel();self.drain()
        self.assertEqual(len(self.client.inflight),1);self.assertFalse(self.replies)
        self.assertEqual(self.client.connection.call.call_args.args[3],'Cancel')
        self.complete();self.assertEqual(len(self.replies),1);self.assertFalse(self.client.inflight)
    def test_updates_have_their_own_lane_one_at_a_time(self):
        for app in ('catalog:a','catalog:b','catalog:c'):
            self.client.call('Update',update_values(app),self.replies.append,progress=lambda p:None)
        for _ in range(3):self.client.call('ReviewsEnrolled',{},self.replies.append)
        ops=[x.args[4].unpack()[1] for x in self.calls()]
        self.assertEqual(ops,['Update','ReviewsEnrolled','ReviewsEnrolled'])
        self.complete(0)
        self.assertEqual([x.args[4].unpack()[1] for x in self.calls()][3:],['Update'])
    def test_service_refusal_is_reported_as_said_not_as_a_broken_service(self):
        self.client.call('ReviewsEnrolled',{},self.replies.append)
        failure=GLib.Error.new_literal(Gio.dbus_error_quark(),
            f'GDBus.Error:{host.BUS}.Refused: A hardware update is already running.',Gio.DBusError.FAILED)
        self.client.connection.call_finish.side_effect=lambda r:(_ for _ in ()).throw(failure)
        self.calls()[0].args[-1](self.client.connection,None)
        self.assertEqual(str(self.replies[0].error),'A hardware update is already running.')
        self.assertNotIn('application service',self.replies[0].error.hint)
    def test_queue_limit_is_a_terminal_error_and_close_never_dispatches_more(self):
        for _ in range(17):self.client.call('ReviewsEnrolled',{},self.replies.append)
        self.drain();self.assertEqual(len(self.replies),1);self.assertFalse(self.replies[0].ok)
        self.assertEqual(len(self.client.pending),16);self.assertEqual(len(self.calls()),2)
        self.client.close();self.complete(0);self.complete(1);self.drain()
        self.assertEqual(len(self.calls()),3,'only the fixed unsubscribe may be added on close')
        self.assertFalse(self.client.pending);self.assertFalse(self.client.queue)
        self.assertEqual(len(self.replies),1)

def update_values(app):
    return {'app_id':app,'commit':NEW,'baseline':OLD,'approve':False}

def reply(inv):
    """The terminal payload a D-Bus caller received, or the refusal text."""
    if inv.return_dbus_error.called:return ('refused',inv.return_dbus_error.call_args.args[1])
    if not inv.return_value.called:return None
    return wire.loads(inv.return_value.call_args.args[0].unpack()[0])

class UpdateQueue(Admission):
    """Update all: every request is admitted, runs alone, in order."""
    APPS=[f'catalog:app{i}' for i in range(5)]
    def setUp(self):
        super().setUp();self.b.lifecycle_ids={};self.b.bus=mock.Mock()
    def tearDown(self):
        for job in self.b.jobs.values():
            if job.get('timer'):GLib.source_remove(job['timer'])
    def update_all(self,sender=':1.7'):
        return [self.call(rid=f'{i+1:032x}',op='Update',values=update_values(app),sender=sender)
                for i,app in enumerate(self.APPS)]
    def started(self):return [c.args[2].get('app_id') for c in self.b._dispatch.call_args_list]
    def finish(self,index,result=None):
        self.b._dispatch.call_args_list[index].args[3](result or Result(value=None))
    def test_concurrent_updates_all_admitted_and_run_one_at_a_time_in_order(self):
        invs=self.update_all()
        self.assertTrue(all(reply(i) is None for i in invs),'no request is refused or answered early')
        self.assertEqual(self.started(),self.APPS[:1])
        for index in range(len(self.APPS)):
            self.assertEqual(self.started(),self.APPS[:index+1],'exactly one runs at a time')
            self.assertIsNone(reply(invs[index+1]) if index+1<len(invs) else None)
            self.finish(index)
            self.assertTrue(reply(invs[index])['ok'])
        self.assertEqual(self.started(),self.APPS)
        self.assertFalse(self.b.jobs);self.assertFalse(self.b.waiting)
    def test_waiting_update_reports_queued_then_starting(self):
        self.update_all()
        waiting=self.b.jobs[(':1.7',f'{2:032x}')]
        self.assertTrue(waiting['queued'])
        self.assertEqual((waiting['progress'].app_id,waiting['progress'].stage,waiting['progress'].fraction),
                         (self.APPS[1],'Queued',0.0))
        self.assertIsNone(self.b.jobs[(':1.7',f'{1:032x}')]['progress'],'the running job reports its own progress')
        self.finish(0)
        self.assertFalse(waiting['queued']);self.assertEqual(waiting['progress'].stage,'Starting')
    def test_failure_in_the_middle_does_not_fail_the_rest(self):
        invs=self.update_all()
        self.finish(0)
        self.finish(1,Result(error=providers.ProviderError('The download stopped.',hint='Check the connection.')))
        for index in range(2,len(self.APPS)):self.finish(index)
        self.assertEqual(self.started(),self.APPS)
        outcomes=[reply(i)['ok'] for i in invs]
        self.assertEqual(outcomes,[True,False,True,True,True])
        self.assertEqual(reply(invs[1])['hint'],'Check the connection.')
    def test_provider_exception_on_start_still_moves_the_queue(self):
        invs=self.update_all()
        self.b._dispatch.side_effect=[RuntimeError('broken'),None,None,None]
        self.finish(0)
        self.assertFalse(reply(invs[1])['ok'])
        self.assertEqual(self.started(),self.APPS[:3])
    def test_cancel_queued_update_answers_now_and_never_starts(self):
        invs=self.update_all()
        own=mock.Mock()
        self.b._call(self.connection,':1.7','',host.BUS,'Cancel',GLib.Variant('(s)',(f'{3:032x}',)),own)
        own.return_value.assert_called_once()
        self.assertEqual(reply(invs[2]),{'ok':False,'error':'Cancelled','hint':'Cancelled','detail':''})
        for index in range(4):self.finish(index)
        self.assertEqual(self.started(),[a for a in self.APPS if a!=self.APPS[2]])
        self.assertEqual([reply(i)['ok'] for i in invs],[True,True,False,True,True])
    def test_cancel_running_update_waits_for_its_terminal_reply(self):
        invs=self.update_all()
        own=mock.Mock()
        self.b._call(self.connection,':1.7','',host.BUS,'Cancel',GLib.Variant('(s)',(f'{1:032x}',)),own)
        self.assertTrue(self.b._dispatch.call_args_list[0].args[5].is_cancelled())
        self.assertIsNone(reply(invs[0]));self.assertEqual(self.started(),self.APPS[:1])
        self.finish(0,Result(error=providers.ProviderError('Cancelled')))
        self.assertEqual(self.started(),self.APPS[:2])
    def test_reads_are_not_crowded_out_by_waiting_updates(self):
        self.update_all()
        for i in range(3):
            inv=self.call(rid=f'{i+0xa0:032x}');inv.return_dbus_error.assert_not_called()
        inv=self.call(rid='f'*32);inv.return_dbus_error.assert_called_once()
    def test_queue_is_bounded(self):
        self.b.jobs={(':1.9',f'{i:032x}'):{'transaction':True,'queued':True,'mutation':True} for i in range(host.MAX_WAITING)}
        inv=self.call(op='Update',values=update_values(ID))
        self.assertEqual(reply(inv)[0],'refused');self.b._dispatch.assert_not_called()
    def test_review_post_is_not_held_behind_updates(self):
        self.update_all()
        inv=self.call(rid='e'*32,op='DeleteReview',values={'slug':'notes'})
        inv.return_dbus_error.assert_not_called();self.assertEqual(self.started()[-1:],[None])
    def test_vanished_client_drops_its_waiting_updates(self):
        invs=self.update_all()
        other=self.call(rid='d'*32,op='Update',values=update_values(ID),sender=':1.9')
        self.b.watches={};self.b.firmware_watchers=set()
        with mock.patch.object(host.Gio,'bus_watch_name_on_connection',return_value=0) as watching:
            host.Broker._watch(self.b,':1.7')
        gone=watching.call_args.args[-1]
        gone()
        self.assertTrue(all(reply(i) is None for i in invs),'a vanished caller gets no reply')
        self.finish(0,Result(error=providers.ProviderError('Cancelled')))
        self.assertEqual(self.started(),[self.APPS[0],ID]);self.assertIsNone(reply(other))
        self.finish(1);self.assertTrue(reply(other)['ok'])
    def test_hardware_update_holds_the_queue_until_it_finishes(self):
        self.b.firmware=NS(installing='dev0')
        inv=self.call(op='Update',values=update_values(ID))
        self.b._dispatch.assert_not_called();self.assertIsNone(reply(inv))
        self.b.firmware.installing=''
        self.b._pump()
        self.assertEqual(self.started(),[ID])

class FakeBus:
    """Client transport wired straight to a real Broker._call, no daemon."""
    def __init__(self,broker,sender=':1.7'):
        self.broker,self.sender,self.requests=broker,sender,[]
    def call(self,bus,obj,iface,method,args,reply_type,flags,timeout,cancellable,callback):
        inv=mock.Mock()
        if method=='Request':self.requests.append(args.unpack()[1])
        def value(variant):
            if callback:GLib.idle_add(lambda:callback(self,variant) and False)
        def error(name,message):
            failure=GLib.Error.new_literal(Gio.dbus_error_quark(),f'GDBus.Error:{name}: {message}',Gio.DBusError.FAILED)
            def deliver():
                callback(self,failure)
            if callback:GLib.idle_add(lambda:deliver() and False)
        inv.return_value.side_effect=value;inv.return_dbus_error.side_effect=error
        with mock.patch.object(host,'authenticate',return_value='org.projectluma.Depot'):
            self.broker._call(None,self.sender,'',host.BUS,method,args,inv)
    def call_finish(self,result):
        if isinstance(result,GLib.Error):raise result
        return result

class UpdateAllEndToEnd(unittest.TestCase):
    """The Depot client and the host service together, as Update all uses them."""
    APPS=[f'catalog:app{i}' for i in range(4)]
    def setUp(self):
        b=self.b=host.Broker.__new__(host.Broker)
        b.jobs={};b.waiting=[];b.firmware=None;b.reviews={};b.last_request=0;b.lifecycle_ids={}
        b.watches={};b._watch=mock.Mock();b.bus=mock.Mock()
        b.installer=mock.Mock();b.catalogue=mock.Mock()
        self.records=[InstalledApp(a,'1',1,update_version='2',app=App(a,a,''),commit=OLD,update_commit=NEW) for a in self.APPS]
        b.installer.installed.side_effect=lambda cb,cancel,**kw:cb(Result(value=tuple(self.records)))
        self.running=[]
        b.installer.update.side_effect=lambda app_id,progress,done,cancel,**kw:self.running.append((app_id,done))
        client=self.client=Client.__new__(Client)
        client.connection=FakeBus(b);client.pending={};client.queue=[];client.inflight=set()
        client.closed=False;client.subscription=1;client.firmware=None
        self.installation=Installation(client)
        self.results={};self.progress={}
    def drain(self):
        context=GLib.MainContext.default()
        for _ in range(200):
            if not context.pending():break
            context.iteration(False)
    def update_all(self):
        for app in self.APPS:
            self.installation.update(app,lambda p,a=app:self.progress.setdefault(a,[]).append(p.stage),
                lambda r,a=app:self.results.__setitem__(a,r),Gio.Cancellable(),
                expected_commit=NEW,expected_installed_commit=OLD)
        self.drain()
    def test_update_all_succeeds_in_order_with_queued_items_marked(self):
        with mock.patch.object(policy,'load',return_value={}):
            self.update_all()
            self.assertEqual([a for a,_ in self.running],self.APPS[:1])
            self.assertFalse(self.results,'nothing is reported failed while waiting')
            for app in self.APPS[1:]:self.assertEqual(self.progress[app],['Queued'])
            for index in range(len(self.APPS)):
                self.running[index][1](Result(value=None));self.drain()
        self.assertEqual([a for a,_ in self.running],self.APPS)
        self.assertTrue(all(self.results[a].ok for a in self.APPS))
        for app in self.APPS[1:]:self.assertEqual(self.progress[app],['Queued','Starting'])
    def test_middle_failure_and_queued_cancel(self):
        cancels={}
        with mock.patch.object(policy,'load',return_value={}):
            for app in self.APPS:
                cancels[app]=Gio.Cancellable()
                self.installation.update(app,lambda p:None,lambda r,a=app:self.results.__setitem__(a,r),
                    cancels[app],expected_commit=NEW,expected_installed_commit=OLD)
            self.drain()
            cancels[self.APPS[3]].cancel();self.drain()
            self.assertFalse(self.results[self.APPS[3]].ok)
            self.running[0][1](Result(value=None));self.drain()
            self.running[1][1](Result(error=providers.ProviderError('Download stopped')));self.drain()
            self.running[2][1](Result(value=None));self.drain()
        self.assertEqual([a for a,_ in self.running],self.APPS[:3])
        self.assertEqual([self.results[a].ok for a in self.APPS],[True,False,True,False])
        self.assertEqual(str(self.results[self.APPS[1]].error),'Download stopped')
    def test_live_depot_two_requests_in_flight_are_queued_not_failed(self):
        """The Depot already installed sends two requests at once."""
        self.client.connection.call(host.BUS,host.OBJECT,host.BUS,'Request',
            GLib.Variant('(sss)',('1'*32,'Update',json.dumps(update_values(self.APPS[0])))),None,0,0,None,None)
        results=[]
        with mock.patch.object(policy,'load',return_value={}):
            self.client.connection.call(host.BUS,host.OBJECT,host.BUS,'Request',
                GLib.Variant('(sss)',('2'*32,'Update',json.dumps(update_values(self.APPS[1])))),None,0,0,None,
                lambda c,r:results.append(r))
            self.drain()
            self.assertFalse(results,'second request waits instead of being refused')
            self.running[0][1](Result(value=None));self.drain()
            self.running[1][1](Result(value=None));self.drain()
        self.assertTrue(wire.loads(results[0].unpack()[0])['ok'])

class PermissionState(unittest.TestCase):
    def test_host_snapshot_reads_authoritative_approvals(self):
        broker=host.Broker.__new__(host.Broker)
        broker.installer=mock.Mock()
        broker.installer.installed.side_effect=lambda cb,*a,**k:cb(Result(value=()))
        results=[]
        with mock.patch.object(policy,'load',return_value={'approved':['host-consent']}):
            broker._dispatch(':1.7','Installed',{'refresh':False},results.append,lambda _:None,Gio.Cancellable())
        self.assertEqual(results[0].value,{'records':(),'approved':['host-consent']})
    def test_client_uses_host_consent_snapshot_and_refuses_invalid_shape(self):
        transport=mock.Mock();installed=Installation(transport);results=[]
        installed.installed(results.append)
        callback=transport.call.call_args.args[2]
        callback(Result(value={'records':(),'approved':['host-consent']}))
        self.assertEqual(installed.permission_state,{'approved':['host-consent']})
        self.assertEqual(results[-1].value,())
        callback(Result(value={'records':(),'approved':[True]}))
        self.assertFalse(results[-1].ok)
        self.assertEqual(installed.permission_state,{'approved':['host-consent']})

class Transactions(unittest.TestCase):
    def setUp(self):
        self.b=host.Broker.__new__(host.Broker);self.b.reviews={};self.b.firmware=None
        self.b.catalogue=mock.Mock();self.b.installer=mock.Mock()
        self.results=[];self.cancel=Gio.Cancellable()
        self.record=InstalledApp(ID,'1',12,update_version='2',app=App(ID,'Notes',''),commit=OLD,update_commit=NEW,permission_changes=(GROW,))
        self.b.installer.installed.side_effect=lambda cb,cancel,**kw:cb(Result(value=(self.record,)))
    def update(self,approve=False,baseline=OLD):
        self.b._dispatch(':1.7','Update',{'app_id':ID,'commit':NEW,'baseline':baseline,'approve':approve},
                         self.results.append,lambda _:None,self.cancel)
    def test_background_cannot_approve_permission_expansion(self):
        with mock.patch.object(policy,'load',return_value={}):self.update()
        self.assertFalse(self.results[0].ok);self.b.installer.update.assert_not_called()
        self.assertEqual(self.b.installer.installed.call_args.kwargs,{'force':True})
    def test_reviewed_exact_target_and_baseline_can_update(self):
        with mock.patch.object(policy,'approve') as approve:self.update(True)
        self.assertEqual(approve.call_args.args[0].commit,NEW)
        self.assertEqual(approve.call_args.args[0].installed_commit,OLD)
        self.assertEqual(self.b.installer.update.call_args.kwargs,{'expected_commit':NEW,'expected_installed_commit':OLD})
    def test_changed_installed_baseline_refused_without_consent(self):
        with mock.patch.object(policy,'approve') as approve:self.update(True,'c'*64)
        approve.assert_not_called();self.b.installer.update.assert_not_called();self.assertFalse(self.results[0].ok)
    def test_cancelled_permission_check_cannot_save_approval(self):
        self.cancel.cancel()
        with mock.patch.object(policy,'approve') as approve:self.update(True)
        approve.assert_not_called();self.b.installer.update.assert_not_called();self.assertFalse(self.results[0].ok)
    def test_async_permission_store_error_returns_terminal_failure(self):
        with mock.patch.object(policy,'approve',side_effect=OSError('disk full')):self.update(True)
        self.assertFalse(self.results[0].ok);self.b.installer.update.assert_not_called()
    def test_channel_review_bound_to_sender_app_and_expiry(self):
        for sender,app,age in [(':1.8',ID,0),(':1.7','catalog:tide',0),(':1.7',ID,121)]:
            self.b.reviews={RID:(sender,app,{},time.monotonic()-age)}
            with self.subTest(sender=sender,app=app,age=age),self.assertRaises(providers.ProviderError):
                self.b._dispatch(':1.7','SwitchChannel',{'app_id':ID,'review':RID},self.results.append,lambda _:None,self.cancel)
            self.b.installer.switch_channel.assert_not_called()
            self.assertIn(RID,self.b.reviews,'a refused foreign request cannot consume another connection’s review')
    def test_review_single_use_exact_saved_object(self):
        reviewed={'checked':object(),'branch':'nightly','permissions':(GROW,)}
        self.b.reviews={RID:(':1.7',ID,reviewed,time.monotonic())}
        args=(':1.7','SwitchChannel',{'app_id':ID,'review':RID},self.results.append,lambda _:None,self.cancel)
        self.b._dispatch(*args)
        self.assertIs(self.b.installer.switch_channel.call_args.args[1],reviewed)
        with self.assertRaises(providers.ProviderError):self.b._dispatch(*args)
    def test_explicit_fresh_update_cannot_be_satisfied_by_old_cache_on_network_failure(self):
        p=native.NativeInstallation();p._updates={'old':{}};p._updates_checked=time.monotonic()
        with mock.patch.object(p,'_check_updates',side_effect=OSError('offline')):
            self.assertEqual(p._pending_updates(),{'old':{}})
            with self.assertRaises(providers.ProviderError):p._pending_updates(force=True)
        self.assertEqual(p._updates,{'old':{}},'working prior discovery survives failed refresh')


class LifecycleBoundary(Admission):
    def setUp(self):
        super().setUp()
        self.b.lifecycle_ids={}
        self.values={'event':'shown','detail':'','seconds':1.25,'window':'DepotWindow'}
    def send(self,**kwargs):
        return self.call(op='Lifecycle',values=self.values,**kwargs)
    def test_valid_fixed_lifecycle_dto(self):
        self.assertEqual(wire.request('Lifecycle',json.dumps(self.values)),self.values)
    def test_lifecycle_rejects_unknown_fields_and_events(self):
        for value in ({**self.values,'MESSAGE_ID':'arbitrary'}, {**self.values,'event':'exec'},
                      {**self.values,'window':'OtherWindow'}, {**self.values,'detail':'x'*513}):
            with self.subTest(value=value),self.assertRaises(ValueError):
                wire.request('Lifecycle',json.dumps(value))
    def test_duration_rejects_coercion_nonfinite_and_unbounded(self):
        for seconds in (True,1,'1',-0.1,600.1,float('nan'),float('inf')):
            with self.subTest(seconds=seconds),self.assertRaises(ValueError):
                wire.request('Lifecycle',json.dumps({**self.values,'seconds':seconds}))
    def test_foreign_signed_app_cannot_inject_event(self):
        inv=self.send(app='org.projectluma.Notes')
        inv.return_dbus_error.assert_called_once()
        self.b._dispatch.assert_not_called();self.assertFalse(self.b.lifecycle_ids)
    def test_authentication_failure_cannot_emit_event(self):
        with mock.patch.object(host,'authenticate',side_effect=PermissionError('untrusted')):
            inv=mock.Mock()
            self.b._call(self.connection,':1.7','',host.BUS,'Request',
                GLib.Variant('(sss)',(RID,'Lifecycle',json.dumps(self.values))),inv)
        inv.return_dbus_error.assert_called_once();self.b._dispatch.assert_not_called()
    def test_replay_refused_after_terminal_completion(self):
        self.send();done=self.b._dispatch.call_args.args[3];done(Result())
        inv=self.send();inv.return_dbus_error.assert_called_once()
        self.assertEqual(self.b._dispatch.call_count,1)
    def test_capacity_refusal_does_not_reserve_unwatched_sender(self):
        self.b.jobs={(f':1.{i}', f'{i:032x}'): {'mutation':False} for i in range(4)}
        inv=self.send()
        inv.return_dbus_error.assert_called_once()
        self.assertFalse(self.b.lifecycle_ids)
        self.b._watch.assert_not_called()
    def test_sender_budget_is_bounded_after_completed_events(self):
        self.b.lifecycle_ids[':1.7']={f'{i:032x}' for i in range(64)}
        inv=self.send(rid='f'*32);inv.return_dbus_error.assert_called_once()
        self.b._dispatch.assert_not_called()
    def test_dispatch_uses_fixed_native_emitter_and_bounded_fields(self):
        from luma_installer import depot_errors
        results=[]
        with mock.patch.object(depot_errors,'lifecycle') as emit:
            host.Broker._dispatch(self.b,':1.7','Lifecycle',self.values,results.append,
                                  lambda _:None,Gio.Cancellable())
        emit.assert_called_once_with('shown',detail='',seconds=1.25,window='DepotWindow')
        self.assertTrue(results[0].ok)
    def test_portable_events_use_existing_async_authenticated_transport(self):
        from luma_installer import depot_errors
        client=mock.Mock()
        with mock.patch.object(depot_errors.os.path,'isfile',return_value=True), \
             mock.patch.object(depot_errors,'_lifecycle_client',client):
            depot_errors.lifecycle('shown',seconds=1.25,window='DepotWindow')
        client.call.assert_called_once()
        self.assertEqual(client.call.call_args.args[:2],('Lifecycle',self.values))
    def test_portable_arbitrary_extra_cannot_reach_transport(self):
        from luma_installer import depot_errors
        client=mock.Mock()
        with mock.patch.object(depot_errors.os.path,'isfile',return_value=True), \
             mock.patch.object(depot_errors,'_lifecycle_client',client), mock.patch.object(depot_errors.sys,'stderr'):
            depot_errors.lifecycle('shown',MESSAGE_ID='caller')
        client.call.assert_not_called()

if __name__=='__main__':unittest.main(verbosity=2)
