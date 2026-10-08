#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Fixture composition smoke test. Never presents a window; pixels use conform."""
import os
from pathlib import Path
import tempfile
import time

ROOT=Path(__file__).resolve().parents[2]
with tempfile.TemporaryDirectory(prefix='photos-composition-') as directory:
    for kind in ('CACHE','DATA','CONFIG','STATE'):
        os.environ[f'XDG_{kind}_HOME']=str(Path(directory)/kind.lower())
    os.environ['GSETTINGS_BACKEND']='memory'
    os.environ['LUMA_PHOTOS_FIXTURE']=str(ROOT/'tests/fixtures/photos-v70.json')
    os.environ['LUMA_PHOTOS_STYLE_PATH']=str(ROOT/'src/prairie-core/style/photos.css')
    from prairie_apps import photos as p
    production_id=p.APP_ID
    fixture=os.environ.pop('LUMA_PHOTOS_FIXTURE')
    override=os.environ.pop('LUMA_PHOTOS_APP_ID',None)
    non_unique=os.environ.pop('LUMA_PHOTOS_NON_UNIQUE',None)
    try:
        assert p.PhotosApplication().get_application_id()==production_id
        # Exercise the shared preview launcher's actual module-level hook
        # without registering an application or presenting any window.
        p.APP_ID=production_id+'.LumaUIPreview'
        assert p.PhotosApplication().get_application_id()==p.APP_ID
        assert p.APP_ID!=production_id,'Preview must not activate the installed Photos app'
        os.environ['LUMA_PHOTOS_FIXTURE']=fixture
        assert p.PhotosApplication().get_application_id()==p.APP_ID,'Fixture preview suffix must not be doubled'
        os.environ['LUMA_PHOTOS_APP_ID']='org.projectluma.Photos.CompositionOverride'
        assert p.PhotosApplication().get_application_id()=='org.projectluma.Photos.CompositionOverride'
    finally:
        p.APP_ID=production_id
        os.environ['LUMA_PHOTOS_FIXTURE']=fixture
        if override is None:os.environ.pop('LUMA_PHOTOS_APP_ID',None)
        else:os.environ['LUMA_PHOTOS_APP_ID']=override
        if non_unique is not None:os.environ['LUMA_PHOTOS_NON_UNIQUE']=non_unique
    application_class=p.PhotosApplication
    invocation_argv=p.sys.argv
    invocations=[]
    class OpenInvocationProbe:
        def run(self,argv):
            invocations.append(list(argv))
            return 42
    try:
        p.PhotosApplication=OpenInvocationProbe
        p.sys.argv=['prairie-photos','file:///fixture/photo.jpg']
        assert p.main()==42
        assert invocations[-1]==p.sys.argv,'First-launch file argument must reach the open handler'
        explicit=['prairie-photos','file:///fixture/other.jpg']
        assert p.main(explicit)==42 and invocations[-1]==explicit
    finally:
        p.PhotosApplication=application_class
        p.sys.argv=invocation_argv
    app=p.PhotosApplication();app.set_default();app.register(None)
    window=p.PhotosWindow(app)
    def settle(predicate):
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            p.GLib.MainContext.default().iteration(False)
            if predicate():return
            time.sleep(.002)
        raise AssertionError('Photos composition did not settle')
    try:
        settle(lambda:window.state is not None)
        assert not getattr(window,'last_error',''),getattr(window,'last_error','')
        assert window.state.total==22
        assert not window.get_visible(),'Runtime tests must never present on the live desktop'
        window._share_record=window.state.records[0]
        def forbidden_clipboard():
            raise AssertionError('Fixture Copy must not access the real clipboard')
        clipboard=window.get_clipboard
        window.get_clipboard=forbidden_clipboard
        try:
            assert window._share_choice('copy',None).startswith('Library copied.')
        finally:window.get_clipboard=clipboard
        assert window.body is not window.photo_body
        rows=[];row=window.sidebar.list.get_first_child()
        while row:
            if hasattr(row,'destination'):rows.append(row)
            row=row.get_next_sibling()
        assert [row.destination for row in rows]==['library','fav','people','places','launch','walls','deleted']
        assert window.new_album_button.get_ancestor(p.Gtk.ListBoxRow) is not None
        fixture_bytes=Path(fixture).read_bytes()
        def library_badge():
            row=window.sidebar.list.get_first_child()
            while row is not None and getattr(row,'destination',None)!='library':
                row=row.get_next_sibling()
            assert row is not None
            assert isinstance(row.trail_widget,p.Gtk.Label)
            return row.trail_widget.get_label()
        assert library_badge()=='22'
        os.environ['LUMA_PHOTOS_VIEWER']='0'
        os.environ['LUMA_PHOTOS_DELETE']='1'
        try:
            window._apply_fixture_state()
            assert window.state.library_count==21 and library_badge()=='21',\
                'Fixture Delete must refresh the visible Library count'
            assert Path(fixture).read_bytes()==fixture_bytes,'Fixture Delete must stay in memory'
            window.state.undo_trash()
            window.state.apply_page(window.state.read_page())
            window.state.close_viewer()
            window._render_sidebar();window._render_library()
            os.environ['LUMA_PHOTOS_UNDO_DELETE']='1'
            window._apply_fixture_state()
            assert window.state.library_count==22 and library_badge()=='22',\
                'Fixture Delete/Undo must restore the visible Library count'
            assert Path(fixture).read_bytes()==fixture_bytes,'Fixture Undo must stay in memory'
            window.state.close_viewer();window._render_library()
        finally:
            for key in ('LUMA_PHOTOS_VIEWER','LUMA_PHOTOS_DELETE','LUMA_PHOTOS_UNDO_DELETE'):
                os.environ.pop(key,None)
        window.request_import()
        assert isinstance(window.menu,p.FloatingMenu)
        assert [button.get_sensitive() for button in window.menu.buttons]==[True,False]
        window._dismiss_menu()
        window.request_new_album()
        assert isinstance(window.menu,p.FloatingMenu)
        window._dismiss_menu()
        breakpoint=window.get_current_breakpoint
        window.get_current_breakpoint=lambda:window.phone_breakpoint
        try:
            window.request_new_album()
            assert isinstance(window.menu,p.kit.MenuDrawer)
            def find_field(widget):
                if isinstance(widget,p.TextField):return widget
                child=widget.get_first_child()
                while child:
                    found=find_field(child)
                    if found is not None:return found
                    child=child.get_next_sibling()
            assert find_field(window.menu) is not None,'Phone menu must retain its custom content'
            window._dismiss_menu()
            assert not window.get_visible()
        finally:window.get_current_breakpoint=breakpoint
        window.state.open_photo('0');window.state.begin_edit()
        # Probe the app's asynchronous completion contract with a real GTK
        # picture/texture; this is not an IM3 rendering or package PASS claim.
        from gi.repository import GdkPixbuf
        class AdjustmentPicture(p.Gtk.Picture):
            def set_adjustments(self,values):
                self.values=dict(values)
        picture=AdjustmentPicture()
        window.viewport=picture
        window.state.draft={'e':10}
        decoding_values=dict(window.state.draft)
        window._adjustment_changed('e',42)
        pixbuf=GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB,False,8,2,2)
        pixbuf.fill(0xffffffff)
        window._image_loaded(window._image_generation,picture,pixbuf,'',decoding_values)
        assert picture.get_paintable() is not None,'Completed decode must install its native texture'
        assert picture.values['e']==42,'Late decode must retain the slider change made while decoding'
        other=AdjustmentPicture()
        window._image_loaded(window._image_generation,other,pixbuf,'',{'pre':'mono'})
        assert other.values=={'pre':'mono'},'Other thumbnail completions retain their own settings'
        paintable=picture.get_paintable()
        window._image_loaded(window._image_generation-1,picture,pixbuf,'',{'e':-42})
        assert picture.get_paintable() is paintable and picture.values['e']==42,'Obsolete decode must not replace current preview'
        window.state.edit_mode='light';window._edit_controls()
        assert set(window._sliders)=={'e','c','hi','sh'}
        # The slider event uses the shared viewport only once IM3 is present;
        # the pure state/save tests verify the adjustment changes in isolation.
        window._render_editor()
        assert window.editor.body is window._slider_groups['light']
        window.state.edit_mode='colour';window._render_editor()
        assert set(window._sliders)=={'e','c','hi','sh','w','sat'}
        window.state.edit_mode='looks';window._render_editor()
        looks=window.editor.body
        window.state.edit_mode='light';window._render_editor()
        window.state.edit_mode='looks';window._render_editor()
        assert window.editor.body is looks
        assert looks.get_child_at_index(5) is not None and looks.get_child_at_index(6) is None
        window.state.edit_mode='crop';window._render_editor()
        window.state.cancel_edit();window.state.close_viewer()
        # v70 keeps Information's wish while Edit temporarily hides its pane.
        # Exercise the real pane's notify and close callbacks, without IM3 or
        # presenting a window; this is not a viewer-rendering PASS claim.
        window.state.open_photo('0')
        record=window.state.current
        window.state.info=True
        window._information(record)
        assert window.details.shown and window.state.info
        assert window.state.begin_edit()
        window.details.show(open=False,subject=record.id)
        assert not window.details.shown
        assert window.state.info,'Edit must retain the wish to reopen Information'
        window.state.cancel_edit();window._information(record)
        assert window.details.shown and window.state.info,'Cancel must restore Information'
        assert window.state.begin_edit()
        window.details.close_button.emit('clicked')
        assert not window.details.shown and window.state.info,'Native close during Edit must retain Information'
        window.state.cancel_edit();window._information(record)
        window.details.close_button.emit('clicked')
        assert not window.details.shown and not window.state.info,'Explicit viewing close must clear Information'
        def fact_labels(widget):
            found=[widget.get_label()] if isinstance(widget,p.Gtk.Label) else []
            child=widget.get_first_child()
            while child:
                found.extend(fact_labels(child));child=child.get_next_sibling()
            return found
        window.state.info=True
        assert window.state.begin_edit()
        window.state.draft={'e':10}
        window.state.adjustments[record.id]=window.state.save_edit(record.id,window.state.baseline,window.state.draft)
        window.state.cancel_edit();window._information(record)
        labels=fact_labels(window.details.body)
        assert 'Taken' in labels,'Information must actually populate its native facts'
        assert labels.count('Edits')==1 and labels.count('Yes · original kept')==1,'Saved edits need v70 original-preserved fact'
        assert window.state.begin_edit()
        window.state.revert_edit()
        window.state.adjustments[record.id]=window.state.save_edit(record.id,window.state.baseline,window.state.draft)
        window.state.cancel_edit();window._information(record)
        assert 'Edits' not in fact_labels(window.details.body),'Reverting must remove the saved-edit fact'
        window.state.close_viewer()
        # Invoke the published More command and requery the actual fixture
        # collection. This checks app wiring without substituting for IM3.
        fixture_bytes=Path(fixture).read_bytes()
        window.state.switch_view('launch');window._load()
        settle(lambda:window.state.view=='launch' and window.state.total==len(window.state.source.rows('launch')))
        launch_ids={record.id for record in window.state.records}
        candidate=next(record for record in window.state.source.rows('library') if record.id not in launch_ids)
        window.state.switch_view('library');window._load()
        settle(lambda:window.state.total==22)
        window.state.open_photo(candidate.id)
        assert window._more_commands().invoke('photos.add-to-album'),'More must expose its album command'
        window.state.close_viewer();window.state.switch_view('launch');window._load()
        settle(lambda:window.state.total==len(launch_ids)+1)
        assert {record.id for record in window.state.records}==launch_ids|{candidate.id},'More must add only the selected photo and retain existing members'
        window.state.open_photo(candidate.id)
        assert window._more_commands().invoke('photos.add-to-album')
        window.state.close_viewer();window._load()
        settle(lambda:window.state.total==len(launch_ids)+1)
        assert len(window.state.records)==len(launch_ids)+1,'Repeated Add must not duplicate album membership'
        assert Path(fixture).read_bytes()==fixture_bytes,'Fixture actions must leave the source document untouched'
        window.request_new_album()
        def descendants(widget):
            yield widget
            child=widget.get_first_child()
            while child:
                yield from descendants(child);child=child.get_next_sibling()
        field=next(widget for widget in descendants(window.menu) if isinstance(widget,p.TextField))
        field.text='Callback album'
        create=next(widget for widget in descendants(window.menu) if isinstance(widget,p.Gtk.Button)
                    and any(isinstance(child,p.Gtk.Label) and child.get_label()=='Create' for child in descendants(widget)))
        create.emit('clicked')
        settle(lambda:any(album.name=='Callback album' and album.id==window.state.view for album in window.state.albums))
        album=next(album for album in window.state.albums if album.name=='Callback album')
        assert window.state.total==0 and not window.state.records,'Create must select the empty new collection'
        destinations=[widget.destination for widget in descendants(window.sidebar.list) if hasattr(widget,'destination')]
        assert destinations.index('walls')<destinations.index(album.id)<destinations.index('deleted'),'New album must follow Wallpapers and precede Deleted'
        assert window.state.library_count==22 and Path(fixture).read_bytes()==fixture_bytes,'Creating an album must preserve library photos and fixture source'
        window.state.switch_view('fav');window._load()
        settle(lambda:window.state.total==6)
        assert len(window.state.records)==6
        window._set_zoom('years')
        assert window.photo_body.get_first_child() is not None
        years=window.photo_body.get_first_child()
        window._set_zoom('days');window._set_zoom('years')
        assert window.photo_body.get_first_child() is years
        assert years.get_child_at_index(2) is not None and years.get_child_at_index(3) is None
        window.state.switch_view('deleted');window._load()
        settle(lambda:window.state.total==0)
        assert window.meta._meta==['Nothing here']
        assert not window._monitors,'Fixture mode must not install source monitors'
        print('PASS: fixture composition, library destinations, albums action, favourites, years and empty state')
    finally:
        window._close_requested();window.destroy()
