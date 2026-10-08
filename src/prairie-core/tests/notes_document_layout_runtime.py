# SPDX-License-Identifier: Apache-2.0
"""Paragraph layout and peer cursors are transient, editable document presentation."""
import os
import unittest
from unittest import mock
from notes_v71_phone_runtime import PhoneTests, pump
from gi.repository import Gtk

class DocumentLayoutTests(PhoneTests):
    def test_layout_and_peer_do_not_change_saved_content_or_selection(self):
        w=self.window
        w._phone_open(w.store.get_note('1'))
        pump()
        before=w.page.serialize()
        w.buffer.select_range(w.buffer.get_iter_at_offset(0),w.buffer.get_iter_at_offset(10))
        w.document_layout.refresh()
        self.assertEqual(w.page.serialize(),before)
        self.assertEqual(tuple(i.get_offset() for i in w.buffer.get_selection_bounds()),(0,10))
        self.assertEqual(w.editor.get_bottom_margin(),12)
        marks=w.page._collaborators
        self.assertEqual(marks['PR'][1],'Priya')
        mark=marks['PR'][0]
        offset=w.buffer.get_iter_at_mark(mark).get_offset()
        w.buffer.insert(w.buffer.get_start_iter(),'Hello ')
        pump()
        self.assertEqual(w.buffer.get_iter_at_mark(mark).get_offset(),offset+6)
        self.assertNotIn('Priya',w.page.serialize()[0][-6:])
        w._open_note(w.store.get_note('2'))
        pump()
        self.assertFalse(w.page._collaborators)

    def test_peer_label_stays_in_text_area_without_moving_mark(self):
        w=self.window
        w._phone_open(w.store.get_note('1'))
        before=w.page.serialize()
        for width in (360,402,720,1180):
            w.set_default_size(width,874)
            pump(.5)
            self.assertEqual(w.get_width(),width)
            # Exercise a real text position nearest the right edge, independent
            # of the fixture's animated collaborator position.
            offset=max(range(w.buffer.get_char_count()),key=lambda i:
                       w.editor.get_iter_location(w.buffer.get_iter_at_offset(i)).x)
            w.page.set_collaborator('PR',name='Priya',hue=320,offset=offset)
            snapshot=Gtk.Snapshot()
            w.page.draw_collaborators(snapshot)
            bounds=snapshot.to_node().get_bounds()
            visible=w.editor.get_visible_rect()
            self.assertGreaterEqual(bounds.get_x(),visible.x)
            self.assertLessEqual(bounds.get_x()+bounds.get_width(),visible.x+visible.width)
            current_mark=w.page._collaborators['PR'][0]
            self.assertEqual(w.buffer.get_iter_at_mark(current_mark).get_offset(),offset)
            self.assertEqual(w.page.serialize(),before)
            directory=os.environ.get('NOTES_CARET_CAPTURE_DIR')
            if directory:
                node=None
                for attempt in range(10):
                    pump(.1)
                    snap=Gtk.Snapshot()
                    Gtk.WidgetPaintable.new(w).snapshot(snap,w.get_width(),w.get_height())
                    node=snap.to_node()
                    if node is not None:break
                    w.queue_draw()
                self.assertIsNotNone(node)
                self.assertTrue(w.get_renderer().render_texture(node,None).save_to_png(
                    f'{directory}/notes-caret-{width}.png'))

    def test_quote_uses_bundled_serif_without_serializing_layout(self):
        from gi.repository import Pango
        w=self.window
        w._phone_open(w.store.get_note('3'))
        pump()
        before=w.page.serialize()
        tags=[tag for key,tag in w.document_layout.tags.items() if key[2]=='quote']
        self.assertTrue(tags)
        quote_line = next(line for line in range(w.buffer.get_line_count()) if 'quote' in w.page.block_of_line(line))
        active = w.buffer.get_iter_at_line(quote_line)[1].get_tags()
        self.assertIn(tags[-1], active)
        # Explicit TextTag line-height used to erase the 16px paragraph gap.
        self.assertGreaterEqual(w.editor.get_line_yrange(w.buffer.get_iter_at_line(quote_line)[1])[1], 41)
        snapshot=Gtk.Snapshot()
        Gtk.WidgetPaintable.new(w.editor).snapshot(snapshot,w.editor.get_width(),w.editor.get_height())
        rendered_fonts=[]
        def inspect(node):
            if node is None:return
            if hasattr(node,'get_font'):
                rendered_fonts.append(node.get_font().describe())
            if hasattr(node,'get_n_children'):
                for i in range(node.get_n_children()):inspect(node.get_child(i))
            elif hasattr(node,'get_child'):inspect(node.get_child())
        inspect(snapshot.to_node())
        self.assertTrue(any(f.get_family() == 'Newsreader' and 'opsz=17' in (f.get_variations() or '')
                            for f in rendered_fonts))
        font=tags[-1].get_property('font-desc')
        resolved=w.editor.get_pango_context().load_font(font).describe()
        self.assertEqual(resolved.get_family(),'Newsreader')
        self.assertEqual(resolved.get_style(),Pango.Style.ITALIC)
        self.assertEqual(w.page.serialize(),before)

if __name__=='__main__':unittest.main()
