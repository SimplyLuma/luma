from __future__ import annotations
import shutil
import subprocess
import threading
from pathlib import Path
import gi
gi.require_version('Gtk','4.0'); gi.require_version('Adw','1')
from gi.repository import Adw, Gio, GLib, Gtk
from luma_appkit import (AppWindow, CommandRegistry, EmptyState, Island,
    IslandSplitView, NavigationSidebar, NavigationRow, Toolbar)
from .config import load_config, windows_apps_root
from .engine import COMPONENTS, WineEngine
from .errors import RelayError
from .registry import capsule_root, list_manifests, read_manifest
from .presentation import capsule_facts, readable_size, apply_registry_setting


def _command_version(program: str) -> str:
    executable = shutil.which(program)
    if not executable: return 'Not installed'
    try:
        result = subprocess.run([executable,'--version'], capture_output=True, text=True, timeout=5)
        return (result.stdout or result.stderr).strip().splitlines()[0][:160] if result.returncode == 0 else 'Version unavailable'
    except (OSError, subprocess.SubprocessError, IndexError): return 'Version unavailable'


class RelayWindow(AppWindow):
    def __init__(self, application: Adw.Application, pane: str = 'windows') -> None:
        super().__init__(application=application, app_id='org.projectluma.Relay', title='Relay',
            icon_name='org.projectluma.Relay', commands=CommandRegistry(()),
            default_width=900, default_height=600, minimum_width=360, minimum_height=440)
        self.engine = WineEngine(load_config()); self.pane = pane
        self.snapshot = {}; self._closed = False; self._generation = 0; self._busy = False
        self.connect('close-request', self._closing)
        self.sidebar = NavigationSidebar(); self.sidebar.append_section('RUNTIMES')
        self.rows = {}
        for key, title, icon in [('windows','Windows','view-dual-symbolic'),('android','Android','phone-symbolic')]:
            row = NavigationRow(title, icon_name=icon); row.pane = key
            self.sidebar.append_row(row); self.rows[key] = row
        self.sidebar.append_section('RELAY')
        row = NavigationRow('About Relay', icon_name='object-flip-horizontal-symbolic'); row.pane = 'about'
        self.sidebar.append_row(row); self.rows['about'] = row
        self.total = Gtk.Label(label='Checking applications…', xalign=0); self.total.add_css_class('dim-label')
        self.sidebar.append_footer(self.total)
        self.sidebar.list.connect('row-selected', self._selected)
        self.content = Island(); self.content.set_hexpand(True)
        bar = Toolbar(); self.back = Gtk.Button(icon_name='go-previous-symbolic', tooltip_text='Back')
        self.back.connect('clicked',self._back); bar.append(self.back)
        self.heading_icon = Gtk.Image(pixel_size=24); bar.append(self.heading_icon)
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        self.heading = Gtk.Label(xalign=0); self.heading.add_css_class('heading')
        self.subtitle = Gtk.Label(xalign=0, ellipsize=3); self.subtitle.add_css_class('dim-label')
        labels.append(self.heading); labels.append(self.subtitle); bar.append(labels)
        refresh = Gtk.Button(icon_name='view-refresh-symbolic', tooltip_text='Refresh')
        refresh.connect('clicked', lambda *_: self.reload()); bar.append(refresh)
        self.content.append(bar)
        self.message = Gtk.Label(wrap=True, xalign=0, visible=False)
        self.message.set_margin_start(16); self.message.set_margin_end(16); self.message.add_css_class('dim-label')
        self.content.append(self.message)
        self.detail_body = Adw.Bin(hexpand=True, vexpand=True); self.content.append(self.detail_body)
        self.split = IslandSplitView(sidebar_width_fraction=.22, min_sidebar_width=178, max_sidebar_width=210)
        self.split.set_sidebar(Adw.NavigationPage.new(self.sidebar,'Relay'))
        self.split.set_content(Adw.NavigationPage.new(self.content,'Runtime'))
        self.set_body(self.split)
        compact = Adw.Breakpoint.new(Adw.BreakpointCondition.parse('max-width: 639px'))
        compact.add_setter(self.split,'collapsed',True); self.add_breakpoint(compact)
        self.split.connect('notify::collapsed',lambda *_: self._sync_back())
        self.sidebar.list.select_row(self.rows.get(pane,self.rows['windows']))
        self.reload()

    def _closing(self, *_): self._closed = True; self._generation += 1; return False
    def _sync_back(self): self.back.set_visible(self.pane.startswith('app:') or self.split.get_collapsed())
    def _back(self, *_):
        if self.pane.startswith('app:'): self.show_pane(self.pane.split(':',2)[1])
        else: self.split.set_show_content(False)
    def _selected(self, _list, row):
        if row is not None and hasattr(row,'pane'): self.show_pane(row.pane)
    def show_pane(self, pane):
        self.pane = pane; self.message.set_visible(False); self._sync_back()
        if self.split.get_collapsed(): self.split.set_show_content(True)
        if pane.startswith('app:'):
            _, kind, app_id = pane.split(':',2)
            if kind == 'windows': self._job(lambda: capsule_facts(app_id),lambda facts: self._windows_app(app_id,facts))
            else: self._android_app(app_id)
        elif self.snapshot:
            {'windows':self._windows, 'android':self._android, 'about':self._about}.get(pane,self._windows)()
        else: self._header('Relay','Checking installed runtimes…','org.projectluma.Relay')
    def _job(self, work, done, message='Checking…'):
        if self._busy: return
        self._busy = True
        self.detail_body.set_sensitive(False)
        self.sidebar.set_sensitive(False)
        self._generation += 1; generation = self._generation
        self.message.set_label(message); self.message.set_visible(True)
        def worker():
            try: result, error = work(), None
            except Exception as failure: result,error=None,str(failure)
            def finish():
                if self._closed or generation != self._generation: return False
                self._busy = False
                self.detail_body.set_sensitive(True)
                self.sidebar.set_sensitive(True)
                self.message.set_visible(bool(error)); self.message.set_label(error or '')
                if not error: done(result)
                return False
            GLib.idle_add(finish)
        threading.Thread(target=worker,daemon=True).start()
    def reload(self):
        def collect():
            doctor = self.engine.doctor()
            windows = sorted(list_manifests(),key=lambda a:a.get('last_opened',''),reverse=True)
            android = []; status = {}; version = 'Version unavailable'
            try:
                from luma_android.engine import WaydroidEngine
                engine = WaydroidEngine(); status = engine.status()
                for app in engine.applications_from_launchers():
                    desktop = Gio.DesktopAppInfo.new('waydroid.'+app.package+'.desktop')
                    if desktop is not None and not desktop.get_nodisplay():
                        android.append({'name':app.name,'package':app.package,'desktop':desktop})
                if str(status.get('session','')).upper() == 'RUNNING':
                    result = subprocess.run(['waydroid','prop','get','ro.build.version.release'],capture_output=True,text=True,timeout=5)
                    if result.returncode==0 and result.stdout.strip(): version=result.stdout.strip()[:80]
            except (ImportError,OSError,subprocess.SubprocessError,RuntimeError): pass
            return {'doctor':doctor,'windows':windows,'android':android,'android_status':status,
                    'android_version':version,'versions':{name:_command_version(name) for name in ('bwrap','winetricks','waydroid')}}
        self._job(collect,self._loaded)
    def _loaded(self, snapshot):
        self.snapshot=snapshot
        for key in ('windows','android'): self.rows[key].set_trailing(str(len(snapshot[key])))
        self.total.set_label(f"{len(snapshot['windows'])+len(snapshot['android'])} applications")
        self.show_pane(self.pane)
    def _header(self,title,subtitle,icon):
        self.heading.set_label(title); self.subtitle.set_label(subtitle); self.heading_icon.set_from_icon_name(icon)
    def _page(self):
        page=Adw.PreferencesPage(); self.detail_body.set_child(page); return page
    @staticmethod
    def _group(page,title,description=None):
        group=Adw.PreferencesGroup(title=title, description=description or ''); page.add(group); return group
    @staticmethod
    def _row(group,title,subtitle='',label=None,action=None):
        row=Adw.ActionRow(title=title,subtitle=subtitle); group.add(row)
        if label:
            button=Gtk.Button(label=label,valign=Gtk.Align.CENTER)
            if action: button.connect('clicked',lambda *_:action())
            else: button.set_sensitive(False)
            row.add_suffix(button)
        return row
    def _missing(self,kind):
        title=kind.title(); self._header(title+' applications','Not set up','phone-symbolic' if kind=='android' else 'view-dual-symbolic')
        self.detail_body.set_child(EmptyState(title+' applications are not set up',
            'Download size is unavailable until a supported runtime source is configured.',
            'phone-symbolic' if kind=='android' else 'view-dual-symbolic',
            primary=('Set up '+title+' applications',lambda:self._setup(kind))))
    def _setup(self,kind):
        # This UI does not bypass the image/update owner to install host packages.
        def prepare():
            if kind=='android':
                from luma_android.engine import WaydroidEngine
                WaydroidEngine().ensure_ready(); return None
            from . import fex
            if fex.available(): fex.prepare_rootfs(); return None
            raise RelayError('Windows support is not available from your configured software sources yet.')
        self._job(prepare,lambda _:self.reload(),'Setting up '+kind.title()+' applications…')
    def _windows(self):
        doctor=self.snapshot['doctor']
        if not doctor['wine_available'] or not doctor['sandbox_available']: self._missing('windows'); return
        self._header('Windows applications',doctor['wine']+' · private app environments','view-dual-symbolic')
        page=self._page(); apps=self._group(page,'Applications')
        for app in self.snapshot['windows']:
            row=self._row(apps,str(app.get('name','Windows application')),self._opened(app))
            row.add_prefix(Gtk.Image.new_from_icon_name(str(app.get('icon','application-x-executable'))))
            row.add_suffix(Gtk.Image(icon_name='go-next-symbolic')); row.set_activatable(True)
            row.connect('activated',lambda _r,a=app:self.show_pane('app:windows:'+a['app_id']))
        self._row(apps,'Add a Windows application','Open an .exe or .msi. Valet inspects it before anything runs.','Open…',lambda:self._choose('windows'))
        group=self._group(page,'Graphics and components','Each application has its own settings and installed components.')
        self._row(group,'Graphics','Wine provides the active renderer. Per-application renderer switching is not available in this build.')
        self._row(group,'Windows version and scaling','Open an application above to change its compatibility settings.')
        self._row(group,'Optional components','Reviewed Microsoft components are installed only into the selected application.')
        group=self._group(page,'What they can reach')
        self._row(group,'Your files','Only folders explicitly granted to each application are shared.')
        self._row(group,'Network and notifications','Managed separately for each application. Changes apply when it next opens.')
        group=self._group(page,'Storage')
        self._row(group,'Private environments',str(windows_apps_root()))
    @staticmethod
    def _opened(app):
        return 'Opened '+str(app['last_opened']) if app.get('last_opened') else 'Last opened unavailable'
    def _android(self):
        status=self.snapshot['android_status']
        if not shutil.which('waydroid'): self._missing('android'); return
        self._header('Android applications',f"Android {self.snapshot['android_version']} · {str(status.get('container','unknown')).lower()}",'phone-symbolic')
        page=self._page(); apps=self._group(page,'Applications')
        for app in self.snapshot['android']:
            row=self._row(apps,app['name'],'Android application')
            icon=app['desktop'].get_icon()
            if icon: row.add_prefix(Gtk.Image.new_from_gicon(icon))
            row.add_suffix(Gtk.Image(icon_name='go-next-symbolic')); row.set_activatable(True)
            row.connect('activated',lambda _r,a=app:self.show_pane('app:android:'+a['package']))
        self._row(apps,'Add an Android application','Open an .apk. Valet reads its manifest first.','Open…',lambda:self._choose('android'))
        group=self._group(page,'Runtime')
        self._row(group,'Resume Android applications','Starts the installed runtime without opening Android Home.','Resume',lambda:self._android_action('resume'))
        self._row(group,'Pause Android applications','Stops Android apps until you resume them.','Pause',lambda:self._confirm_lifecycle('pause'))
        self._row(group,'Restart Android applications','Closes open Android apps and preserves their data.','Restart',lambda:self._confirm_lifecycle('restart'))
        group=self._group(page,'What they can reach')
        self._row(group,'Storage','Android apps share their isolated Android storage. Your Luma home folder is not shared.')
        self._row(group,'Notifications, camera, microphone and location','Use each application’s permissions. Unavailable host bridges are not presented as working switches.')
        group=self._group(page,'Getting around')
        self._row(group,'Back','Use the Back control beside the window buttons.')
        group=self._group(page,'Hardware')
        self._row(group,'Graphics','Uses the graphics profile admitted for this device. Switching profiles here is not yet available.')
    def _android_action(self, action):
        def work():
            from luma_android.engine import WaydroidEngine
            engine=WaydroidEngine()
            return {'resume':engine.ensure_ready,'pause':engine.stop_session,'restart':engine.restart_session}[action]()
        self._job(work,lambda _:self.reload(),action.title()+' in progress…')
    def _confirm_lifecycle(self,action):
        dialog=Adw.AlertDialog(heading=action.title()+' Android applications?',body='Open Android apps will close. Their saved data will be preserved.')
        dialog.add_response('cancel','Cancel'); dialog.add_response(action,action.title())
        dialog.set_default_response('cancel'); dialog.set_close_response('cancel')
        dialog.connect('response',lambda _d,response:self._android_action(action) if response==action else None); dialog.present(self)
    def _windows_app(self,app_id,facts):
        if self.pane!='app:windows:'+app_id: return
        app=facts['manifest']; self._header(app.get('name','Windows application'),readable_size(facts['bytes'])+' · '+self._opened(app),app.get('icon','application-x-executable'))
        page=self._page(); group=self._group(page,'This application')
        for title,key,callback in [('Network','network',self.engine.set_network),('May notify you','notifications',self.engine.set_notifications)]:
            row=Adw.SwitchRow(title=title,subtitle='Applies when the application next opens.'); row.set_active(bool(app.get(key,True)))
            row.connect('notify::active',lambda widget,_p,fn=callback:self._setting(lambda enabled=widget.get_active():fn(app_id,enabled)))
            group.add(row)
        self._choice(group,'Windows version',['Not set','Windows 11','Windows 10','Windows 8.1','Windows 7'],[None,'win11','win10','win81','win7'],facts['windows_version'],lambda value:apply_registry_setting(app_id,'windows-version',value))
        self._choice(group,'Scaling',['Not set','100%','125%','150%','200%'],[None,'96','120','144','192'],facts['dpi'],lambda value:apply_registry_setting(app_id,'scale',value))
        group=self._group(page,'Your files','Only the folders listed here are available to this application.')
        for grant in app.get('grants',[]):
            self._row(group,Path(grant['path']).name,grant['path']+' · '+grant.get('mode','read-only'),'Revoke',lambda g=grant:self._setting(lambda:self.engine.revoke(app_id,Path(g['path']))))
        self._row(group,'Share a folder','Grant read-only access to one selected folder.','Choose…',lambda:self._folder(app_id))
        group=self._group(page,'Components','Install only into this application. Downloads come from the named publishers.')
        for verb,title in COMPONENTS.items():
            self._row(group,title,'Installed' if verb in facts['components'] else 'Not installed',
                'Installed' if verb in facts['components'] else 'Install',None if verb in facts['components'] else lambda v=verb:self._component(app_id,v))
        group=self._group(page,'Storage'); self._row(group,'Kept in',facts['prefix'])
        self._row(group,'Remove '+str(app.get('name','application')),'Valet manages removal and the choice to keep application data.','Remove…',lambda:self._remove('org.projectluma.Relay.Windows.'+app_id+'.desktop'))
    def _choice(self,group,title,labels,values,current,apply):
        row=Adw.ActionRow(title=title,subtitle='Close and reopen the application after changing this setting.')
        select=Gtk.DropDown.new_from_strings(labels); select.set_valign(Gtk.Align.CENTER)
        select.set_selected(values.index(current) if current in values else 0)
        select.connect('notify::selected',lambda widget,_p:self._setting(lambda:apply(values[widget.get_selected()])) if values[widget.get_selected()] is not None else widget.set_selected(values.index(current) if current in values else 0))
        row.add_suffix(select); group.add(row)
    def _setting(self,work):
        self._job(work,lambda _:self.show_pane(self.pane),'Applying…')
    def _folder(self,app_id):
        dialog=Gtk.FileDialog(title='Share a folder with this application')
        def picked(source,result):
            try:
                file=source.select_folder_finish(result)
                if file and file.get_path(): self._setting(lambda:self.engine.grant(app_id,Path(file.get_path()),'read-only'))
            except GLib.Error: pass
        dialog.select_folder(self,None,picked)
    def _component(self,app_id,verb):
        dialog=Adw.AlertDialog(heading='Install '+COMPONENTS[verb]+'?',body='This downloads an optional Microsoft component into this application’s private environment. The publisher’s license applies.')
        dialog.add_response('cancel','Cancel'); dialog.add_response('install','Install'); dialog.set_default_response('cancel')
        dialog.connect('response',lambda _d,response:self._job(lambda:self.engine.install_component(app_id,verb),lambda _:self.show_pane(self.pane),'Installing '+COMPONENTS[verb]+'…') if response=='install' else None)
        dialog.present(self)
    def _android_app(self,package):
        app=next((a for a in self.snapshot.get('android',[]) if a['package']==package),None)
        if app is None: self.message.set_label('This application is no longer registered. Refresh to check again.'); self.message.set_visible(True); return
        self._header(app['name'],'Android application','phone-symbolic')
        icon=app['desktop'].get_icon()
        if icon: self.heading_icon.set_from_gicon(icon)
        page=self._page(); group=self._group(page,'This application')
        def permissions():
            from luma_android.engine import WaydroidEngine
            WaydroidEngine().open_permissions(package)
        self._row(group,'Files, notifications, camera and microphone','Android owns and enforces these per-app permissions.','Manage…',lambda:self._job(permissions,lambda _:None,'Opening permissions…'))
        group=self._group(page,'Storage')
        self._row(group,'Application data','Kept inside isolated Android storage. Size is not available to this host interface.')
        self._row(group,'Remove '+app['name'],'The Android runtime remains available to other applications.','Remove…',lambda:self._remove('waydroid.'+package+'.desktop'))
    def _about(self):
        self._header('About Relay','Compatibility layer','org.projectluma.Relay'); page=self._page()
        group=self._group(page,'Relay')
        self._row(group,'Applications from other platforms','Relay starts when you open a Windows or Android application. There is nothing here you need to visit for it to work.')
        group=self._group(page,'Runtimes')
        self._row(group,'Windows',self.snapshot['doctor']['wine']+' · '+str(len(self.snapshot['windows']))+' applications')
        self._row(group,'Android',self.snapshot['android_version']+' · '+str(len(self.snapshot['android']))+' applications')
        group=self._group(page,'Built on open source','Luma owns the integration, sandbox policy, application records and interface. It does not own their upstream work.')
        for name,program,description in [('Wine',None,'Windows API compatibility engine'),('Bubblewrap','bwrap','Per-application namespace mechanism'),('Winetricks','winetricks','Reviewed optional component transactions'),('Waydroid','waydroid','Android container engine')]:
            version=self.snapshot['doctor']['wine'] if program is None else self.snapshot['versions'][program]
            self._row(group,name,description+' · '+version)
    def _choose(self,kind):
        dialog=Gtk.FileDialog(title='Add an application'); filter=Gtk.FileFilter(name='Windows applications' if kind=='windows' else 'Android applications')
        for suffix in (['exe','msi'] if kind=='windows' else ['apk','apks','xapk','apkm']): filter.add_pattern('*.'+suffix)
        filters=Gio.ListStore.new(Gtk.FileFilter); filters.append(filter); dialog.set_filters(filters)
        def selected(source,result):
            try:
                file=source.open_finish(result)
                if file and file.get_path(): subprocess.Popen(['/usr/bin/luma-installer',file.get_path()],start_new_session=True)
            except GLib.Error: pass
            except OSError as error: self.message.set_label(str(error)); self.message.set_visible(True)
        dialog.open(self,None,selected)
    def _remove(self,desktop):
        try: subprocess.Popen(['/usr/bin/luma-installer','--remove',desktop],start_new_session=True)
        except OSError as error: self.message.set_label(str(error)); self.message.set_visible(True)


class RelayApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id='org.projectluma.Relay',flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
    def do_command_line(self,command_line):
        args=command_line.get_arguments()[1:]; pane='windows'
        if len(args)==2 and args[0]=='--pane' and args[1] in {'windows','android','about'}: pane=args[1]
        elif args: command_line.printerr('Usage: luma-relay-settings [--pane windows|android|about]\n'); return 2
        window=self.props.active_window or RelayWindow(self,pane); window.show_pane(pane); window.present(); return 0
    def do_activate(self):
        window=self.props.active_window or RelayWindow(self); window.present()
def main():
    import sys
    return RelayApplication().run(sys.argv)
if __name__=='__main__': raise SystemExit(main())
