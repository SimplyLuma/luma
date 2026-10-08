"""Real GTK sign-in modal, synthetic transport only; no daemon or account opened."""
import json
import os
from pathlib import Path
import time
import gi
gi.require_version('Gtk','4.0');gi.require_version('Adw','1');gi.require_version('Gdk','4.0')
from gi.repository import Adw, Gdk, Gio, GLib, Gtk
from luma_appkit import AppWindow, Card, CommandRegistry, LayerHost, TextButton, TextField, TypeLabel
from luma_continuity.application import ConnectWindow

context=GLib.MainContext.default()
def pump(seconds=.2):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        while context.pending():context.iteration(False)
        time.sleep(.003)
def descendants(widget):
    yield widget
    child=widget.get_first_child()
    while child:
        yield from descendants(child);child=child.get_next_sibling()
def button(card,label):
    return next(w for w in descendants(card) if isinstance(w,TextButton) and w.label_widget.get_text()==label)
def field(card,label):
    return next(w for w in descendants(card) if isinstance(w,TextField) and w.label.get_text()==label)
class SyntheticCloud:
    def __init__(self):self.calls=[];self.done=None
    def connect(self,code,name,done):self.calls.append((code,name));self.done=done
app=Adw.Application(application_id='org.projectluma.Connect.SignInQualification',flags=Gio.ApplicationFlags.NON_UNIQUE)
assert app.register(None)
results=[]
appearance=Gio.Settings.new("org.project_luma.shell-state")
for dark,width in ((dark,width) for dark in (False,True) for width in (360,500,1024,1440)):
    assert appearance.set_string("surface-treatment","dark" if dark else "light")
    Gio.Settings.sync();pump()
    assert Adw.StyleManager.get_default().get_dark()==dark
    window=AppWindow(application=app,app_id='org.projectluma.Connect.SignInQualification',title='Luma Connect',
                     icon_name='org.projectluma.Connect',commands=CommandRegistry(()),
                     default_width=width,default_height=740,minimum_width=360,minimum_height=440)
    return_focus=TextButton('Open Sign In',style='raised');window.set_body(return_focus)
    cloud=SyntheticCloud();window.cloud_sync=cloud;observed=[]
    window._dialog_page=ConnectWindow._dialog_page
    window._say=lambda text:observed.append(('toast',text));window._refresh_cloud=lambda:observed.append(('refresh',))
    window.present();pump();window.set_default_size(width,740);pump(.4)
    return_focus.grab_focus()
    handle=ConnectWindow._sign_in_dialog(window);pump(.35)
    assert handle is not None and isinstance(handle.card,Card),'native dialog must use the actual shared Card/LayerHost'
    assert window.layer_host.modal is handle
    assert handle.card.measure(Gtk.Orientation.HORIZONTAL,-1)[0]<=window.get_width()
    assert handle.card.get_width()<=window.get_width(),(width,handle.card.get_width(),window.get_width())
    if width>=500:
        assert handle.card.get_width()<=500,('desktop modal is bounded',width,handle.card.get_width())
    assert not window.get_visible_dialog(),'no legacy Adw.Dialog layer'
    hub=button(handle.card,'Open Luma Hub')
    glyph=next(w for w in descendants(hub) if isinstance(w,Gtk.Image))
    assert glyph.get_icon_name()=='lumaui-arrow-up-right-symbolic'
    assert Gtk.IconTheme.get_for_display(window.get_display()).has_icon(glyph.get_icon_name()),'Hub glyph must actually ship'
    for image in (w for w in descendants(handle.card) if isinstance(w,Gtk.Image)):
        icon=image.get_icon_name()
        assert icon and Gtk.IconTheme.get_for_display(window.get_display()).has_icon(icon),('every form glyph ships',icon)
    button(handle.card,'Enter a Code…').emit('clicked');pump(.3)
    code=field(handle.card,'Code');name=field(handle.card,'Name for this computer');submit=button(handle.card,'Sign In')
    assert code.entry.get_has_frame()
    code.entry.grab_focus();pump()
    pages=next(w for w in descendants(handle.card) if isinstance(w,Gtk.Stack))
    for entry in (code.entry,name.entry):
        ok,bounds=entry.compute_bounds(pages);assert ok
        assert bounds.get_x()>=2 and bounds.get_x()+bounds.get_width()<=pages.get_width()-2,('whole field/focus ring fits stack',width,bounds,pages.get_width())
        assert bounds.get_y()>=2 and bounds.get_y()+bounds.get_height()<=pages.get_height()-2
    if width>=500:assert handle.card.get_width()<=500
    assert code.entry.get_accessible_role()==Gtk.AccessibleRole.TEXT_BOX
    assert not submit.get_sensitive()
    code.entry.set_text('short');code.entry.emit('activate');assert cloud.calls==[]
    code.entry.set_text('ABCD EFGH');name.entry.set_text('  Synthetic ThinkPad  ');pump()
    assert submit.get_sensitive() and submit.has_css_class('key') and not submit.has_css_class('suggested-action')
    button(handle.card,'Back').emit('clicked');pump();button(handle.card,'Enter a Code…').emit('clicked');pump()
    assert code.entry.get_text()=='ABCD EFGH'
    code.entry.emit('activate');code.entry.emit('activate')
    assert cloud.calls==[('ABCDEFGH','Synthetic ThinkPad')]
    assert not code.get_sensitive() and not name.get_sensitive() and not submit.get_sensitive()
    cloud.done('Synthetic server rejected this code');pump()
    assert submit.get_sensitive() and code.get_sensitive() and name.get_sensitive()
    assert any(isinstance(w,TypeLabel) and w.get_text()=='Synthetic server rejected this code' for w in descendants(handle.card))
    if width==360:
        settings=Gtk.Settings.get_default();original_font=settings.get_property('gtk-font-name')
        settings.set_property('gtk-font-name','Figtree 18');Gtk.Widget.set_default_direction(Gtk.TextDirection.RTL);pump(.3)
        assert handle.card.get_width()<=window.get_width()
        for entry in (code.entry,name.entry):
            ok,bounds=entry.compute_bounds(pages);assert ok
            assert bounds.get_x()>=2 and bounds.get_x()+bounds.get_width()<=pages.get_width()-2
        settings.set_property('gtk-font-name',original_font);Gtk.Widget.set_default_direction(Gtk.TextDirection.LTR)
        a11y=Gio.Settings.new('org.gnome.desktop.a11y.interface')
        assert a11y.set_boolean('high-contrast',True);Gio.Settings.sync();pump(.3)
        from luma_appkit import widgets
        assert widgets.appearance['name']=='high-contrast'
        assert handle.card.get_width()<=window.get_width()
        assert a11y.set_boolean('high-contrast',False);Gio.Settings.sync();pump()
    output=os.environ.get('LUMA_CONNECT_SMOKE_OUTPUT')
    if output:
        out=Path(output);out.mkdir(parents=True,exist_ok=True)
        snap=Gtk.Snapshot();paint=Gtk.WidgetPaintable.new(window);paint.snapshot(snap,window.get_width(),window.get_height())
        node=snap.to_node();assert node
        texture=window.get_renderer().render_texture(node,None);assert texture.save_to_png(str(out/f'signin-{width}-{"dark" if dark else "light"}.png'))
    if width==1024:
        window.set_default_size(360,740);pump(.4)
        assert handle.card.get_width()<=window.get_width(),('resize overflow',handle.card.get_width(),window.get_width())
        assert code.entry.get_text()=='ABCD EFGH'
        window.set_default_size(1024,740);pump(.4)
    submit.emit('clicked');cloud.done(None);pump()
    assert handle.closed and window.layer_host.modal is None
    assert observed==[('toast','Signed in. The first sync is running.'),('refresh',)]
    # Dismissal preserves focus and a delayed transport completion cannot
    # mutate a dismissed modal or falsely report success to the window.
    handle=ConnectWindow._sign_in_dialog(window);pump(.3)
    button(handle.card,'Enter a Code…').emit('clicked');pump()
    field(handle.card,'Code').entry.set_text('ABCDEFGH');button(handle.card,'Sign In').emit('clicked')
    previous=list(observed)
    assert handle._keys.emit('key-pressed',Gdk.KEY_Escape,0,Gdk.ModifierType(0))
    pump(.3);cloud.done(None);pump()
    assert observed==previous and handle.closed and window.layer_host.modal is None
    results.append({'dark':dark,'requested_width':width,'actual_width':window.get_width(),'validation':'pass','single_flight':'pass','error_retry':'pass','success':'pass','dismissed_callback':'pass'})
    window.destroy();pump()
assert len(results)==8
print(json.dumps({'result':'pass','cases':results},sort_keys=True))
