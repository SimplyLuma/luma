# SPDX-License-Identifier: Apache-2.0
# Independent source integration: actual GIO D-Bus service + actual GTK menu model.
# This is not a signed Flatpak installation or a sandbox identity/trust test.
import os,time,unittest
from unittest import mock
from gi.repository import Gio,GLib
from luma_appkit import app_updates as api

A,B='a'*64,'b'*64
XML='''<node>
<interface name="org.freedesktop.portal.Flatpak"><method name="CreateUpdateMonitor"><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method></interface>
<interface name="org.freedesktop.portal.Flatpak.UpdateMonitor"><method name="Close"/><method name="Update"><arg type="s" direction="in"/><arg type="a{sv}" direction="in"/></method><signal name="UpdateAvailable"><arg type="a{sv}"/></signal><signal name="Progress"><arg type="a{sv}"/></signal></interface>
</node>'''

def spin(predicate=lambda:False,seconds=.8):
    end=time.monotonic()+seconds
    context=GLib.MainContext.default()
    while time.monotonic()<end:
        while context.pending(): context.iteration(False)
        if predicate(): return True
        time.sleep(.002)
    return predicate()

class Portal:
    def __init__(self):
        self.connection=Gio.DBusConnection.new_for_address_sync(os.environ['DBUS_SESSION_BUS_ADDRESS'],
          Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT|Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,None,None)
        self.info=Gio.DBusNodeInfo.new_for_xml(XML)
        self.registration=self.connection.register_object(api.PATH,self.info.interfaces[0],self.method,None,None)
        self.monitors={};self.created=0;self.updates=0;self.delay=0;self.method_error=None;self.return_wrong=False
        self.hold_create=False;self.pending_create=None;self.hold_update=False;self.pending_update=None;self.closed_paths=[]
        self.acquire()
    def acquire(self):
        self.connection.call_sync('org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus','RequestName',GLib.Variant('(su)',(api.BUS,0)),None,Gio.DBusCallFlags.NONE,1000,None)
    def release(self):
        self.connection.call_sync('org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus','ReleaseName',GLib.Variant('(s)',(api.BUS,)),None,Gio.DBusCallFlags.NONE,1000,None)
    def method(self,connection,sender,path,interface,name,args,invocation):
        if name=='CreateUpdateMonitor':
            self.created+=1
            token=args.unpack()[0]['handle_token']
            handle=api.PREFIX+sender[1:].replace('.','_')+'/'+token
            def finish():
                rid=connection.register_object(handle,self.info.interfaces[1],self.method,None,None)
                self.monitors[handle]=(rid,sender)
                self.emit(handle,'UpdateAvailable',{'running-commit':A,'local-commit':A,'remote-commit':B})
                if self.return_wrong:
                    foreign=handle+'_alternate'
                    rid=connection.register_object(foreign,self.info.interfaces[1],self.method,None,None)
                    self.monitors[foreign]=(rid,sender)
                    invocation.return_value(GLib.Variant('(o)',(foreign,)))
                elif self.hold_create:self.pending_create=(invocation,handle)
                else:invocation.return_value(GLib.Variant('(o)',(handle,)))
                return GLib.SOURCE_REMOVE
            if self.delay: GLib.timeout_add(self.delay,finish)
            else: finish()
        elif name=='Update':
            self.updates+=1
            self.last_update_args=args.unpack()
            if self.hold_update:self.pending_update=invocation
            elif self.method_error: invocation.return_dbus_error(self.method_error,'controlled refusal')
            else: invocation.return_value(GLib.Variant('()',()))
        elif name=='Close':
            invocation.return_value(GLib.Variant('()',()))
            self.closed_paths.append(path)
            rid,_=self.monitors.pop(path)
            connection.unregister_object(rid)
    def finish_create(self):
        invocation,handle=self.pending_create;self.pending_create=None
        invocation.return_value(GLib.Variant('(o)',(handle,)))
    def finish_update_error(self):
        invocation=self.pending_update;self.pending_update=None
        invocation.return_dbus_error('org.freedesktop.DBus.Error.Failed','late controlled error')
    def emit(self,handle,name,info):
        if handle not in self.monitors:return
        self.connection.emit_signal(self.monitors[handle][1],handle,api.MONITOR,name,
            GLib.Variant('(a{sv})',({k:GLib.Variant('u' if type(v)is int else 's',v) for k,v in info.items()},)))
    def dispose(self):
        if self.pending_create:self.finish_create()
        if self.pending_update:self.finish_update_error()
        for rid,_ in self.monitors.values():self.connection.unregister_object(rid)
        self.monitors.clear()
        self.connection.unregister_object(self.registration)
        self.release();self.connection.close_sync(None)

class RealPortalBehavior(unittest.TestCase):
    def setUp(self):self.portal=Portal();self.client=api.AppUpdates('org.projectluma.Tide')
    def tearDown(self):self.client.close();spin(seconds=.05);self.portal.dispose()
    def ready(self):
        self.client.start()
        self.assertTrue(spin(lambda:self.client.state=='available'))
        self.assertEqual((self.client.running,self.client.local,self.client.remote),(A,A,B))
    def test_initial_signal_before_create_reply_and_exact_token_path(self):
        self.ready();self.assertIn(self.client.handle,self.portal.monitors)
    def test_update_acceptance_is_not_success_and_done_is_terminal(self):
        self.ready();self.assertTrue(self.client.install())
        self.assertTrue(spin(lambda:self.portal.updates==1))
        spin(seconds=.05);self.assertEqual(self.client.state,'installing')
        self.assertEqual(self.portal.last_update_args,('',{}))
        self.portal.emit(self.client.handle,'Progress',{'status':0,'progress':100})
        spin(seconds=.04);self.assertEqual(self.client.state,'installing')
        self.portal.emit(self.client.handle,'Progress',{'status':2})
        self.assertTrue(spin(lambda:self.client.state=='ready'))
    def test_empty_does_not_claim_installed_update(self):
        self.ready();self.client.install();spin(lambda:self.portal.updates==1)
        self.portal.emit(self.client.handle,'Progress',{'status':1})
        self.assertTrue(spin(lambda:self.client.state=='current'))
    def test_permission_widening_progress_goes_to_software_manager_review(self):
        self.ready();self.client.install();spin(lambda:self.portal.updates==1)
        self.portal.emit(self.client.handle,'Progress',{'status':3,'error':'org.freedesktop.DBus.Error.NotSupported'})
        self.assertTrue(spin(lambda:self.client.state=='review'))
    def test_persisted_user_denial_routes_to_software_manager(self):
        self.ready();self.client.install();spin(lambda:self.portal.updates==1)
        self.portal.emit(self.client.handle,'Progress',{'status':3,'error':'org.freedesktop.DBus.Error.AccessDenied'})
        self.assertTrue(spin(lambda:self.client.state=='review'),
            'persisted portal denial merely retries refused portal instead of offering software manager')
    def test_generic_failure_never_claims_ready(self):
        self.ready();self.client.install();spin(lambda:self.portal.updates==1)
        self.portal.emit(self.client.handle,'Progress',{'status':3,'error':'org.freedesktop.DBus.Error.Failed'})
        self.assertTrue(spin(lambda:self.client.state=='failed'))
    def test_permission_widening_method_error_goes_to_review(self):
        for error in ('org.freedesktop.DBus.Error.NotSupported','org.freedesktop.DBus.Error.AccessDenied'):
            with self.subTest(error=error):
                self.portal.method_error=error
                self.ready();self.client.install()
                self.assertTrue(spin(lambda:self.client.state=='review'))
                self.client.close();self.assertTrue(spin(lambda:not self.portal.monitors))
                self.client=api.AppUpdates('org.projectluma.Tide')
    def test_malformed_commits_do_not_replace_current_snapshot(self):
        self.ready();self.portal.emit(self.client.handle,'UpdateAvailable',{'running-commit':A,'local-commit':A,'remote-commit':'not-a-commit'})
        spin(seconds=.05);self.assertEqual(self.client.remote,B)
    def test_close_after_creation_withdraws_monitor(self):
        self.ready();self.client.close()
        self.assertTrue(spin(lambda:not self.portal.monitors))
    def test_close_during_delayed_create_does_not_leave_monitor(self):
        self.portal.delay=100;self.client.start()
        self.assertTrue(spin(lambda:self.portal.created==1))
        self.client.close();spin(seconds=.25)
        self.assertEqual(self.portal.monitors,{})
    def test_portal_disappearance_does_not_leave_installing_forever(self):
        self.ready();self.client.install();spin(lambda:self.portal.updates==1)
        self.portal.release()
        self.assertTrue(spin(lambda:self.client.state in ('unavailable','failed'),seconds=.4),
            'name owner disappeared but update still reports installing')
        self.portal.acquire()

    def test_mismatched_creation_reaps_only_generated_handle_and_falls_back(self):
        self.portal.return_wrong=True
        self.client.start()
        self.assertTrue(spin(lambda:self.portal.created==1))
        self.assertTrue(spin(lambda:self.client.state=='unavailable'))
        self.assertTrue(spin(lambda:self.client.handle in self.portal.closed_paths))
        self.assertIn(self.client.handle+'_alternate',self.portal.monitors)
        self.assertNotIn(self.client.handle+'_alternate',self.portal.closed_paths)
        self.assertEqual(self.client.state,'unavailable','mismatched handle leaves update menu disabled in checking')
    def test_delayed_creation_does_not_block_main_loop(self):
        self.portal.delay=100
        ticks=[]
        timer=GLib.timeout_add(5,lambda:(ticks.append(True) or GLib.SOURCE_CONTINUE))
        self.client.start()
        self.assertTrue(spin(lambda:self.portal.created==1))
        spin(seconds=.04)
        GLib.source_remove(timer)
        self.assertGreater(len(ticks),3)
        self.assertTrue(spin(lambda:self.client.state=='available'))

    def test_initial_signal_does_not_enable_before_exact_acknowledgement(self):
        self.portal.hold_create=True;self.client.start()
        self.assertTrue(spin(lambda:self.portal.pending_create is not None and self.client.remote==B))
        self.assertFalse(self.client.created);self.assertEqual(self.client.state,'checking')
        self.assertFalse(self.client.action_enabled());self.assertFalse(self.client.install())
        self.assertEqual(self.portal.updates,0)
        self.portal.finish_create();self.assertTrue(spin(lambda:self.client.state=='available'))
        self.assertTrue(self.client.created);self.assertTrue(self.client.action_enabled())
    def test_owner_replacement_rejects_late_old_creation_and_signals(self):
        self.portal.hold_create=True;self.client.start()
        self.assertTrue(spin(lambda:self.portal.pending_create is not None))
        old_handle=self.client.handle;self.portal.release()
        self.assertTrue(spin(lambda:self.client.state=='unavailable'))
        replacement=Portal()
        try:
            self.assertTrue(spin(lambda:self.client.state=='available'))
            new_handle=self.client.handle;self.assertNotEqual(new_handle,old_handle)
            self.portal.finish_create();self.assertTrue(spin(lambda:old_handle not in self.portal.monitors))
            self.portal.connection.emit_signal(self.client.connection.get_unique_name(),old_handle,api.MONITOR,'UpdateAvailable',
                GLib.Variant('(a{sv})',({'running-commit':GLib.Variant('s',A),'local-commit':GLib.Variant('s',B),'remote-commit':GLib.Variant('s',B)},)))
            spin(seconds=.05);self.assertEqual(self.client.state,'available')
            self.assertTrue(self.client.install());self.assertTrue(spin(lambda:replacement.updates==1))
            self.assertEqual(self.portal.updates,0)
            self.client.close();spin(seconds=.05)
        finally:replacement.dispose()
        self.portal.acquire()
    def test_terminal_progress_wins_over_late_method_error(self):
        self.portal.hold_update=True;self.ready();self.client.install()
        self.assertTrue(spin(lambda:self.portal.pending_update is not None))
        self.portal.emit(self.client.handle,'Progress',{'status':2})
        self.assertTrue(spin(lambda:self.client.state=='ready'))
        self.portal.finish_update_error();spin(seconds=.05);self.assertEqual(self.client.state,'ready')
        self.assertEqual(self.client.local,B)
    def test_owner_replacement_rejects_old_update_error(self):
        self.portal.hold_update=True;self.ready();self.client.install()
        self.assertTrue(spin(lambda:self.portal.pending_update is not None))
        self.portal.release();self.assertTrue(spin(lambda:self.client.state=='unavailable'))
        replacement=Portal()
        try:
            self.assertTrue(spin(lambda:self.client.state=='available'))
            self.portal.finish_update_error();spin(seconds=.05);self.assertEqual(self.client.state,'available')
            self.client.close();spin(seconds=.05)
        finally:replacement.dispose()
        self.portal.acquire()
    def test_oversized_signal_and_wrong_status_type_are_ignored(self):
        self.ready()
        self.portal.emit(self.client.handle,'UpdateAvailable',{'running-commit':A,'local-commit':B,'remote-commit':B,'extra':'x'*70000})
        spin(seconds=.05);self.assertEqual(self.client.state,'available');self.assertEqual(self.client.local,A)
        self.client.install();self.assertTrue(spin(lambda:self.portal.updates==1))
        self.portal.emit(self.client.handle,'Progress',{'status':'2'})
        spin(seconds=.05);self.assertEqual(self.client.state,'installing')
    def test_start_is_idempotent_and_shutdown_action_disabled(self):
        self.ready();self.client.start();spin(seconds=.05);self.assertEqual(self.portal.created,1)
        self.client.close();self.assertFalse(self.client.action_enabled());self.assertFalse(self.client.install())

class NativeWindowMenu(unittest.TestCase):
    def test_async_available_update_appears_in_existing_menu(self):
        import gi
        gi.require_version('Gtk','4.0');gi.require_version('Adw','1')
        from gi.repository import Gtk,Adw
        from luma_appkit.commands import CommandRegistry
        from luma_appkit.menus import Menu
        from luma_appkit.widgets import command_menu_model
        from luma_appkit.action_toast import Toast
        Gtk.init();Adw.init()
        application=Adw.Application(application_id='org.projectluma.UpdateReview')
        application.register(None)
        window=Adw.ApplicationWindow(application=application)
        controller=api.AppUpdates('org.projectluma.Tide')
        with mock.patch.object(api,'for_application',return_value=controller),mock.patch.object(Toast,'show'):
            registry=api.window_commands(window,CommandRegistry())
            menu=Menu(registry,keep_parent=True)
            menu_model=command_menu_model(registry,application)
            # AppWindow calls this immediately after installing its native menu.
            api.refresh_native_action(window)
            command=registry.get('luma.application.update')
            self.assertFalse(command.enabled())
            native_action=application.lookup_action('luma-application-update')
            self.assertFalse(native_action.get_enabled())
            window.present();self.assertTrue(spin(window.get_mapped))
            controller.created=True
            controller.running=controller.local=A;controller.remote=B
            controller._set('available')
            self.assertTrue(command.enabled())
            self.assertTrue(native_action.get_enabled())
            menu._refresh_state(menu)
            def labels(model):
                result=[]
                for index in range(model.get_n_items()):
                    label=model.get_item_attribute_value(index,'label',None)
                    if label:result.append(label.unpack())
                    for link in ('section','submenu'):
                        child=model.get_item_link(index,link)
                        if child:result.extend(labels(child))
                return result
            self.assertIn('App updates',labels(menu.get_menu_model()),'existing visible app menu never gained update row')
            self.assertIn('App updates',labels(menu_model),'application GMenu never gained update row')
        window.destroy();controller.close()
    def test_real_initial_signal_keeps_native_action_disabled_until_ack(self):
        import gi
        gi.require_version('Gtk','4.0');gi.require_version('Adw','1')
        from gi.repository import Gtk,Adw
        from luma_appkit.commands import CommandRegistry
        from luma_appkit.widgets import command_menu_model
        from luma_appkit.action_toast import Toast
        Gtk.init();Adw.init()
        portal=Portal();portal.hold_create=True
        app=Adw.Application(application_id='org.projectluma.UpdateAckReview')
        app.register(None);window=Adw.ApplicationWindow(application=app)
        controller=api.AppUpdates('org.projectluma.Tide')
        try:
            with mock.patch.object(api,'for_application',return_value=controller),mock.patch.object(Toast,'show'):
                registry=api.window_commands(window,CommandRegistry())
                window.commands=registry  # Actual AppWindow action routing contract.
                command_menu_model(registry,app);api.refresh_native_action(window)
                action=app.lookup_action('luma-application-update')
                window.present();self.assertTrue(spin(window.get_mapped))
                controller.start()
                self.assertTrue(spin(lambda:portal.pending_create is not None and controller.remote==B))
                self.assertEqual(controller.state,'checking');self.assertFalse(action.get_enabled())
                action.activate(None);spin(seconds=.03);self.assertEqual(portal.updates,0)
                portal.finish_create();self.assertTrue(spin(lambda:action.get_enabled()))
                action.activate(None);self.assertTrue(spin(lambda:portal.updates==1))
                self.assertEqual(controller.state,'installing');self.assertFalse(action.get_enabled())
                portal.emit(controller.handle,'Progress',{'status':2})
                self.assertTrue(spin(lambda:controller.state=='ready' and action.get_enabled()))
                controller.close();self.assertFalse(action.get_enabled())
        finally:
            window.destroy();controller.close();spin(seconds=.05);portal.dispose()

    def test_available_and_ready_for_same_commit_have_separate_notices(self):
        import gi
        gi.require_version('Gtk','4.0');gi.require_version('Adw','1')
        from gi.repository import Gtk,Adw
        from luma_appkit.commands import CommandRegistry
        from luma_appkit.action_toast import Toast
        Gtk.init();Adw.init()
        application=Adw.Application(application_id='org.projectluma.UpdateNoticeReview')
        application.register(None)
        window=Adw.ApplicationWindow(application=application)
        controller=api.AppUpdates('org.projectluma.Tide')
        with mock.patch.object(api,'for_application',return_value=controller),mock.patch.object(Toast,'show') as toast:
            api.window_commands(window,CommandRegistry())
            window.present()
            self.assertTrue(spin(window.get_mapped))
            controller.created=True
            controller.running=controller.local=A;controller.remote=B;controller._set('available')
            controller.local=B;controller._set('ready')
            self.assertEqual(toast.call_count,2,'available B suppressed ready B notice')
        window.destroy();controller.close()

    def test_notice_reconnects_when_window_is_realized_again(self):
        import gi
        gi.require_version('Gtk','4.0');gi.require_version('Adw','1')
        from gi.repository import Gtk,Adw
        from luma_appkit.commands import CommandRegistry
        from luma_appkit.action_toast import Toast
        Gtk.init();Adw.init()
        application=Adw.Application(application_id='org.projectluma.UpdateRemapReview')
        application.register(None)
        window=Adw.ApplicationWindow(application=application)
        controller=api.AppUpdates('org.projectluma.Tide')
        with mock.patch.object(api,'for_application',return_value=controller),mock.patch.object(Toast,'show') as toast:
            api.window_commands(window,CommandRegistry())
            window.present();self.assertTrue(spin(window.get_mapped))
            window.hide();spin(seconds=.03)
            window.unrealize();spin(seconds=.03)
            controller.created=True
            controller.running=controller.local=A;controller.remote=B;controller._set('available')
            self.assertEqual(toast.call_count,0)
            window.present();self.assertTrue(spin(window.get_mapped))
            self.assertEqual(toast.call_count,1)
            controller.local=B;controller._set('ready')
            self.assertEqual(toast.call_count,2)
        window.destroy();controller.close()

if __name__=='__main__':unittest.main()
