# SPDX-License-Identifier: Apache-2.0
"""Fixture-only port smoke test in a private mutter and private session bus.

LUMA_MONITOR_RUNTIME_PYTHONPATH selects explicit built/staged app and kit roots.
Both imported package origins must remain inside those roots. Set this variable
inside the final runtime after toolbox; temporary XDG paths are assigned here.
Without the variable, checks explicitly use this worktree's source packages.
"""
import os
import sys
import subprocess
import tempfile
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
FIXTURE=ROOT/'tests/fixtures/monitor-v70.json'


def identity_check():
    """Hold a fake production owner and exercise the actual preview entry point."""
    from gi.repository import Gio,GLib
    assert 'monitor-runtime-' in os.environ.get('DBUS_SESSION_BUS_ADDRESS',''), 'Identity test requires its private bus'
    private_root=Path(os.environ['DBUS_SESSION_BUS_ADDRESS'].split('unix:path=',1)[1]).parent
    assert all(Path(os.environ[name]).parent==private_root for name in
               ('XDG_CONFIG_HOME','XDG_STATE_HOME','XDG_CACHE_HOME','XDG_DATA_HOME')), 'Identity data must remain in the private test root'
    production_id='io.luma.Monitor';preview_id=production_id+'.LumaUIPreview'
    machine='--identity-machine' in sys.argv
    if '--identity-production' in sys.argv:
        if machine:
            from luma_monitor.machine_application import MonitorApplication
        else:
            from luma_monitor.application import MonitorApplication
        app=MonitorApplication()
        assert app.get_application_id()==production_id,app.get_application_id()
        assert app.register(None) and app.get_is_remote(), 'Production probe did not resolve the private dummy owner'
        print('PASS production identity preserved:',app.get_application_id(),'; private dummy is remote owner; no activation')
        return
    activations=[]
    production_preferences=Path(os.environ['XDG_CONFIG_HOME'])/'luma-monitor/preferences.json'
    production_preferences.parent.mkdir(parents=True,exist_ok=True)
    production_preferences_bytes=b'{"tab":"cpu","advanced":false,"background":false,"untouched":{"owner":"production"}}\n'
    production_preferences.write_bytes(production_preferences_bytes)
    state_folder=Path(os.environ['XDG_STATE_HOME'])/'luma/windows'
    def production_states():
        return [p for p in state_folder.glob(production_id+'*.json')
                if not p.name.startswith(preview_id+'.')]
    assert not production_states(), 'Private test started with production window state'
    production=Gio.Application(application_id=production_id)
    production.connect('activate',lambda *_:activations.append('production'))
    assert production.register(None) and not production.get_is_remote()
    probe_env=dict(os.environ)
    probe_env.pop('LUMA_MONITOR_PREVIEW',None);probe_env.pop('LUMA_MONITOR_FIXTURE',None)
    probe=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--child','--identity-production']+(['--identity-machine'] if machine else []),env=probe_env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    deadline=time.monotonic()+10
    context=GLib.MainContext.default()
    while probe.poll() is None and time.monotonic()<deadline:
        while context.pending():context.iteration(False)
        time.sleep(.01)
    try:probe_out,probe_error=probe.communicate(timeout=1)
    except subprocess.TimeoutExpired:
        probe.kill();probe.communicate();raise AssertionError('Production identity probe timed out')
    print(probe_out)
    assert probe.returncode==0,probe_error
    from luma_monitor.preview import make_application
    app=make_application(['--this-machine'] if machine else [])
    assert app.get_application_id()==preview_id
    assert not (app.get_flags() & Gio.ApplicationFlags.NON_UNIQUE), 'Identity evidence must use bus registration'
    assert app.register(None) and not app.get_is_remote(), 'Preview resolved another instance'
    connection=app.get_dbus_connection()
    def owner(name):
        return connection.call_sync('org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus','GetNameOwner',GLib.Variant('(s)',(name,)),GLib.VariantType.new('(s)'),Gio.DBusCallFlags.NONE,3000,None).unpack()[0]
    assert owner(preview_id)==connection.get_unique_name()
    assert owner(production_id)==production.get_dbus_connection().get_unique_name()
    failures=[];seen=[]
    def inspect_and_close():
        window=app.get_active_window()
        try:
            assert window and window.get_application() is app
            assert window.get_application().get_application_id()==preview_id
            assert window._geometry_app_id==preview_id,window._geometry_app_id
            assert window._state.path.name==preview_id+'.json',window._state.path
            assert window._geometry_state.path.name.startswith(preview_id+'.'),window._geometry_state.path
            if machine:assert window.preferences!=production_preferences, 'Preview shares production machine preferences'
            assert not activations,activations
            seen.append(preview_id)
        except Exception as error:failures.append(str(error))
        finally:
            if window:window.close()
        app.quit();return GLib.SOURCE_REMOVE
    GLib.timeout_add(700,inspect_and_close)
    app.run([sys.argv[0],'--this-machine'] if machine else [])
    assert not failures,failures
    assert seen==[preview_id] and not app.get_windows(), 'Preview did not close'
    assert not activations,activations
    assert not production_states(), 'Preview wrote production window state'
    assert production_preferences.read_bytes()==production_preferences_bytes, 'Preview wrote production machine preferences'
    print('PASS private-bus preview identity:',preview_id,'; production activated 0 times; production window state/preferences untouched; windows closed; mode', 'this-machine' if machine else 'lumaui')


def child():
    if 'LUMA_MONITOR_RUNTIME_PYTHONPATH' in os.environ:
        import importlib.util
        roots=[Path(p).resolve() for p in os.environ['LUMA_MONITOR_RUNTIME_PYTHONPATH'].split(os.pathsep)]
        for module in ('luma_monitor','luma_appkit'):
            spec=importlib.util.find_spec(module)
            assert spec and spec.origin, f'No concrete runtime package for {module}'
            origin=Path(spec.origin).resolve()
            assert any(origin.is_relative_to(root) for root in roots),f'{module} fell back outside selected runtime roots: {origin}'
            print('Selected runtime package',module,origin,flush=True)
    if any(flag in sys.argv for flag in ('--identity','--identity-production','--identity-machine')):
        return identity_check()
    if '--kit-tests' in sys.argv:
        import unittest
        suite=unittest.defaultTestLoader.discover(str(ROOT/'tests/unit'),pattern='test_luma_appkit*.py')
        result=unittest.TextTestRunner(verbosity=1).run(suite)
        raise SystemExit(0 if result.wasSuccessful() else 1)
    if '--app-tests' in sys.argv:
        import unittest
        suite=unittest.defaultTestLoader.discover(str(ROOT/'src/luma-monitor/tests'),pattern='test_*.py')
        assert suite.countTestCases()>0, 'No Monitor unit tests discovered'
        result=unittest.TextTestRunner(verbosity=2).run(suite)
        assert not result.skipped, 'Native Monitor unit checks must not skip GTK integration tests'
        raise SystemExit(0 if result.wasSuccessful() else 1)
    import traceback
    from luma_monitor.application import MonitorApplication,GLib
    from gi.repository import Adw
    app=MonitorApplication();failures=[];checked=[]
    if '--contrast' in sys.argv:
        app.connect_after('activate',lambda a: failures.append('High contrast was not enabled') if not Adw.StyleManager.get_default().get_high_contrast() else None)
    if '--light' in sys.argv or '--dark' in sys.argv:
        app.connect_after('activate',lambda a: failures.append('Requested theme was not enabled') if Adw.StyleManager.get_default().get_dark()!=('--dark' in sys.argv) else None)
    if '--live' in sys.argv:
        from gi.repository import Gtk
        def live_check(attempt=0):
            w=app.get_active_window()
            if not getattr(w,'live_processes',None) and attempt<40:
                GLib.timeout_add(250,lambda:live_check(attempt+1));return GLib.SOURCE_REMOVE
            try:
                assert not w.fixture
                process=next(p for p in w.live_processes if p.pid==os.getpid())
                assert process.start>0
                if not getattr(w,'_refresh_probe',False):
                    w._sort('n','ascending');w._resource('cpu')
                    assert w.sort==('v','descending'),w.sort
                    w.totals['cpu']=None
                    for row in w.apps:row['cpu']=None
                    w._render_body()
                    w.header.cells[0].grab_focus()
                    assert w.get_focus() is w.header.cells[0], 'Could not focus the list header'
                    w._refresh_probe=True
                    w._sample()
                    GLib.timeout_add(250,lambda:live_check(attempt+1));return GLib.SOURCE_REMOVE
                if w.totals['cpu'] is None and attempt<40:
                    GLib.timeout_add(250,lambda:live_check(attempt+1));return GLib.SOURCE_REMOVE
                assert w.totals['cpu'] is not None,'No second live CPU sample'
                assert w.get_focus() is w.header.cells[0],'Live redraw lost header keyboard focus'
                from luma_monitor.application import descendants
                labels=[widget.get_label() for widget in descendants(w.hero) if isinstance(widget,Gtk.Label)]
                assert 'Loading…' not in labels,labels
                measured=[row for row in w.apps if row['cpu'] is not None]
                assert measured,'All app CPU rows are still unavailable after the second sample'
                listed=[widget.get_name() for widget in descendants(w.list) if isinstance(widget,Gtk.Button) and widget.get_name().startswith('mn-app-')]
                expected=['mn-app-'+row['id'] for row in sorted(measured,key=lambda row:row['cpu'],reverse=True)]
                assert listed[:len(expected)]==expected,(listed,expected)
                for resource in ('cpu','mem','disk','net','en'):
                    w._sort('n','ascending')
                    w._resource(resource);w._toggle_all();assert w.all_processes;w._toggle_all()
                    assert w.sort==('v','descending'),(resource,w.sort)
                assert {g['id'] for g in w.groups}=={'sys','bg','kern'}
                assert len({p[1] for g in w.groups for p in g['p']})==sum(g['total'] for g in w.groups)
                w._live_properties(process.pid)
                w._pick('p:'+str(process.pid))
                kill=next(widget for widget in descendants(w.center.bar) if widget.get_name()=='mn-force')
                assert kill.get_sensitive(),'Live Kill action is disabled'
                w._ask_force()
                assert w.confirm_dialog.handle is not None
                w.confirm_dialog.handle.cancel()  # Never signal the live Monitor process.
                w._open_sheet('files')
                def inspected():
                    try:
                        assert w.host.modal is not None
                        facts=dict(w.properties[(process.pid,process.start,process.cgroup)])
                        assert 'lumaui_runtime.py' in facts['Command line'],facts
                        assert not w.fixture
                        w.host.modal.close();w.close()
                    except Exception:failures.append(traceback.format_exc())
                    app.quit();return GLib.SOURCE_REMOVE
                GLib.timeout_add(500,inspected)
            except Exception:
                failures.append(traceback.format_exc());app.quit()
            return GLib.SOURCE_REMOVE
        GLib.timeout_add(1000,live_check);app.run([])
        assert not failures,'\n'.join(failures)
        print('PASS MONITOR live runtime: sample and inspect own process on private bus/compositor; no control writes')
        return
    if '--phone' in sys.argv:app.connect_after('activate',lambda a:a.get_active_window().set_default_size(390,740))
    if '--late-inspection' in sys.argv:
        import threading
        from types import SimpleNamespace
        from unittest.mock import patch
        from luma_monitor.application import descendants
        started=threading.Event();release=threading.Event();completed=[];deliveries=[]
        columns=['FD','Type','Object'];rows=[['0','file','/dev/null']]
        def read_table(process,kind):
            assert process.pid==3113 and kind=='files'
            started.set();assert release.wait(5),'Inspection worker was not released'
            return columns,rows
        def begin():
            w=app.get_active_window()
            try:
                w._toggle_all();w._pick('p:3113')
                w.live_processes=[SimpleNamespace(pid=3113,start=12345,cgroup='/fixture/viola')]
                actual=w._inspection_ready
                def ready(*args):
                    completed.append(True);deliveries.append(True)
                    result=actual(*args)
                    if len(deliveries)==1:GLib.idle_add(stale_done)
                    else:GLib.timeout_add(250,current_done)
                    return result
                w._inspection_ready=ready
                w.fixture=False
                try:w._open_sheet('files')
                finally:w.fixture=True
                assert started.wait(2),'Actual inspection worker did not start'
                # The read has completed for one process, then a sampler sees
                # another process with the same PID before idle delivery.
                w.live_processes=[SimpleNamespace(pid=3113,start=12346,cgroup='/fixture/viola')]
                release.set()
            except Exception:
                failures.append(traceback.format_exc());release.set();w.close();app.quit()
            return GLib.SOURCE_REMOVE
        def stale_done():
            w=app.get_active_window()
            try:
                assert completed,'Actual inspection idle completion did not run'
                assert w.host.modal is None,'Reused PID opened the old process read'
                completed.clear()
                w.fixture=False
                try:w._open_sheet('files')
                finally:w.fixture=True
            except Exception:
                failures.append(traceback.format_exc());w.close();app.quit()
            return GLib.SOURCE_REMOVE
        def current_done():
            w=app.get_active_window()
            try:
                assert completed and w.host.modal is not None,'Current process read was discarded'
                close=next(c for c in descendants(w.host.modal.card) if c.get_name()=='mn-sheet-close')
                close.emit('clicked')
                GLib.timeout_add(400,finish)
            except Exception:
                failures.append(traceback.format_exc());w.close();app.quit()
            return GLib.SOURCE_REMOVE
        def finish():
            w=app.get_active_window()
            try:assert w.host.modal is None,'Actual inspection close left a modal open'
            except Exception:failures.append(traceback.format_exc())
            w.close();app.quit();return GLib.SOURCE_REMOVE
        def expired():
            failures.append('Actual inspection completion exceeded 10 seconds')
            release.set()
            if app.get_active_window():app.get_active_window().close()
            app.quit();return GLib.SOURCE_REMOVE
        with patch('luma_monitor.inspection.inspect_table',side_effect=read_table):
            deadline=GLib.timeout_add(10000,expired)
            GLib.timeout_add(1000,begin);app.run([])
            if not failures:GLib.source_remove(deadline)
        assert not failures,'\n'.join(failures)
        assert not app.get_windows(),'Late inspection left a window open'
        print('PASS MONITOR actual asynchronous inspection: reused PID result rejected; current identity opens; actual close; windows closed; fixture-only reads')
        return
    if '--inspection' in sys.argv:
        if '--phone' not in sys.argv:app.connect_after('activate',lambda a:a.get_active_window().set_default_size(1180,740))
        from gi.repository import Gtk
        from luma_monitor.application import descendants
        cases=iter((('a:viola','files'),('a:viola','maps'),('p:3113','files'),('p:3113','maps')))
        inspected=[]
        def open_next():
            w=app.get_active_window()
            try:
                subject,kind=next(cases)
            except StopIteration:
                w.close();app.quit();return GLib.SOURCE_REMOVE
            try:
                w._deselect()
                if w.all_processes!=subject.startswith('p:'):w._toggle_all()
                w._pick(subject);w._open_sheet(kind)
                assert w.host.modal is not None
            except Exception:
                failures.append(traceback.format_exc());w.close();app.quit();return GLib.SOURCE_REMOVE
            def check_sheet():
                try:
                    modal=w.host.modal
                    assert modal is not None and not modal.drawer,'Inspection must remain a centred card'
                    sheet=next(c for c in descendants(modal.card) if c.get_name()=='mn-sheet')
                    assert sheet.get_width()<=w.island.get_width()-48+2,(sheet.get_width(),w.island.get_width())
                    ok,bounds=sheet.compute_bounds(w)
                    assert ok,'Inspection outer bounds must be measurable'
                    print('Inspection card',subject,kind,'window width',w.get_width(),'island width',w.island.get_width(),'card width',bounds.size.width,flush=True)
                    assert bounds.size.width<=w.island.get_width()-48+2,(bounds.size.width,w.island.get_width())
                    if '--phone' in sys.argv:
                        assert w.get_width()==390,w.get_width()
                    else:
                        assert w.get_width()==1180,w.get_width()
                        assert abs(bounds.size.width-720)<=2,bounds.size.width
                    body=next(c for c in descendants(sheet) if isinstance(c,Gtk.ListBox))
                    rows=[c for c in descendants(body) if c.get_parent() is body]
                    assert len(rows)==8,len(rows)
                    cells=[[c.get_label() for c in descendants(row) if isinstance(c,Gtk.Label)] for row in rows]
                    assert all(len(row)==(3 if kind=='files' else 4) for row in cells),cells
                    assert cells[0]==(['0','file','/dev/null'] if kind=='files' else ['5578a3c10000','r-xp','1.9 MB','/usr/bin/luma-'+('viola' if subject.startswith('a:') else 'x')]),cells[0]
                    if kind=='files':
                        path=next(c for c in descendants(sheet) if isinstance(c,Gtk.Label) and 'index.db' in c.get_label())
                        assert path.get_label().startswith('/home/nick/.var/app/')
                        if '--phone' in sys.argv:assert path.get_layout().get_line_count()>1,'Long phone file paths must wrap'
                    close=next(c for c in descendants(modal.card) if c.get_name()=='mn-sheet-close')
                    close.emit('clicked');inspected.append((subject,kind))
                    def after_close():
                        if w.host.modal is not None:
                            failures.append('Inspection close left its modal open');w.close();app.quit();return GLib.SOURCE_REMOVE
                        return open_next()
                    GLib.timeout_add(400,after_close)
                except Exception:
                    failures.append(traceback.format_exc());w.close();app.quit()
                return GLib.SOURCE_REMOVE
            GLib.timeout_add(250,check_sheet);return GLib.SOURCE_REMOVE
        GLib.timeout_add(1000,open_next);app.run([])
        assert not failures,'\n'.join(failures)
        assert len(inspected)==4,inspected
        assert not app.get_windows(),'Inspection runtime left a window open'
        print('PASS MONITOR inspection runtime: app/process files/maps, eight complete rows, centred card bounds, phone path wrapping and actual close buttons; windows closed')
        return
    if os.environ.get('LUMA_MONITOR_QUERY') and '--search-layout' not in sys.argv:
        def initial_query():
            try:
                w=app.get_active_window();assert w.query==os.environ['LUMA_MONITOR_QUERY']
                assert w.all_processes==(not any(w.query.casefold() in a['n'].casefold() for a in w.apps))
                w.search.entry.set_text('')
                if w.all_processes:w._toggle_all()
            except Exception:failures.append(traceback.format_exc())
            return GLib.SOURCE_REMOVE
        GLib.timeout_add(300,initial_query)
    def verify():
        try:
            w=app.get_active_window();assert w is not None and w.fixture
            assert len(w.apps)==10 and w.totals['cpu']==49.7
            from gi.repository import Gtk
            theme=Gtk.IconTheme.get_for_display(w.get_display())
            assert all(theme.has_icon('luma-v3-'+a['ic']) for a in w.apps)
            if '--phone' in sys.argv:assert w.get_width()==390,(w.get_width(),w.get_height())
            w._pick('a:viola');assert w.selected=='a:viola'
            w._expand('viola');assert 'viola' in w.open_apps
            w._more();w._priority();w._set_priority('5')
            w._open_sheet('files');assert w.host.modal is not None;w.host.modal.close()
            w._open_sheet('maps');assert w.host.modal is not None;w.host.modal.close()
            w._ask_force();assert w.confirm_dialog.handle is not None;w.confirm_dialog.handle.cancel()
            w._control('pause');assert 'viola' in w.source.stopped
            w._control('resume');assert 'viola' not in w.source.stopped
            w._control('quit');assert len(w.source.visible_apps())==9
            w._reopen('viola');assert len(w.source.visible_apps())==10
            w._toggle_all();w._pick('p:3113')
            w._ask_force();assert w.confirm_dialog.handle is not None;w.confirm_dialog.handle.cancel()
            groups_before=[list(g['p']) for g in w.groups]
            w._control('end');assert w.selected is None
            assert [g['p'] for g in w.groups]==groups_before
            w.search.entry.set_text('rustc');assert w.query=='rustc'
            w.search.entry.set_text('nothing-matches');assert w.all_processes
            assert not w.working and not w.timer and not hasattr(w,'catalog')
            w.close()
        except Exception:failures.append(traceback.format_exc())
        app.quit();return GLib.SOURCE_REMOVE
    def action_checks(geometry=True):
        from gi.repository import Gtk
        from luma_monitor.application import descendants
        w=app.get_active_window();w._deselect()
        if not geometry:
            assert w.get_width()==(390 if '--phone' in sys.argv else 1180),w.get_width()
        actions=iter(('mn-app-viola','mn-more','mn-priority','mn-priority-5','mn-more','mn-stop','mn-continue','mn-more','mn-files','mn-sheet-close','mn-more','mn-maps','mn-sheet-close'))
        def activate_next():
            try:name=next(actions)
            except StopIteration:
                try:
                    assert w.source.priority['viola']=='5'
                    assert 'viola' not in w.source.stopped
                    assert w.host.modal is None
                except Exception:failures.append(traceback.format_exc())
                if not geometry:
                    w.close();app.quit()
                    assert not app.get_windows(), 'Named-button action check left a window open'
                    return GLib.SOURCE_REMOVE
                w._deselect()
                if '--phone' not in sys.argv:
                    w._pick('a:viola');w._more();w._priority()
                    def check_synchronous_menu():
                        try:
                            anchor=next(c for c in descendants(w) if c.get_name()=='mn-more' and c.get_mapped())
                            ok,menu_bounds=w._menu_widget.compute_bounds(w.layer_host);ok_anchor,anchor_bounds=anchor.compute_bounds(w.layer_host)
                            assert ok and ok_anchor
                            assert abs(menu_bounds.origin.x+menu_bounds.size.width/2-anchor_bounds.origin.x-anchor_bounds.size.width/2)<=2,(menu_bounds.origin.x,menu_bounds.size.width,anchor_bounds.origin.x,anchor_bounds.size.width)
                        except Exception:failures.append(traceback.format_exc())
                        w._deselect();return verify()
                    GLib.timeout_add(200,check_synchronous_menu);return GLib.SOURCE_REMOVE
                return verify()
            try:
                if '--activity-layout' in sys.argv and name=='mn-more' and w.source.priority.get('viola')=='5':
                    row=next(c for c in descendants(w.list) if c.get_name()=='mn-app-viola')
                    title=next(c for c in descendants(row) if isinstance(c,Gtk.Label) and c.has_css_class('mn-app-name'))
                    tag=next(c for c in descendants(row) if isinstance(c,Gtk.Label) and c.get_label()=='Low priority')
                    for cell in (title,tag):
                        ok,bounds=cell.compute_bounds(row);ok_parent,parent_bounds=cell.get_parent().compute_bounds(row)
                        print('Actual activity title/tag',cell.get_label(),'row',row.get_width(),row.get_height(),'box',bounds.origin.x,bounds.origin.y,bounds.size.width,bounds.size.height,'lines',cell.get_layout().get_line_count(),'ellipsized',cell.get_layout().is_ellipsized(),flush=True)
                        assert ok and ok_parent,'Title/tag allocation unavailable'
                        assert not cell.get_layout().is_ellipsized(),('Source title/tag must remain fully readable',cell.get_label())
                        assert bounds.origin.x>=parent_bounds.origin.x-2 and bounds.origin.x+bounds.size.width<=parent_bounds.origin.x+parent_bounds.size.width+2,('Title/tag exceeds its app-owned parent',cell.get_label())
                    if '--phone' not in sys.argv:
                        title_bounds=title.compute_bounds(row)[1];tag_bounds=tag.compute_bounds(row)[1]
                        assert abs(tag_bounds.origin.x-title_bounds.origin.x-title_bounds.size.width-8)<=2,'Desktop inline priority tag must retain source8px gap'
                if os.environ.get('LUMA_MONITOR_RUNTIME_METRICS'):
                    print('Before',name,'bar minimum',w.center.bar.measure(Gtk.Orientation.HORIZONTAL,-1)[0],'hero minimum',w.hero.measure(Gtk.Orientation.HORIZONTAL,-1)[0])
                target=next(c for c in descendants(w) if c.get_name()==name and c.get_mapped())
                assert isinstance(target,Gtk.Button),name
                if geometry and name=='mn-priority' and '--phone' in sys.argv:
                    drawer=w._menu_widget._drawer
                    ok,card_bounds=drawer.handle.card.compute_bounds(w)
                    ok_bar,bar_bounds=w.center.bar.compute_bounds(w)
                    assert ok and ok_bar
                    assert card_bounds.origin.y+card_bounds.size.height<=bar_bounds.origin.y-6,(card_bounds.origin.y,card_bounds.size.height,bar_bounds.origin.y)
                if geometry and name=='mn-sheet-close' and '--phone' in sys.argv:
                    assert not w.host.modal.drawer,'Inspection cards stay centred on phones'
                    sheet=next(c for c in descendants(w.host.modal.card) if c.get_name()=='mn-sheet')
                    assert sheet.get_width()<=w.island.get_width()-48+2,(sheet.get_width(),w.island.get_width())
                    paths=[c for c in descendants(w.host.modal.card) if isinstance(c,Gtk.Label) and 'index.db' in c.get_label()]
                    if paths:assert paths[0].get_layout().get_line_count()>1,'Phone file paths must wrap within the table'
                if geometry and name=='mn-priority-5' and '--phone' not in sys.argv:
                    anchor=next(c for c in descendants(w) if c.get_name()=='mn-more' and c.get_mapped())
                    ok,menu_bounds=w._menu_widget.compute_bounds(w.layer_host);ok_anchor,anchor_bounds=anchor.compute_bounds(w.layer_host)
                    assert ok and ok_anchor
                    assert abs(menu_bounds.origin.x+menu_bounds.size.width/2-anchor_bounds.origin.x-anchor_bounds.size.width/2)<=2,(menu_bounds.origin.x,menu_bounds.size.width,anchor_bounds.origin.x,anchor_bounds.size.width)
                target.emit('clicked')
            except Exception:
                failures.append(name+'\n'+traceback.format_exc());app.quit();return GLib.SOURCE_REMOVE
            GLib.timeout_add(200,activate_next);return GLib.SOURCE_REMOVE
        return activate_next()

    if '--search-layout' in sys.argv:
        from gi.repository import Pango
        from luma_monitor.application import descendants
        if '--phone' not in sys.argv:app.connect_after('activate',lambda a:a.get_active_window().set_default_size(1180,740))
        def check_empty():
            w=app.get_active_window()
            try:
                def apps_only():return [c for c in descendants(w.center.bar_row) if c.get_name()=='mn-all']
                assert w.query=='nothing-matches' and w.all_processes and not apps_only()
                empty=next(c for c in descendants(w.list) if (getattr(c,'get_label',lambda:'')() or '').startswith('No apps match'))
                font=empty.get_pango_context().get_font_description()
                assert abs(font.get_size()/Pango.SCALE-13)<=.5 and int(font.get_weight())==400,font.to_string()
                ok,bounds=empty.compute_bounds(w.list)
                assert ok and abs(bounds.origin.x-12)<=2,(ok,bounds.origin.x)
                w.search.entry.set_text('')
                w._toggle_all();assert not w.all_processes
                w._toggle_all();assert w.all_processes and len(apps_only())==1
                button=apps_only()[0]
                w.search.entry.set_text('rustc')
                assert w.query=='rustc' and apps_only()==[button],'Typing must preserve the previously rendered all-process bar'
                print('PASS MONITOR search: real entry startup query, auto expansion, empty text type/inset and existing Apps only control preserved',flush=True)
            except Exception:failures.append(traceback.format_exc())
            finally:w.close();app.quit()
            return GLib.SOURCE_REMOVE
        def check_search():
            w=app.get_active_window()
            try:
                assert w.query==os.environ['LUMA_MONITOR_QUERY']=='rustc'
                assert w.search.entry.get_text()=='rustc' and w.all_processes
                assert not [c for c in descendants(w.center.bar_row) if c.get_name()=='mn-all'],'Initial query rebuilt the bar with Apps only instead of preserving the initial search bar'
                w.search.entry.set_text('nothing-matches')
                GLib.timeout_add(200,check_empty)
            except Exception:
                failures.append(traceback.format_exc());w.close();app.quit()
            return GLib.SOURCE_REMOVE
        GLib.timeout_add(1000,check_search);app.run([])
        assert not failures,'\n'.join(failures)
        assert not app.get_windows(),'Search check left a window open'
        return

    if '--more-layout' in sys.argv or '--more-spacing' in sys.argv:
        from gi.repository import Gtk,Pango
        from luma_monitor.application import descendants
        if '--phone' not in sys.argv:app.connect_after('activate',lambda a:a.get_active_window().set_default_size(1180,740))
        def check_more():
            w=app.get_active_window()
            try:
                more=next(c for c in descendants(w.list) if c.get_name()=='mn-more-sys')
                assert isinstance(more,Gtk.Button) and more.get_mapped()
                text=more.get_child();font=text.get_pango_context().get_font_description()
                parent=more.get_parent();previous=more.get_prev_sibling()
                ok,bounds=more.compute_bounds(parent);ok_prev,prev_bounds=previous.compute_bounds(parent)
                assert ok and ok_prev
                assert abs(bounds.origin.y-prev_bounds.origin.y-prev_bounds.size.height-6)<=2,'Show more source top gap6px'
                assert abs(bounds.size.height-30)<=2,('Show more source height30px',bounds.size.height)
                assert abs(bounds.origin.x-40)<=2,('Show more source start40px',bounds.origin.x)
                print('Actual Show more font/classes/allocation',font.to_string(),text.get_css_classes(),bounds.origin.x,bounds.origin.y,bounds.size.height,'gap',bounds.origin.y-prev_bounds.origin.y-prev_bounds.size.height,flush=True)
                if '--more-layout' in sys.argv:
                    assert abs(font.get_size()/Pango.SCALE-12)<=.5 and int(font.get_weight())==600,font.to_string()
                assert 'sys' not in w.more_groups
                more.emit('clicked')
                assert 'sys' in w.more_groups and not any(c.get_name()=='mn-more-sys' for c in descendants(w.list)),'Actual Show more did not expand system group'
                print('PASS MONITOR actual Show more:6px gap,30px height,40px inset and real group expansion; type check', 'strict12/600' if '--more-layout' in sys.argv else 'separate pending shared semantics',flush=True)
            except Exception:failures.append(traceback.format_exc())
            finally:w.close();app.quit()
            return GLib.SOURCE_REMOVE
        def open_all():
            w=app.get_active_window()
            try:
                button=next(c for c in descendants(w) if c.get_name()=='mn-all' and c.get_mapped())
                assert isinstance(button,Gtk.Button);button.emit('clicked')
                GLib.timeout_add(400,check_more)
            except Exception:
                failures.append(traceback.format_exc());w.close();app.quit()
            return GLib.SOURCE_REMOVE
        GLib.timeout_add(1000,open_all);app.run([])
        assert not failures,'\n'.join(failures)
        assert not app.get_windows(),'Show more check left a window open'
        return

    if '--detail-layout' in sys.argv:
        from gi.repository import Gtk
        from luma_monitor.application import descendants
        if '--phone' not in sys.argv:app.connect_after('activate',lambda a:a.get_active_window().set_default_size(1180,740))
        def check_property_gaps(grid):
            pairs=0
            for cell in descendants(grid):
                if not cell.has_css_class('mn-property-cell'):continue
                labels=[c for c in descendants(cell) if isinstance(c,Gtk.Label)]
                assert len(labels)==2,'Every property must retain its key and value'
                key,value=labels;parent=key.get_parent()
                ok_key,key_bounds=key.compute_bounds(parent);ok_value,value_bounds=value.compute_bounds(parent)
                assert ok_key and ok_value and abs(value_bounds.origin.y-key_bounds.origin.y-key_bounds.size.height-2)<=2,'Property key/value gap must match source2px'
                pairs+=1
            assert pairs,'No allocated property cells checked'
        def check_process_details():
            w=app.get_active_window()
            try:
                process=next(c for c in descendants(w.list) if c.get_name()=='mn-process-3113')
                parent=process.get_parent()
                grid=next(c for c in descendants(parent) if c.get_parent() is parent and c.has_css_class('mn-properties'))
                ok,bounds=grid.compute_bounds(parent);ok_row,row_bounds=process.compute_bounds(parent)
                assert ok and abs(bounds.origin.x-40)<=2,('Raw process properties source inset40px',bounds.origin.x)
                assert abs(parent.get_width()-bounds.origin.x-bounds.size.width-12)<=2,'Raw process properties source end inset12px'
                assert ok_row and abs(bounds.origin.y-row_bounds.origin.y-row_bounds.size.height-2)<=2,'Raw process properties source top gap2px'
                check_property_gaps(grid)
                print('PASS MONITOR raw process properties: actual insets, row flow and key/value gaps',flush=True)
            except Exception:failures.append(traceback.format_exc())
            finally:w.close();app.quit()
            return GLib.SOURCE_REMOVE
        def open_process_details():
            w=app.get_active_window()
            try:
                for name in ('mn-all','mn-process-3113','mn-details'):
                    button=next(c for c in descendants(w) if c.get_name()==name and c.get_mapped())
                    assert isinstance(button,Gtk.Button),name
                    button.emit('clicked')
                GLib.timeout_add(400,check_process_details)
            except Exception:
                failures.append(traceback.format_exc());w.close();app.quit()
            return GLib.SOURCE_REMOVE
        def check_details():
            w=app.get_active_window()
            try:
                assert w.get_width()==(390 if '--phone' in sys.argv else 1180),w.get_width()
                heading=next(c for c in descendants(w.list) if isinstance(c,Gtk.Label) and c.get_label()=='Processes')
                parent=heading.get_parent();ok,bounds=heading.compute_bounds(parent)
                assert ok and abs(bounds.origin.x-12)<=2,('Processes source inset12px',ok,bounds.origin.x)
                properties=next(c for c in descendants(parent) if c.get_parent() is parent and c.has_css_class('mn-properties'))
                ok_props,props_bounds=properties.compute_bounds(parent)
                assert ok_props and abs(bounds.origin.y-props_bounds.origin.y-props_bounds.size.height-16)<=2,('Properties bottom margin10px plus heading top6px',bounds.origin.y,props_bounds.origin.y,props_bounds.size.height)
                process=next(c for c in descendants(parent) if isinstance(c,Gtk.Button) and c.has_css_class('mn-child-process'))
                name=next(c for c in descendants(process) if isinstance(c,Gtk.Label) and c.has_css_class('mn-mono'))
                ok_name,name_bounds=name.compute_bounds(parent);ok_row,row_bounds=process.compute_bounds(parent)
                assert ok_name and abs(name_bounds.origin.x-bounds.origin.x)<=2,'Processes heading must align with child names'
                assert ok_row and abs(row_bounds.origin.y-bounds.origin.y-bounds.size.height-2)<=2,'Heading bottom gap must match source2px'
                check_property_gaps(properties)
                print('PASS MONITOR actual details allocation: heading inset/flow and child-name alignment',bounds.origin.x,bounds.origin.y,flush=True)
                GLib.timeout_add(200,open_process_details)
            except Exception:
                failures.append(traceback.format_exc());w.close();app.quit()
            return GLib.SOURCE_REMOVE
        def expand_details():
            w=app.get_active_window()
            try:
                button=next(c for c in descendants(w.list) if c.get_name()=='mn-expand-viola' and c.get_mapped())
                assert isinstance(button,Gtk.Button)
                button.emit('clicked');GLib.timeout_add(400,check_details)
            except Exception:
                failures.append(traceback.format_exc());w.close();app.quit()
            return GLib.SOURCE_REMOVE
        GLib.timeout_add(1000,expand_details);app.run([])
        assert not failures,'\n'.join(failures)
        assert not app.get_windows(),'Details allocation check left a window open'
        return

    if '--row-layout' in sys.argv:
        from gi.repository import Gtk
        from luma_monitor.application import descendants
        if '--phone' not in sys.argv:app.connect_after('activate',lambda a:a.get_active_window().set_default_size(1180,740))
        def check_rows():
            w=app.get_active_window()
            try:
                assert w.get_width()==(390 if '--phone' in sys.argv else 1180),w.get_width()
                buttons=[c for c in descendants(w.list) if c.get_name().startswith('mn-expand-')]
                assert len(buttons)==10,len(buttons)
                for button in buttons:
                    glyph=next(c for c in descendants(button) if isinstance(c,Gtk.Image))
                    ok,bounds=glyph.compute_bounds(button)
                    assert ok and abs(bounds.origin.x+bounds.size.width/2-button.get_width()/2)<=2,(button.get_name(),bounds.origin.x,bounds.size.width,button.get_width())
                    assert abs(bounds.origin.y+bounds.size.height/2-button.get_height()/2)<=2,(button.get_name(),bounds.origin.y,bounds.size.height,button.get_height())
                print('PASS MONITOR disclosure allocation: ten real glyphs centred in their actual buttons',flush=True)
            except Exception:failures.append(traceback.format_exc())
            finally:w.close();app.quit()
            return GLib.SOURCE_REMOVE
        GLib.timeout_add(1000,check_rows);app.run([])
        assert not failures,'\n'.join(failures)
        assert not app.get_windows(),'Disclosure allocation check left a window open'
        return

    if '--actions' in sys.argv or '--activity-layout' in sys.argv:
        # Functional evidence stays available while the full smoke retains its
        # shared geometry failures. Every transition uses the named GtkButton
        # and yields to the native main loop before finding the next control.
        if '--phone' not in sys.argv:app.connect_after('activate',lambda a:a.get_active_window().set_default_size(1180,740))
        GLib.timeout_add(1000,lambda:action_checks(geometry=False))
        app.run([])
        assert not failures,'\n'.join(failures)
        assert not app.get_windows(), 'Named-button action check left a window open'
        print('PASS MONITOR named-button actions: select, priority submenu/Low, Stop/Continue, files/maps/close; windows closed; separate from full geometry')
        return

    from luma_monitor.application import descendants
    resources=iter(('cpu','mem','disk','net','en'))
    def next_resource():
        w=app.get_active_window()
        try:
            resource=next(resources)
        except StopIteration:
            return action_checks()
        w._resource(resource);w._toggle_all()
        def check_viewport():
            try:
                assert w.all_processes
                process_rows=[c for c in descendants(w) if c.has_css_class('mn-process-row')]
                assert process_rows, 'All processes must allocate process rows'
                assert all(abs(c.get_height()-(36 if c.has_css_class('mn-child-process') else 38))<=2
                           for c in process_rows), [(c.get_name(),c.get_height()) for c in process_rows]
                if '--phone' in sys.argv:
                    assert w.get_width()==390,(resource,w.get_width())
                    assert not w.sidebar.get_visible()
                    assert all(not c.get_visible() for c in w._narrow_widgets),(resource,w.get_width(),[(c.get_css_name(),c.get_name(),getattr(c,'get_label',lambda:'')()) for c in w._narrow_widgets if c.get_visible()])
                    assert w.chart.get_width()<=330,(resource,w.chart.get_width())
                if resource=='cpu':
                    expected=4 if '--phone' in sys.argv else 8
                    positions=[w.core_grid.query_child(c)[:2] for c in descendants(w.core_grid) if c.get_parent() is w.core_grid]
                    assert len(positions)==8 and max(x for x,y in positions)==expected-1,positions
                    assert w.core_grid.get_height()==(110 if '--phone' in sys.argv else 70),w.core_grid.get_height()
                elif resource in ('mem','disk','net','en'):
                    expected=2 if '--phone' in sys.argv else 4
                    assert max(w.advanced_grid.query_child(c)[0] for c in descendants(w.advanced_grid) if c.get_parent() is w.advanced_grid)==expected-1
                if resource in ('disk','net','en'):
                    cells=next(c for c in descendants(w.hero) if c.get_name()=='mn-resource-cells')
                    widths=[c.get_width() for c in descendants(cells) if c.get_parent() is cells]
                    assert len(widths)==3,widths
                    if '--phone' not in sys.argv:
                        assert abs(widths[0]-widths[1])<=1,widths
                        ratio=2 if resource=='en' else 1
                        assert abs(widths[2]-ratio*widths[0])<=2,widths
                    else:
                        from gi.repository import Gtk
                        children=[c for c in descendants(cells) if c.get_parent() is cells]
                        assert all(width>=child.measure(Gtk.Orientation.HORIZONTAL,-1)[0]
                                   for child,width in zip(children,widths)),widths
                        assert cells.get_width()<=330,cells.get_width()
                        assert sum(widths)+20>=cells.get_width(),(widths,cells.get_width())
                        bounds=[child.compute_bounds(cells)[1] for child in children]
                        assert all(abs(right.origin.x-left.origin.x-left.size.width-10)<=1
                                   for left,right in zip(bounds,bounds[1:])),widths
                if resource=='en' and os.environ.get('LUMA_MONITOR_RUNTIME_METRICS'):
                    from gi.repository import Gtk
                    for c in descendants(w.hero):
                        if isinstance(c,Gtk.Grid):
                            print('Energy grid',c.measure(Gtk.Orientation.HORIZONTAL,-1)[:2],c.get_width())
                            for cell in descendants(c):
                                if cell.get_parent() is c:print('cell',c.query_child(cell),cell.measure(Gtk.Orientation.HORIZONTAL,-1)[:2],cell.get_width())
                checked.append(resource);w._toggle_all()
            except Exception:
                failures.append(traceback.format_exc());app.quit();return GLib.SOURCE_REMOVE
            GLib.timeout_add(100,next_resource);return GLib.SOURCE_REMOVE
        GLib.timeout_add(150,check_viewport);return GLib.SOURCE_REMOVE
    GLib.timeout_add(1000,next_resource);app.run([])
    assert not failures,'\n'.join(failures)
    assert checked==['cpu','mem','disk','net','en'],checked
    print('PASS MONITOR fixture runtime: five resources, selection, details, menus, priority, sheets, confirmation, pause/resume, quit/reopen, search; no live source')


def parent():
    before=FIXTURE.read_bytes()
    with tempfile.TemporaryDirectory(prefix='monitor-runtime-') as tmp:
        tmp=Path(tmp);runtime=Path(os.environ.get('XDG_RUNTIME_DIR',f'/run/user/{os.getuid()}'))
        bus_path=tmp/'session.bus';conf=tmp/'bus.conf'
        conf.write_text('<busconfig><type>session</type><auth>EXTERNAL</auth><policy context="default"><allow send_destination="*"/><allow receive_sender="*"/><allow own="*"/></policy></busconfig>')
        env={k:v for k,v in os.environ.items() if k not in ('DISPLAY','WAYLAND_DISPLAY','DBUS_SESSION_BUS_ADDRESS')}
        display='monitor-runtime-'+str(os.getpid())
        env.update(XDG_RUNTIME_DIR=str(runtime),DBUS_SESSION_BUS_ADDRESS='unix:path='+str(bus_path),GSETTINGS_BACKEND='memory',GTK_A11Y='none',PYTHONPATH=str(ROOT/'src/luma-monitor')+':'+str(ROOT/'src/luma-platform/appkit'),LUMA_MONITOR_FIXTURE=str(FIXTURE),LUMA_MONITOR_PREVIEW='1',WAYLAND_DISPLAY=display,GDK_BACKEND='wayland')
        if 'LUMA_MONITOR_RUNTIME_PYTHONPATH' in os.environ:
            roots=os.environ['LUMA_MONITOR_RUNTIME_PYTHONPATH'].split(os.pathsep)
            assert all(p and Path(p).is_absolute() and Path(p).is_dir() for p in roots),'Selected runtime roots must be existing absolute directories'
            env['PYTHONPATH']=os.environ['LUMA_MONITOR_RUNTIME_PYTHONPATH']
        for name,leaf in [('XDG_CONFIG_HOME','config'),('XDG_STATE_HOME','state'),('XDG_CACHE_HOME','cache'),('XDG_DATA_HOME','data')]:env[name]=str(tmp/leaf)
        if '--contrast' in sys.argv:env['ADW_DEBUG_HIGH_CONTRAST']='1'
        if '--light' in sys.argv or '--dark' in sys.argv:
            env['ADW_DEBUG_COLOR_SCHEME']='prefer-dark' if '--dark' in sys.argv else 'prefer-light'
        if '--live' in sys.argv or '--kit-tests' in sys.argv or '--app-tests' in sys.argv or '--identity' in sys.argv or '--identity-machine' in sys.argv:
            env.pop('LUMA_MONITOR_FIXTURE',None)
        if '--identity' in sys.argv or '--identity-machine' in sys.argv:
            env.pop('LUMA_MONITOR_PREVIEW',None)
        bus=mutter=None
        with (tmp/'compositor.log').open('w') as log:
            try:
                bus=subprocess.Popen(['systemd-socket-activate','-E','DBUS_SESSION_BUS_ADDRESS','-E','XDG_RUNTIME_DIR','-l',str(bus_path),'dbus-broker-launch','--scope=user','--config-file='+str(conf)],env=env,stdout=log,stderr=log)
                for _ in range(100):
                    if bus_path.exists():break
                    time.sleep(.05)
                assert bus_path.exists(),'Private session bus failed'
                mutter=subprocess.Popen(['mutter','--headless','--wayland','--no-x11','--wayland-display='+display,'--virtual-monitor','1600x1000'],env=env,stdout=log,stderr=log)
                for _ in range(100):
                    if (runtime/display).exists():break
                    time.sleep(.1)
                assert (runtime/display).exists(),'Private compositor failed'
                result=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--child']+([x for x in ('--phone','--live','--contrast','--light','--dark','--kit-tests','--app-tests','--identity','--identity-machine','--inspection','--actions','--row-layout','--search-layout','--detail-layout','--activity-layout','--more-layout','--more-spacing','--late-inspection') if x in sys.argv]),env=env,capture_output=True,text=True,timeout=90 if '--kit-tests' in sys.argv or '--app-tests' in sys.argv else 30)
                print(result.stdout);print(result.stderr)
                assert result.returncode==0,f'Fixture runtime exited {result.returncode}'
            finally:
                for process in (mutter,bus):
                    if process and process.poll() is None:
                        process.terminate()
                        try:process.wait(5)
                        except subprocess.TimeoutExpired:process.kill();process.wait(5)
                assert FIXTURE.read_bytes()==before,'Fixture was written'

if __name__=='__main__':child() if '--child' in sys.argv else parent()
