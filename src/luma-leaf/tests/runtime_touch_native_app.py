# SPDX-License-Identifier: Apache-2.0
"""Ordinary-user Leaf half of the actual compositor touchscreen gate.

Run under an owned Wayland compositor. No sandbox override, injected gesture,
or reader function turns the page: the compositor supplies native touch events.
Only inspection and assertions pass through the private file protocol.
"""
import json
import hashlib
import sqlite3
import os
from pathlib import Path
import sys
import tempfile
import time

if os.getuid() == 0 or os.environ.get("WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS"):
    raise RuntimeError("native input acceptance requires ordinary user and WebKit sandbox")
control = Path(os.environ["LUMA_LEAF_TOUCH_CONTROL"])
control.mkdir(parents=True, exist_ok=True)
home = tempfile.TemporaryDirectory(prefix="leaf-native-touch-")
for variable, folder in (("HOME", ""), ("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache"),
                         ("XDG_CONFIG_HOME", "config"), ("LEAF_BOOKS", "Books")):
    os.environ[variable] = str(Path(home.name) / folder)
    Path(os.environ[variable]).mkdir(parents=True, exist_ok=True)
import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib
from tests.fixtures import BODY, write_reader_epub
from luma_leaf.application import LeafApplication

write_reader_epub(Path(os.environ["LEAF_BOOKS"]) / "touch.epub")
app = LeafApplication()
app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
failure = []
state = {"stage": "open", "until": time.monotonic() + 45, "cfi": None, "busy": False, "events": []}

def publish(kind, **values):
    temporary = control / "state.tmp"
    temporary.write_text(json.dumps({"kind": kind, "pid": os.getpid(), **values}))
    temporary.replace(control / "state.json")

def fail(error):
    failure.append(error)
    publish("error", error=str(error))
    app.quit()

def button_geometry(button):
    window = app.window
    native = button.get_native()
    ok, bounds = button.compute_bounds(native)
    assert ok and button.get_mapped() and bounds.size.width > 0 and bounds.size.height > 0
    tx, ty = native.get_surface_transform()
    surface = native.get_surface()
    origin = []
    while surface != window.get_surface():
        assert hasattr(surface, "get_parent"), "button surface cannot be located relative to window"
        px, py = surface.get_position_x(), surface.get_position_y()
        origin.append([px, py])
        tx += px
        ty += py
        surface = surface.get_parent()
    return {"x": bounds.origin.x + bounds.size.width / 2 + tx,
            "y": bounds.origin.y + bounds.size.height / 2 + ty,
            "native": type(native).__name__, "popup_origins": origin}

def inspect_geometry(kind):
    window = app.window
    reader = window.reader
    state["busy"] = True
    def inspected(value):
        state["busy"] = False
        try:
            assert value and value.get("text"), value
            ok, bounds = reader.web.compute_bounds(window)
            assert ok
            tx, ty = window.get_surface_transform()
            state["expected_word"] = value["text"]
            state["book_sha256"] = hashlib.sha256(Path(reader.book.path).read_bytes()).hexdigest()
            publish(kind, cfi=state["cfi"], web={"x": bounds.origin.x+tx,"y": bounds.origin.y+ty,
                    "width": bounds.size.width,"height": bounds.size.height}, text=value)
            state["stage"] = "input"
        except BaseException as error:
            fail(error)
    reader.call(r"""(()=>{const f=document.getElementById('frame'),d=f.contentDocument,w=d.createTreeWalker(d.body,NodeFilter.SHOW_TEXT),r=d.createRange(),o=f.getBoundingClientRect();let n;
      while(n=w.nextNode()){const m=n.textContent.match(/[\p{L}\p{N}]+/u);if(!m)continue;r.setStart(n,m.index);r.setEnd(n,m.index+m[0].length);const b=r.getBoundingClientRect();if(b.width>0&&b.left>=0&&b.right<d.defaultView.innerWidth&&b.top>=0&&b.bottom<d.defaultView.innerHeight)return{x:o.x+b.x+b.width/2,y:o.y+b.y+b.height/2,text:m[0]};}return null})()""", inspected)

def poll():
    try:
        if time.monotonic() > state["until"]:
            raise AssertionError(f"native touchscreen timed out at {state['stage']}")
        if app.window is None or state["busy"]:
            return True
        window = app.window
        if state["stage"] == "open":
            if len(app.library.books()) != 1:
                return True
            window.set_default_size(int(os.environ.get("LUMA_LEAF_TOUCH_WIDTH", "1160")), 700)
            window.set_title("Leaf native touchscreen acceptance")
            app.open_book(app.library.books()[0].id)
            if os.environ.get("LUMA_LEAF_TOUCH_FULLSCREEN") == "1": window.fullscreen()
            state["stage"] = "book"
        reader = window.reader
        if state["stage"] == "book" and reader.position:
            reader.run(f"leaf.goToChapter({BODY}, null)")
            state["stage"] = "chapter"
            state["settle"] = time.monotonic() + .7
        if state["stage"] == "chapter" and reader.position.get("spine") == BODY and time.monotonic() > state["settle"]:
            state["cfi"] = reader.position["cfi"]
            state["busy"] = True
            inspect_geometry("ready")
        command_file = control / "command"
        if state["stage"] == "input" and command_file.exists():
            command = command_file.read_text().strip()
            command_file.unlink()
            if command == "forward":
                assert reader.position["cfi"] != state["cfi"], "native left swipe did not advance"
                publish("forward", cfi=reader.position["cfi"])
            elif command == "back":
                assert reader.position["cfi"] == state["cfi"], "native right swipe did not return"
                inspect_geometry("back")
            elif command == "hold":
                state["busy"] = True
                def held(value):
                    try:
                        assert value == state["expected_word"], (value, state["expected_word"])
                        assert reader.selection and reader.selection.get("selected"), "native hold did not select text"
                        assert reader.selection["selected"].strip() == state["expected_word"], (reader.selection["selected"], state["expected_word"])
                        assert reader.selection_bubble.get_mapped(), "native hold exposes no text actions"
                        state["busy"] = False
                        publish("held", selection=reader.selection["selected"],
                                button=button_geometry(reader.selection_bubble._buttons["pencil" if os.environ.get("LUMA_LEAF_TOUCH_NOTE") == "1" else "highlighter"]))
                    except BaseException as error:
                        fail(error)
                reader.call("document.getElementById('frame').contentDocument.getSelection().toString()", held)
            elif command == "note_editor":
                assert reader.selection_popover.get_mapped() and reader.selection_stack.get_visible_child_name()=="note"
                assert reader.note_view.has_focus(), "native Note tap did not focus editor"
                def widgets(widget):
                    yield widget
                    child=widget.get_first_child()
                    while child is not None:
                        yield from widgets(child);child=child.get_next_sibling()
                save=next(w for w in widgets(reader.selection_popover) if hasattr(w,"get_label") and w.get_label()=="Save" and w.get_mapped())
                state["widgets"]=widgets
                publish("note_editor", text=button_geometry(reader.note_view),save=button_geometry(save))
            elif command == "noted":
                highlights=app.library.highlights(reader.book.id)
                assert len(highlights)==1 and highlights[0].note=="native touch note", "native keyboard/Save did not persist note"
                buttons=[w for w in state["widgets"](window) if w.get_mapped() and w.get_tooltip_text()=="Bookmark"]
                assert len(buttons)==1, ("mapped bookmark action",len(buttons))
                publish("noted", bookmark=button_geometry(buttons[0]),note=highlights[0].note)
            elif command == "pinned":
                marks=app.library.bookmarks(reader.book.id);assert len(marks)==1
                path=app.library.db.execute("PRAGMA database_list").fetchone()[2]
                with sqlite3.connect("file:"+path+"?mode=ro",uri=True) as persisted:
                    assert persisted.execute("SELECT note FROM highlights WHERE deleted=0").fetchall()==[("native touch note",)]
                    assert persisted.execute("SELECT count(*) FROM bookmarks WHERE deleted=0").fetchone()[0]==1
                assert hashlib.sha256(Path(reader.book.path).read_bytes()).hexdigest()==state["book_sha256"], "annotation modified EPUB"
                publish("pass",notes=1,bookmarks=1,native_note_keyboard_save=True,native_bookmark_tap=True,original_epub_unchanged=True)
                app.quit()
            elif command == "picker":
                assert reader.selection_popover.get_mapped(), "native Highlight tap did not open colour choices"
                publish("picker", button=button_geometry(reader.dot_buttons["y"]))
            elif command == "saved":
                assert len(app.library.highlights(reader.book.id)) == 1, "native colour tap did not save highlight"
                assert app.library.highlights(reader.book.id)[0].colour == "y"
                publish("pass", highlights=1, native_highlight_taps=True)
                app.quit()
            else:
                raise AssertionError(f"unknown input assertion {command}")
    except BaseException as error:
        fail(error)
        return False
    return True

app.connect_after("activate", lambda *_: GLib.timeout_add(80, poll))
try:
    app.run([])
    if failure:
        raise failure[0]
    print("LEAF NATIVE TOUCH APP PASS", flush=True)
finally:
    home.cleanup()
