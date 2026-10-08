"""Log what a screen reader receives from the Shell: AT-SPI announcements and notifications."""
import sys, time
import gi
gi.require_version('Atspi', '2.0')
from gi.repository import Atspi

def cb(event):
    src = event.source
    try:
        name, role = src.get_name(), src.get_role_name()
    except Exception:
        name = role = None
    detail = event.any_data
    try:
        detail = detail if isinstance(detail, (str, int, float)) or detail is None else str(detail)
    except Exception:
        detail = None
    print(f'{time.time():.3f} {event.type} role={role!r} name={name!r} detail1={event.detail1} any={detail!r}', flush=True)

listener = Atspi.EventListener.new(cb)
for kind in ('object:announcement', 'object:notification', 'window:', 'object:state-changed:showing'):
    listener.register(kind)
print('listening', flush=True)
Atspi.event_main()
