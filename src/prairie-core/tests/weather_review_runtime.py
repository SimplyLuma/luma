"""No mobile sidebar; locations remain reachable through the actual Places control."""
import os,time
from pathlib import Path
root=Path(__file__).resolve().parents[3]
os.environ.update(LUMA_WEATHER_FIXTURE=str(root/'tests/fixtures/weather-v71.json'),
                  LUMA_WEATHER_STYLE_PATH=str(root/'src/prairie-core/style/weather.css'),
                  LUMA_WEATHER_VIEW='days', LUMA_FORM_FACTOR='desktop', GSETTINGS_BACKEND='memory')
from gi.repository import Gio,GLib,Gtk
Gio.Settings.new('org.project_luma.shell-state').set_string('surface-treatment','dark')
Gio.Settings.new('org.gnome.desktop.interface').set_string('color-scheme','prefer-dark')
from prairie_apps.weather import WeatherApplication,WeatherWindow

def settle(seconds=.65):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
        time.sleep(.005)

def capture(w,name):
    out=os.environ.get('WEATHER_REVIEW_SHOTS')
    if not out:return
    snap=Gtk.Snapshot();Gtk.WidgetPaintable.new(w).snapshot(snap,w.get_width(),w.get_height())
    assert w.get_renderer().render_texture(snap.to_node(),None).save_to_png(str(Path(out)/name))

app=WeatherApplication();app.set_application_id('org.projectluma.Weather.ReviewTest');assert app.register(None)
for initial in (402,1180):
    w=WeatherWindow(app);w.set_default_size(initial,874);w.present();settle()
    assert w.places and w.snapshots
    for width in (initial,680,402,360,1180):
        w.set_default_size(width,874);settle()
        if width<560:
            assert w.phone and not w.sidebar.get_mapped() and w.toggle._drawer is None
            w.toggle.toggle();settle(.15)
            assert w.toggle._drawer is None, 'F9 must not open a phone sidebar'
            assert w.page.get_margin_top()==12+w.status_inset
            if width==402:capture(w,'weather-days-narrow-after.png')
            w._open_places();settle()
            assert w.center.grown=='places' and w.phone_search.entry.get_mapped()
            if width==402:capture(w,'weather-places-narrow-after.png')
            target=next(p for p in w.places if p.uid!=w.selected)
            w._pick_place(target.uid);settle()
            assert w.selected==target.uid
            w.center.fold();settle()
        elif width==680:
            w.toggle.toggle();settle()
            assert w.toggle.shown, 'compact navigation should remain available'
            # Deliberately leave open: crossing to phone must dismiss it.
        else:
            assert w.sidebar.get_mapped()
        print(f'PASS Weather initial{initial} at{width}px',flush=True)
    w.set_default_size(402,874);settle();w._open_places();settle()
    w.phone_search.entry.grab_focus();w.phone_search.set_text('Paris');w.phone_search.search_now();settle()
    assert w.phone_search.results and w.phone_search.pick()
    settle();assert any(p.name=='Paris' for p in w.places)
    # Empty-state Add must also use Places, never the sidebar.
    w.fixture.samples.clear();w.places=();w.snapshots={};w.selected='';w._render();settle()
    w.empty.primary_button.emit('clicked');settle()
    assert w.phone_search.entry.get_mapped() and w.toggle._drawer is None
    w.close();settle()
app.quit()
