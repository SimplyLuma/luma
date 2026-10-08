# SPDX-License-Identifier: Apache-2.0
"""Actual allocated share cards, long members and focus reachability."""
import os
from pathlib import Path
import time
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib, Gsk
from luma_appkit import ShareResult, ShareSheet, ShareSubject, Person, install_appkit, install_lumaui
from luma_appkit.bar_share import Collaborator
from luma_appkit.structure_layers import LayerHost

assert Gtk.init_check(), 'Actual GTK display is required.'
install_appkit(); install_lumaui()


def settle(predicate, label, seconds=5):
    loop = GLib.MainLoop(); deadline = time.monotonic() + seconds; failures = []
    def check():
        try:
            if predicate(): loop.quit(); return False
        except Exception as error:
            failures.append(str(error)); loop.quit(); return False
        if time.monotonic() >= deadline:
            failures.append(label); loop.quit(); return False
        return True
    GLib.timeout_add(20, check); loop.run()
    assert not failures, failures


def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child:
        yield from walk(child); child = child.get_next_sibling()


def allocated_frames(window, label):
    # Widget allocation and scroll ranges update on the native frame clock.
    # Observe them before using fresh controls or capturing cached pixels.
    frames = []
    callback = window.add_tick_callback(lambda *_: (frames.append(True), True)[1])
    try:
        settle(lambda: len(frames) >= 3, label)
    finally:
        window.remove_tick_callback(callback)


def snapshot(window, path):
    allocated_frames(window, 'native frames after allocation')
    paintable = Gtk.WidgetPaintable.new(window); nodes = []
    def pixels():
        view = Gtk.Snapshot(); paintable.snapshot(view, window.get_width(), window.get_height())
        node = view.to_node()
        if node: nodes.append(node)
        return node is not None
    settle(pixels, 'rendered pixels')
    renderer = Gsk.CairoRenderer.new(); renderer.realize(window.get_surface())
    try: assert renderer.render_texture(nodes[-1], None).save_to_png(str(path))
    finally: renderer.unrealize()


proof = os.environ.get('LUMA_SHARE_LAYOUT_EVIDENCE')
if proof: Path(proof).mkdir(parents=True, exist_ok=True)
for width, height in ((360,640),(500,370),(1024,800),(1440,900)):
    window = Gtk.Window(default_width=width, default_height=height)
    button = Gtk.Button(label='Share', halign=Gtk.Align.END, valign=Gtk.Align.START)
    host = LayerHost(button, name='window'); window.set_child(host); window.present()
    settle(lambda: host.get_width() > 0, 'real host allocation')
    members = [Collaborator(Person('Very long recipient name ' + str(i)*30, username='recipient'+str(i)), 'comment') for i in range(20)]
    owner = Person('Same display name', username='owner')
    candidates = [owner, members[0].person,
                  Person('Same display name', username='first'),
                  Person('Duplicate username', username='FIRST')]
    candidates += [Person('Long favorite name '*8+str(i), username='favorite'+str(i)) for i in range(8)]
    actions = []
    def choose(choice, value):
        actions.append((choice, value))
        return ShareResult(True)
    sheet = ShareSheet.present(button, document=ShareSubject('Long shared title '*40, kind='doc'),
        choices=('work-together','send-copy'), collaborators=members, people=candidates, owner=owner, on_choice=choose)
    settle(lambda: sheet.get_mapped() and sheet.get_height()>0, 'actual share card')
    assert [p.username for p in sheet._matches()] == ['first','favorite0','favorite1','favorite2'], 'empty input shows four unique people, excluding self and existing members'
    assert sheet.suggestions.get_visible()
    if proof: snapshot(window, Path(proof)/f'share-default-{width}.png')
    settle(lambda: sheet.scroller.get_vadjustment().get_upper() > sheet.scroller.get_vadjustment().get_page_size(), 'long membership scrolls')
    # Redraw and placement must not count old x/y margins as new content size.
    for _ in range(4): sheet._draw(); sheet.floater.place()
    def bounded():
        ok, rect = sheet.compute_bounds(host)
        return ok and rect.get_x() >= -1 and rect.get_y() >= -1 and rect.get_x()+rect.get_width() <= host.get_width()+1 and rect.get_y()+rect.get_height() <= host.get_height()+1
    settle(bounded, 'card stays wholly in the allocated host')
    # _draw replaces controls synchronously; GTK allocates their wrapped
    # content later. Scrolling an old adjustment range is not a user action
    # on the newly rendered sheet. Keep the reachability assertion unchanged.
    allocated_frames(window, 'redrawn membership allocation')
    last = [w for w in walk(sheet) if isinstance(w,Gtk.Button) and w.has_css_class('lumaui-share-role')][-1]
    assert last.grab_focus(), 'The final permission control must accept keyboard focus.'
    adjustment = sheet.scroller.get_vadjustment()
    adjustment.set_value(adjustment.get_upper()-adjustment.get_page_size())
    def visible():
        ok, rect = last.compute_bounds(sheet.scroller)
        return ok and rect.get_y() >= -1 and rect.get_y()+rect.get_height() <= sheet.scroller.get_height()+1
    settle(visible, 'last permission button remains reachable through scrolling')
    if proof: snapshot(window, Path(proof)/f'share-{width}.png')
    # GTK Text's actual activate signal is the keyboard Return action; it must
    # select the first visible default suggestion, without requiring a query.
    sheet.invite.emit('activate')
    assert actions[-1][0] == 'invite' and actions[-1][1].person.username == 'first'
    assert len(sheet.collaborators) == 21 and sheet.query == ''
    assert all(p.username != 'first' for p in sheet._matches())
    sheet.close(); window.destroy()
    print(f'PASS actual share {width}x{height}: default people, keyboard invite, bounded card, long members, final permission focus/scroll')

read_only = ShareSheet(ShareSubject('Read only', kind='doc'), choices=('work-together',), people=candidates, manage_access=False)
assert not read_only.suggestions.get_visible() and read_only._matches() == [], 'read-only viewers cannot receive invite controls'
refused = ShareSheet(ShareSubject('Rejected invite', kind='doc'), choices=('work-together',), people=candidates, owner=owner,
                    on_choice=lambda *_: ShareResult(False, 'Permission denied'))
before = refused._matches()
refused.invite.emit('activate')
assert refused.collaborators == [] and refused._matches() == before, 'a refused default invite cannot grant local access'
print('PASS readonly default controls and refused keyboard invitation')

# Keep the actual card open while the app replaces the list row containing its
# old Share control. Starting and acknowledging an invite must still show
# feedback through the existing window host, not raise on a detached button.
window = Gtk.Window(default_width=500, default_height=370)
button = Gtk.Button(label='Share'); content = Gtk.Box(); content.append(button)
host = LayerHost(content, name='window'); window.set_child(host); window.present()
settle(lambda: host.get_width() > 0, 'redraw host mapped')
sheet = ShareSheet.present(button, document=ShareSubject('Redrawn task list', kind='doc'),
    choices=('work-together',), people=[Person('Bob', username='bob')],
    on_choice=lambda *_: ShareResult(False, 'Updating sharing…'))
settle(lambda: sheet.get_mapped(), 'actual redraw card')
content.remove(button); content.append(Gtk.Button(label='Replacement list row'))
assert button.get_root() is None and sheet.get_root() is window
result = sheet._choose('invite', Collaborator(Person('Bob', username='bob'), 'edit'))
assert not result.completed and sheet.collaborators == []
assert any(isinstance(w, Gtk.Label) and w.get_label() == 'Updating sharing…' for w in walk(host)), 'pending feedback remains in persistent host'
sheet.close(); window.destroy()
print('PASS live row redraw preserves native pending-invite feedback')
