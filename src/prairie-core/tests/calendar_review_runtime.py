"""Review regressions on disposable Calendar fixtures; no provider writes."""
import os,time,tempfile
from pathlib import Path
root=Path(__file__).resolve().parents[3]
os.environ.update(PRAIRIE_EDS_MODE='disabled', LUMA_CALENDAR_FIXTURE=str(root/'tests/fixtures/calendar-v70.json'),
                  LUMA_CALENDAR_STYLE_PATH=str(root/'src/prairie-core/style/calendar.css'),
                  LUMA_CALENDAR_SELECTED_EVENT='27', LUMA_CALENDAR_TZID='UTC', LUMA_FORM_FACTOR='desktop')
from prairie_apps import calendar
from prairie_apps.calendar_window import CalendarWindow
from gi.repository import Adw,GLib,Gtk,Gio
os.environ['GSETTINGS_BACKEND']='memory'
Gio.Settings.new('org.project_luma.shell-state').set_string('surface-treatment','dark')
Gio.Settings.new('org.gnome.desktop.interface').set_string('color-scheme','prefer-dark')

def settle():
    until=time.monotonic()+.6
    while time.monotonic()<until:
        while GLib.MainContext.default().pending(): GLib.MainContext.default().iteration(False)
        time.sleep(.005)

def find(widget,name):
    if widget.get_name()==name:return widget
    child=widget.get_first_child()
    while child:
        result=find(child,name)
        if result is not None:return result
        child=child.get_next_sibling()

def capture(win,name):
    out=os.environ.get('CALENDAR_REVIEW_SHOTS')
    if not out:return
    snap=Gtk.Snapshot();Gtk.WidgetPaintable.new(win).snapshot(snap,win.get_width(),win.get_height())
    texture=win.get_renderer().render_texture(snap.to_node(),None)
    assert texture.save_to_png(str(Path(out)/name))

calendar.APP_ID += '.ReviewTest'
app=calendar.CalendarApplication();assert app.register(None)
Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
for width in (360,402,720,1180):
    win=CalendarWindow(app);win.set_default_size(width,874);win.present();settle()
    assert win.loaded and win.selected_event
    if width<560:
        assert not win.title_island.has_css_class('flat')
        back=win.center.bar_row.get_first_child()
        assert back.get_width()<150,(width,back.get_width())
        assert not back.get_hexpand()
        ok,bounds=back.compute_bounds(win);assert ok and bounds.get_x()<40,(width,bounds.get_x())
        capture(win,f'calendar-event-{width}-after.png')
        back.emit('clicked');settle()
        assert win.selected_event is None
    win.date_menu();settle()
    today=find(win,'cal-picker-today');previous=find(win,'cal-year-prev');next_year=find(win,'cal-year-next')
    assert today and previous and next_year
    for button in (today,previous,next_year):
        assert button.has_css_class('raised')
        assert button.get_height()==36,(width,button.get_name(),button.get_height())
    year=int(find(win,'cal-picker-year').get_text())
    next_year.emit('clicked');settle()
    assert int(find(win,'cal-picker-year').get_text())==year+1
    find(win,'cal-year-prev').emit('clicked');settle()
    assert int(find(win,'cal-picker-year').get_text())==year
    for month in range(1,13):
        button=find(win,f'cal-month-{month}')
        assert button and button.has_css_class('raised')
        ok,bounds=button.compute_bounds(win)
        assert ok and bounds.get_x()>=0 and bounds.get_x()+bounds.get_width()<=win.get_width()
    capture(win,f'calendar-picker-{width}-after.png')
    find(win,'cal-month-11').emit('clicked');settle()
    assert win.anchor.month==11 and win.anchor.year==year
    win.date_menu();settle()
    find(win,'cal-picker-today').emit('clicked');settle()
    assert win.anchor==win.today, ('Today did not navigate',win.anchor,win.today)
    win.close();settle()
    print(f'PASS Calendar reviewed picker/back/header at {width}px',flush=True)
app.quit()
