# SPDX-License-Identifier: Apache-2.0
"""Native comment submission retains scroll through real EDS invalidations."""
from datetime import datetime
from pathlib import Path
import os
import subprocess
import tempfile
import time

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib
from luma_appkit import add_style_sheet, install_appkit, install_lumaui
from prairie_apps.tasks import TasksWindow
from prairie_apps.tasks_backend import TasksRepository, read_task


def wait(predicate=lambda: True, seconds=10):
    first = time.monotonic()
    deadline = first + seconds
    loop = GLib.MainLoop()
    failure = []

    def check():
        try:
            if time.monotonic() - first > .35 and predicate():
                loop.quit()
                return GLib.SOURCE_REMOVE
            if time.monotonic() >= deadline:
                failure.append(AssertionError('Real EDS task workflow did not settle'))
                loop.quit()
                return GLib.SOURCE_REMOVE
        except Exception as error:
            failure.append(error)
            loop.quit()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    # Dispatch real provider invalidations, allocation and frame callbacks as
    # the application does. Sleeping after each individual context iteration
    # artificially limits callback throughput and may sample a pending rebuild.
    GLib.timeout_add(5, check)
    loop.run()
    if failure:
        raise failure[0]


def dispatch_for(seconds):
    loop = GLib.MainLoop()
    GLib.timeout_add(round(seconds * 1000), lambda: (loop.quit(), GLib.SOURCE_REMOVE)[1])
    loop.run()


def children(widget):
    yield widget
    child = widget.get_first_child()
    while child:
        yield from children(child)
        child = child.get_next_sibling()


def main():
    assert 'LUMA_TASKS_FIXTURE' not in os.environ
    # Use the exact production schema with a private, nonpersistent backend.
    with tempfile.TemporaryDirectory() as directory:
        schema = Path(os.environ['LUMA_CREATOR_SCHEMA_FILE'])
        Path(directory, schema.name).write_bytes(schema.read_bytes())
        subprocess.run(['glib-compile-schemas', '--strict', directory], check=True)
        os.environ['GSETTINGS_SCHEMA_DIR'] = directory
        os.environ['GSETTINGS_BACKEND'] = 'memory'
        seed = TasksRepository()
        seed.load()
        source = seed.create_list('Real comment acceptance')
        wait(lambda: source in {s.uid for s in seed.load()[0]})
        uid = seed.add(source, {'title': 'Real comment acceptance',
                               'due': datetime.now().astimezone()})
        for i in range(30):
            seed.comment(read_task(seed._get(source, uid), source),
                         'Earlier comment ' + str(i), 'Acceptance user')
        seed.close()
        install_appkit()
        install_lumaui()
        add_style_sheet(os.environ['LUMA_TASKS_STYLE_PATH'])
        app = Adw.Application(application_id='org.projectluma.Tasks.RealCommentAcceptance',
                              flags=Gio.ApplicationFlags.NON_UNIQUE)
        app.register(None)
        try:
            for width in (360, 500, 1024, 1440):
                window = TasksWindow(app)
                try:
                    window.set_default_size(width, 800)
                    window.present()
                    wait(lambda: window.get_mapped() and window.get_surface().get_width() == width
                         and any(t['t'] == 'Real comment acceptance' for t in window.data['tasks'])
                         and not window._loading)
                    key = next(t['id'] for t in window.data['tasks'] if t['t'] == 'Real comment acceptance')
                    window._pick(key)
                    wait()
                    scroller = window.phone_panel if window.phone else window.details.scroller
                    adjustment = scroller.get_vadjustment()
                    wait(lambda: adjustment.get_upper() - adjustment.get_page_size() > 100)
                    adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
                    wait()
                    submitted = 'Real EDS comment at width ' + str(width)
                    entry = next(w for w in children(window) if w.get_name() == 'tk-comment')
                    entry.set_text(submitted)
                    entry.emit('activate')
                    wait(lambda: not window.busy and not window._loading
                         and any(a[1] == 'c' and a[2] == submitted for t in window.data['tasks']
                                 if t['id'] == key for a in t['act']))
                    # Include the later provider objects-modified readback and GTK frames.
                    dispatch_for(1)
                    scroller = window.phone_panel if window.phone else window.details.scroller
                    adjustment = scroller.get_vadjustment()
                    assert adjustment.get_value() > 100, (width, adjustment.get_value(), adjustment.get_upper())
                    assert adjustment.get_upper() - adjustment.get_page_size() - adjustment.get_value() < 5, (
                        width, 'Provider readback hid the submitted comment')
                    # Deterministically exercise a late top-control focus, then
                    # another real-data redraw after the first reveal settled.
                    next(w for w in children(window) if w.get_name() == 'tk-detail-complete').grab_focus()
                    wait()
                    assert adjustment.get_upper() - adjustment.get_page_size() - adjustment.get_value() < 5
                    window._render()
                    wait()
                    scroller = window.phone_panel if window.phone else window.details.scroller
                    adjustment = scroller.get_vadjustment()
                    assert adjustment.get_upper() - adjustment.get_page_size() - adjustment.get_value() < 5
                    # Native scrolling explicitly returns control to the user.
                    from gi.repository import Gtk
                    controller = next(controller for owner, controller in window._comment_reveal_handlers
                                      if isinstance(controller, Gtk.EventControllerScroll))
                    controller.emit('scroll', 0., -1.)
                    adjustment.set_value(100)
                    wait()
                    assert window._comment_to_reveal is None
                    assert abs(adjustment.get_value() - 100) < 1
                    print('PASS native Tasks GTK + real EDS comment provider-readback scroll at width', width)
                finally:
                    window.close()
                    wait()
        finally:
            app.run_dispose()
            cleanup = TasksRepository()
            cleanup.load()
            cleanup.delete_list(source)
            cleanup.close()


if __name__ == '__main__':
    main()
