# SPDX-License-Identifier: Apache-2.0
"""Signed Depot UI adapter to existing native transaction owners.

This is an unprivileged, D-Bus activated per-user service, not a root write API.
App/ref/catalogue trust, libflatpak/polkit, permission comparison and firmware
safety remain with their maintained owners. OS operations go directly from
Depot to Update1 so polkit continues to see the original sandbox subject.
"""
import time
import uuid
from gi.repository import Gio, GLib
from luma_depot.host_wire import BUS, OBJECT, TOKEN, MUTATIONS, request, dumps
from luma_depot.providers import Result, ProviderError, run_async
from .app_data_broker import authenticate

XML = '''<node><interface name="org.projectluma.DepotHost1">
<method name="Request"><arg type="s" name="request_id" direction="in"/>
<arg type="s" name="operation" direction="in"/><arg type="s" name="parameters" direction="in"/>
<arg type="s" name="result" direction="out"/></method>
<method name="Cancel"><arg type="s" name="request_id" direction="in"/></method>
<signal name="Progress"><arg type="s" name="request_id"/><arg type="s" name="progress"/></signal>
<signal name="FirmwareState"><arg type="s" name="state"/></signal>
</interface></node>'''

class Broker:
    def __init__(self):
        from luma_depot.native import NativeCatalogue, NativeInstallation
        self.catalogue, self.installer = NativeCatalogue(), NativeInstallation()
        self.loop = GLib.MainLoop()
        self.bus = None
        self.jobs = {}
        self.watches = {}
        self.reviews = {}
        self.lifecycle_ids = {}
        self.firmware = None
        self.firmware_watchers = set()
        self.last_request = time.monotonic()
        self.owner = Gio.bus_own_name(Gio.BusType.SESSION, BUS, Gio.BusNameOwnerFlags.NONE,
                                     self._acquired, None, lambda *_: self.loop.quit())
        self.idle = GLib.timeout_add_seconds(30, self._idle)

    def _idle(self):
        if self.jobs or (self.firmware and (self.firmware.installing or self.firmware.checking or self.firmware.refreshing)):
            return GLib.SOURCE_CONTINUE
        if time.monotonic() - self.last_request < 60: return GLib.SOURCE_CONTINUE
        self.loop.quit()
        return GLib.SOURCE_REMOVE

    def _acquired(self, connection, *_):
        self.bus = connection
        connection.register_object(OBJECT, Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0], self._call, None, None)

    def _watch(self, sender):
        if sender in self.watches: return
        def gone(*_):
            for key,job in list(self.jobs.items()):
                if key[0] == sender: job['cancel'].cancel(); job['vanished'] = True
            self.reviews = {k:v for k,v in self.reviews.items() if v[0] != sender}
            self.firmware_watchers.discard(sender)
            self.lifecycle_ids.pop(sender, None)
            watch = self.watches.pop(sender, 0)
            if watch: Gio.bus_unwatch_name(watch)
        self.watches[sender] = Gio.bus_watch_name_on_connection(self.bus, sender,
            Gio.BusNameWatcherFlags.NONE, lambda *_: None, gone)

    def _call(self, connection, sender, _path, _iface, method, args, invocation):
        if method == 'Cancel':
            rid, = args.unpack()
            job = self.jobs.get((sender,rid))
            if job is None:
                invocation.return_dbus_error(BUS+'.Refused', 'No transaction belongs to this connection.')
            else:
                job['cancel'].cancel()
                invocation.return_value(GLib.Variant('()', ()))
            return
        try:
            if method != 'Request': raise ValueError('Unsupported Depot method.')
            rid, operation, text = args.unpack()
            if not TOKEN.fullmatch(rid): raise ValueError('Invalid request identity.')
            values = request(operation, text)
            if authenticate(connection, sender) != 'org.projectluma.Depot':
                raise PermissionError('Only the installed signed Depot application may use this service.')
            if (sender,rid) in self.jobs: raise ValueError('Duplicate request identity.')
            if len(self.jobs) >= 4 or (operation in MUTATIONS and any(j['mutation'] for j in self.jobs.values())):
                raise ValueError('Depot is already working. Try again when the current operation finishes.')
            if operation in MUTATIONS and self.firmware and self.firmware.installing:
                raise ValueError('A hardware update is already running.')
            if operation == 'Lifecycle':
                seen = self.lifecycle_ids.setdefault(sender, set())
                if rid in seen or len(seen) >= 64:
                    raise ValueError('Duplicate or excessive Depot lifecycle events.')
                seen.add(rid)
        except Exception as error:
            invocation.return_dbus_error(BUS+'.Refused', str(error)[:512]); return
        self.last_request = time.monotonic()
        self._watch(sender)
        cancel = Gio.Cancellable()
        key = (sender,rid)
        self.jobs[key] = {'cancel':cancel, 'mutation': operation in MUTATIONS, 'vanished':False,
                          'progress':None, 'timer':0}
        def finish(result):
            job = self.jobs.pop(key, None)
            if job is None: return
            if job['timer']: GLib.source_remove(job['timer'])
            if job['vanished']: return
            try:
                if result.error:
                    payload={'ok':False,'error':str(result.error)[:512],
                        'hint':result.error.hint[:512], 'detail':result.error.detail[:4096]}
                else: payload={'ok':True,'value':result.value}
                invocation.return_value(GLib.Variant('(s)', (dumps(payload),)))
            except Exception:
                invocation.return_dbus_error(BUS+'.Refused', 'Depot could not produce a bounded result. Refresh to inspect the actual installed state.')
        # A cancelled provider's actual worker must still release its reservation.
        # Suppressing a UI callback cannot be treated as a terminal transaction.
        finish._deliver_cancelled = True
        def progress(value):
            job = self.jobs.get(key)
            if not job or job['vanished']: return
            job['progress'] = value
            if job['timer']: return
            def flush():
                job['timer']=0
                if key in self.jobs and not job['vanished']:
                    try: self.bus.emit_signal(sender,OBJECT,BUS,'Progress',GLib.Variant('(ss)',(rid,dumps(job['progress']))))
                    except Exception: pass
                return GLib.SOURCE_REMOVE
            job['timer']=GLib.timeout_add(100,flush)
        try: self._dispatch(sender, operation, values, finish, progress, cancel)
        except Exception as error: finish(Result(error=ProviderError(str(error))))

    def _app(self, app_id):
        # The card is resolved anew from verified host discovery. A sandbox
        # cannot provide the package name, repository, file, command or URL.
        found = (self.catalogue._last or self.catalogue.snapshot()).find(app_id)
        if found is None: raise ProviderError('This application is no longer available. Refresh Depot.')
        return found

    def _dispatch(self, sender, operation, v, done, progress, cancel):
        from luma_depot import system_tools
        from . import depot_counting
        def guarded(callback):
            def deliver(result):
                try: callback(result)
                except Exception as error: done(Result(error=ProviderError(str(error))))
            deliver._deliver_cancelled = True
            return deliver
        if operation == 'Lifecycle':
            from .depot_errors import lifecycle
            extra = {'window':v['window']} if v['window'] else {}
            lifecycle(v['event'], detail=v['detail'], seconds=v['seconds'], **extra)
            done(Result())
        elif operation == 'Catalogue':
            if v['refresh']: self.catalogue.request_refresh()
            self.catalogue.load_catalogue(done,cancel)
        elif operation == 'Installed':
            def installed(result):
                if not result.ok: done(result); return
                if cancel.is_cancelled(): done(Result(error=ProviderError('Cancelled'))); return
                from . import depot_autoupdate
                # Permission consent has one authoritative native owner. UI
                # history/notification state is not permission authority.
                done(Result(value={'records':result.value,
                    'approved':depot_autoupdate.load().get('approved', [])}))
            self.installer.installed(guarded(installed),cancel,force=v['refresh'])
        elif operation == 'Permissions':
            def permissions(r):
                if not r.ok: done(r)
                elif cancel.is_cancelled(): done(Result(error=ProviderError('Cancelled')))
                else: self.catalogue.permissions(r.value,done,cancel)
            run_async(lambda:self._app(v['app_id']),guarded(permissions))
        elif operation == 'FreeBytes': run_async(self.installer.free_bytes,done)
        elif operation in ('Launch','ReviewRemoval','Install','SystemChange'):
            def resolved(r):
                if not r.ok: done(r); return
                if cancel.is_cancelled(): done(Result(error=ProviderError('Cancelled'))); return
                app=r.value
                if operation == 'Launch':
                    self.installer.launch(app.app_id,Gio.AppLaunchContext()); done(Result())
                elif operation == 'ReviewRemoval': self.installer.review_removal(app.app_id); done(Result())
                elif operation == 'Install': self.installer.install(app,progress,done,cancel)
                else: self.installer.system_change(app,v['action'],progress,done,cancel)
            run_async(lambda:self._app(v['app_id']),guarded(resolved))
        elif operation == 'Update':
            # Recompute the permission diff on the host. A bulk/background
            # request is never permission consent, even if its UI cache races.
            def checked(r):
                if cancel.is_cancelled(): done(Result(error=ProviderError('Cancelled'))); return
                if not r.ok: done(r); return
                record=next((x for x in r.value if x.app_id==v['app_id']),None)
                if record is None or record.commit!=v['baseline'] or record.update_commit!=v['commit']:
                    done(Result(error=ProviderError('This update changed. Refresh and review it again.'))); return
                from . import depot_autoupdate
                pending=depot_autoupdate.Pending(record.app_id, record.app.name if record.app else record.app_id,
                    record.update_version, any(p.change in ('added','widened') for p in record.permission_changes),
                    record.update_commit,record.commit)
                if pending.widens:
                    if v['approve']: depot_autoupdate.approve(pending)
                    elif depot_autoupdate.is_held(pending,depot_autoupdate.load()):
                        done(Result(error=ProviderError('Review the new permissions in Depot before updating.'))); return
                self.installer.update(v['app_id'],progress,done,cancel,
                    expected_commit=v['commit'],expected_installed_commit=v['baseline'])
            self.installer.installed(guarded(checked),cancel,force=True)
        elif operation == 'ReviewChannel':
            def reviewed(r):
                if cancel.is_cancelled(): done(Result(error=ProviderError('Cancelled'))); return
                if not r.ok: done(r); return
                self.reviews={k:x for k,x in self.reviews.items() if time.monotonic()-x[3]<120}
                if len(self.reviews)>=16: done(Result(error=ProviderError('Too many pending channel reviews.'))); return
                token=uuid.uuid4().hex
                self.reviews[token]=(sender,v['app_id'],r.value,time.monotonic())
                done(Result(value={'review':token,'branch':r.value['branch'],'permissions':r.value['permissions']}))
            self.installer.review_channel(v['app_id'],v['branch'],guarded(reviewed),cancel)
        elif operation == 'SwitchChannel':
            checked=self.reviews.get(v['review'])
            if checked is None or checked[:2]!=(sender,v['app_id']) or time.monotonic()-checked[3]>=120:
                raise ProviderError('Review this app’s channel again.')
            self.reviews.pop(v['review'])
            self.installer.switch_channel(v['app_id'],checked[2],progress,done,cancel)
        elif operation == 'Revert': self.installer.revert(v['app_id'],v['commit'],progress,done,cancel)
        elif operation == 'Remove': self.installer.remove(v['app_id'],keep_data=v['keep_data'],callback=done,cancellable=cancel)
        elif operation == 'SystemTools': run_async(system_tools.load,done)
        elif operation == 'RemoveSystemTool':
            def remove():
                if v['name'] not in {x.name for x in system_tools.load()}:
                    raise ProviderError('Only a system tool you added may be removed here.')
                return system_tools.remove(v['name'])
            run_async(remove,done)
        elif operation == 'GetSettings':
            from dataclasses import asdict
            done(Result(value=asdict(depot_counting.load_settings())))
        elif operation == 'SetSettings':
            depot_counting.save_settings(depot_counting.Settings(**v)); done(Result())
        elif operation in ('ReviewsEnrolled','Reviews','SaveReview','DeleteReview'):
            def reviews():
                from .depot_reviews import ReviewsClient, is_enrolled
                if operation == 'ReviewsEnrolled': return is_enrolled()
                app = self._app('catalog:'+v['slug'])
                if app.slug != v['slug']: raise ProviderError('This app has no verified reviews listing.')
                client=ReviewsClient()
                if operation=='Reviews': return client.reviews(v['slug'],v['cursor'])
                if operation=='DeleteReview': return client.delete(v['slug'])
                from luma_depot.native import installed_flatpak_refs
                pair=installed_flatpak_refs().get(app.flatpak_id)
                version=pair[1].get_appdata_version() or '' if pair else ''
                return client.save(v['slug'],rating=v['rating'],title=v['title'],body=v['body'],
                    version=version, installed_on_luma=bool(pair or app.system_state=='installed'))
            run_async(reviews,done)
        elif operation == 'FirmwareBlocklistRefresh':
            def refresh():
                import urllib.request
                from . import depot_firmware
                from .depot_catalog import public_key
                with urllib.request.urlopen(depot_firmware.URL,timeout=10) as stream:
                    content=stream.read(depot_firmware.MAX_BYTES+1)
                with urllib.request.urlopen(depot_firmware.URL+'.minisig',timeout=10) as stream:
                    signature=stream.read(4096)
                depot_firmware.remember(content,signature,public_key())
            run_async(refresh,done)
        elif operation.startswith('Firmware'):
            self._firmware(sender,operation,v,done)
        else: raise ProviderError('Unsupported Depot operation.')

    def _firmware_snapshot(self):
        firmware=self.firmware
        return {key:getattr(firmware,key) for key in ('updates','available','problem','on_battery',
            'installing','progress','phase','failures','refreshing','refresh_error','error','loaded','checking','fwupd_build')} | {
            'offers':{u.device_id:firmware.offer(u) for u in firmware.updates}}

    def _firmware_changed(self):
        if not self.firmware: return
        try: text=dumps(self._firmware_snapshot())
        except Exception: return
        for sender in tuple(self.firmware_watchers):
            try:self.bus.emit_signal(sender,OBJECT,BUS,'FirmwareState',GLib.Variant('(s)',(text,)))
            except GLib.Error: pass

    def _firmware(self,sender,operation,v,done):
        if operation=='FirmwareUnsubscribe':
            self.firmware_watchers.discard(sender); done(Result()); return
        if self.firmware is None:
            from luma_depot.system_updates import Firmware
            self.firmware=Firmware(self._firmware_changed)
        self.firmware_watchers.add(sender)
        if operation=='FirmwareLoad': self.firmware.load()
        elif operation=='FirmwareRefresh': self.firmware.refresh()
        elif operation=='FirmwareInstall':
            update=next((u for u in self.firmware.updates if (u.device_id,u.version)==(v['device_id'],v['version'])),None)
            if update is None or self.firmware.offer(update).state!='ready' or self.firmware.installing:
                raise ProviderError('Hardware update changed or its safety requirements are not met. Check again.')
            self.firmware.install(update)
        done(Result(value=self._firmware_snapshot()))

    def run(self):
        try:self.loop.run()
        finally:
            for job in self.jobs.values():job['cancel'].cancel()
            for watch in self.watches.values():Gio.bus_unwatch_name(watch)
            Gio.bus_unown_name(self.owner)

if __name__=='__main__': Broker().run()
