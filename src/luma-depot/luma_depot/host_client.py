# SPDX-License-Identifier: Apache-2.0
"""Depot's sandbox uses narrow native-owner methods, never host file access."""
import json
import uuid
from gi.repository import Gio, GLib
from .host_wire import BUS, OBJECT, dumps, loads, request
from .providers import Result, ProviderError

class Client:
    def __init__(self):
        self.connection=Gio.bus_get_sync(Gio.BusType.SESSION,None)
        self.pending={}
        self.queue=[]
        self.inflight=set()
        self.closed=False
        self.firmware=None
        self.subscription=self.connection.signal_subscribe(BUS,BUS,None,OBJECT,None,
            Gio.DBusSignalFlags.NONE,self._signal)

    def _signal(self,_connection,_sender,_path,_interface,name,parameters):
        if self.closed:return
        try:
            if name=='Progress':
                rid,text=parameters.unpack()
                progress=self.pending.get(rid,{}).get('progress')
                if progress:progress(loads(text))
            elif name=='FirmwareState' and self.firmware:
                text,=parameters.unpack()
                self.firmware._state(loads(text))
        except (ValueError,TypeError,KeyError):return

    def call(self,operation,values,callback,progress=None,cancellable=None):
        if self.closed:
            GLib.idle_add(callback,Result(error=ProviderError('The Depot host connection is closed.')));return
        rid=uuid.uuid4().hex
        text=json.dumps(values,separators=(',',':'),allow_nan=False)
        request(operation,text) # Same strict contract on both sides.
        if len(self.pending)>=16:
            GLib.idle_add(callback,Result(error=ProviderError('Depot is already checking applications. Try again shortly.')));return
        job={'progress':progress,'cancellable':cancellable,'handler':0,
             'operation':operation,'text':text,'callback':callback}
        self.pending[rid]=job
        self.queue.append(rid)
        if cancellable:
            job['handler']=cancellable.connect(lambda *_: GLib.idle_add(self.cancel,rid))
        if cancellable and cancellable.is_cancelled():self.cancel(rid)
        self._pump()

    def _pump(self):
        # A cold window asks for catalogue, inventory, free space, reviews and
        # tools together. Bound transport concurrency below the native owner's
        # four-job admission limit; optional reads cannot crowd out its own UI.
        while not self.closed and self.queue and len(self.inflight)<2:
            rid=self.queue.pop(0)
            job=self.pending.get(rid)
            if job is None:continue
            self.inflight.add(rid)
            self._dispatch(rid,job)

    def _dispatch(self,rid,job):
        callback,cancellable=job['callback'],job['cancellable']
        def finished(connection,result):
            job=self.pending.pop(rid,None)
            self.inflight.discard(rid)
            if job and job['handler']:cancellable.disconnect(job['handler'])
            try:
                text,=connection.call_finish(result).unpack()
                payload=loads(text)
                if type(payload) is not dict or type(payload.get('ok')) is not bool:
                    raise ValueError('The Depot host returned an invalid result.')
                value=(Result(value=payload.get('value')) if payload['ok'] else
                    Result(error=ProviderError(payload.get('error','Depot could not complete this operation.'),
                         hint=payload.get('hint',''),detail=payload.get('detail',''))))
            except (GLib.Error,ValueError,TypeError,KeyError) as error:
                value=Result(error=ProviderError('Depot could not reach its application service.',
                     hint='Retry or update Luma’s application service. Your installed apps are preserved.',detail=str(error)))
            try:
                if not self.closed:callback(value)
            finally:self._pump()
        # The actual transaction terminal reply determines completion. Do not
        # cancel the transport and lose a late reply after libflatpak commits.
        self.connection.call(BUS,OBJECT,BUS,'Request',GLib.Variant('(sss)',(rid,job['operation'],job['text'])),
            GLib.VariantType.new('(s)'),Gio.DBusCallFlags.NONE,30*60*1000,None,finished)

    def cancel(self,rid):
        job=self.pending.get(rid)
        if job is None:return GLib.SOURCE_REMOVE
        if rid not in self.inflight:
            self.queue.remove(rid)
            self.pending.pop(rid)
            if job['handler']:job['cancellable'].disconnect(job['handler'])
            def cancelled():
                if not self.closed:job['callback'](Result(error=ProviderError('Cancelled')))
                return GLib.SOURCE_REMOVE
            GLib.idle_add(cancelled)
            return GLib.SOURCE_REMOVE
        self.connection.call(BUS,OBJECT,BUS,'Cancel',GLib.Variant('(s)',(rid,)),None,
            Gio.DBusCallFlags.NONE,5000,None,None)
        return GLib.SOURCE_REMOVE

    def sync(self,operation,values):
        text=json.dumps(values,separators=(',',':'),allow_nan=False);request(operation,text)
        reply=self.connection.call_sync(BUS,OBJECT,BUS,'Request',
            GLib.Variant('(sss)',(uuid.uuid4().hex,operation,text)),GLib.VariantType.new('(s)'),
            Gio.DBusCallFlags.NONE,30000 if operation in ('Reviews','SaveReview','DeleteReview') else 5000,None)
        payload=loads(reply.unpack()[0])
        if not payload['ok']:raise OSError(payload.get('error','Depot host request failed.'))
        return payload.get('value')

    def close(self):
        if self.closed:return
        self.closed=True
        for rid in tuple(self.pending):self.cancel(rid)
        self.connection.call(BUS,OBJECT,BUS,'Request',
            GLib.Variant('(sss)',(uuid.uuid4().hex,'FirmwareUnsubscribe','{}')),None,
            Gio.DBusCallFlags.NONE,5000,None,None)
        self.connection.signal_unsubscribe(self.subscription)

class Catalogue:
    def __init__(self,client):self.client=client;self._cache=None;self._refresh=False
    def request_refresh(self):self._refresh=True
    def load_catalogue(self,callback,cancellable=None):
        def done(r):
            if r.ok:self._cache=r.value
            callback(r)
        refresh,self._refresh=self._refresh,False
        self.client.call('Catalogue',{'refresh':refresh},done,cancellable=cancellable)
    def search(self,query,callback,cancellable=None):
        def done(r):
            if r.ok:
                text=query.casefold()
                r=Result(value=tuple(a for a in r.value.apps if not a.unlisted
                     and text in (a.name+' '+a.summary+' '+a.developer).casefold()))
            callback(r)
        self.load_catalogue(done,cancellable)
    def app(self,app_id,callback,cancellable=None):
        self.load_catalogue(lambda r:callback(Result(value=r.value.find(app_id)) if r.ok else r),cancellable)
    def permissions(self,app,callback,cancellable=None):
        self.client.call('Permissions',{'app_id':app.app_id},callback,cancellable=cancellable)

class Installation:
    requires_permission_approval=True
    delivers_cancelled_terminal=True
    def __init__(self,client):
        self.client=client;self.force_update_check=False;self.available_bytes=0
        self.permission_state={'approved':[]}
    def installed(self,callback,cancellable=None):
        refresh,self.force_update_check=self.force_update_check,False
        self.client.call('FreeBytes',{},lambda r:setattr(self,'available_bytes',r.value) if r.ok else None)
        def installed(result):
            if result.ok:
                value=result.value
                if (type(value) is not dict or set(value)!={'records','approved'}
                        or type(value['records']) is not tuple or type(value['approved']) is not list
                        or len(value['approved'])>200 or not all(type(x) is str for x in value['approved'])):
                    result=Result(error=ProviderError('Depot could not verify its installed application state.'))
                else:
                    self.permission_state={'approved':list(value['approved'])}
                    result=Result(value=value['records'])
            callback(result)
        self.client.call('Installed',{'refresh':refresh},installed,cancellable=cancellable)
    def free_bytes(self):return self.available_bytes
    def install(self,app,on_progress,callback,cancellable=None):
        self.client.call('Install',{'app_id':app.app_id},callback,on_progress,cancellable)
    def update(self,app_id,on_progress,callback,cancellable=None,*,expected_commit='',expected_installed_commit='',permission_approval=False):
        self.client.call('Update',{'app_id':app_id,'commit':expected_commit,'baseline':expected_installed_commit,
              'approve':bool(permission_approval)},callback,on_progress,cancellable)
    def review_channel(self,app_id,branch,callback,cancellable=None):
        self.client.call('ReviewChannel',{'app_id':app_id,'branch':branch},callback,cancellable=cancellable)
    def switch_channel(self,app_id,reviewed,on_progress,callback,cancellable=None):
        self.client.call('SwitchChannel',{'app_id':app_id,'review':reviewed['review']},callback,on_progress,cancellable)
    def revert(self,app_id,commit,on_progress,callback,cancellable=None):
        self.client.call('Revert',{'app_id':app_id,'commit':commit},callback,on_progress,cancellable)
    def remove(self,app_id,*,keep_data,callback,cancellable=None):
        self.client.call('Remove',{'app_id':app_id,'keep_data':keep_data},callback,cancellable=cancellable)
    def system_change(self,app,action,on_progress,callback,cancellable=None):
        self.client.call('SystemChange',{'app_id':app.app_id,'action':action},callback,on_progress,cancellable)
    def launch(self,app_id,_context):self._intent('Launch',app_id)
    def review_removal(self,app_id):self._intent('ReviewRemoval',app_id)
    def _intent(self,operation,app_id):
        self.client.sync(operation,{'app_id':app_id})
    def can_remove(self,app_id):return False # Managed records carry the actual host removal state.

class Firmware:
    def __init__(self,client,on_change):
        self.client=client;self.on_change=on_change;client.firmware=self
        self.updates=();self.available=None;self.problem='';self.on_battery=False
        self.installing='';self.progress=0.;self.phase='';self.failures={}
        self.refreshing=False;self.refresh_error='';self.error='';self.loaded=False
        self.checking=False;self.fwupd_build='';self.offers={}
    def _state(self,state):
        if type(state) is not dict:return
        for key in ('updates','available','problem','on_battery','installing','progress','phase',
                    'failures','refreshing','refresh_error','error','loaded','checking','fwupd_build','offers'):
            if key in state:setattr(self,key,state[key])
        self.on_change()
    def _done(self,result):
        if result.ok:self._state(result.value)
        else:self.problem=result.error.hint or str(result.error);self.available=False;self.on_change()
    def load(self):self.client.call('FirmwareLoad',{},self._done)
    def refresh(self):self.client.call('FirmwareRefresh',{},self._done)
    def install(self,update):
        self.client.call('FirmwareInstall',{'device_id':update.device_id,'version':update.version},self._done)
    def offer(self,update):
        from .system_updates import Offer
        return self.offers.get(update.device_id,Offer('paused',reason='Check hardware updates again.'))
    def attention(self):
        return tuple(u for u in self.updates if self.offer(u).state in ('ready','unmet') or u.device_id in self.failures)
    def key(self,update):
        from luma_installer.depot_firmware_safety import attempt_key
        return attempt_key(update.guids[0] if update.guids else update.device_id,update.plugin,
                           update.current_version,update.version,self.fwupd_build)

def sandboxed():
    from luma_appkit.app_updates import running_flatpak
    return running_flatpak('org.projectluma.Depot')

def load_settings(client):
    from luma_installer.depot_counting import Settings
    value=client.sync('GetSettings',{})
    if set(value)!={'install_events','countme','app_updates'} or any(type(x) is not bool for x in value.values()):
        raise OSError('Depot settings could not be checked.')
    return Settings(**value)

def save_settings(client,settings):
    from dataclasses import asdict
    client.sync('SetSettings',asdict(settings))

class ReviewsClient:
    """The existing review client shape, with enrollment kept on the host."""
    def __init__(self,client,on_change):
        self.client=client;self.enrolled=False
        # Used only for display; this never authorizes a URL or bearer token.
        from luma_installer.depot_reviews import HUB_URL
        self.hub=HUB_URL
        def loaded(r):
            self.enrolled=r.ok and r.value is True
            on_change()
        client.call('ReviewsEnrolled',{},loaded)
    def is_enrolled(self):return self.enrolled
    def _call(self,operation,values):
        from luma_installer.depot_reviews import ReviewsError
        try:return self.client.sync(operation,values)
        except (OSError,GLib.Error,ValueError) as error:raise ReviewsError(str(error)) from error
    def reviews(self,slug,cursor=''):return self._call('Reviews',{'slug':slug,'cursor':cursor})
    def save(self,slug,*,rating,title,body,version,installed_on_luma):
        return self._call('SaveReview',{'slug':slug,'rating':rating,'title':title,'body':body})
    def delete(self,slug):return self._call('DeleteReview',{'slug':slug})
