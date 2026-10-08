# SPDX-License-Identifier: Apache-2.0
import io
from pathlib import Path
import tempfile
import unittest
import cairo
import gi
gi.require_version('Poppler','0.18')
gi.require_version('GdkPixbuf','2.0')
from gi.repository import GLib, Poppler, GdkPixbuf
from luma_viewer.annotations import Mark
from luma_viewer.export import save_pdf, save_image, compose_pdf

class ExportContentTests(unittest.TestCase):
    def test_pdf_redaction_removes_underlying_text_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'source.pdf';copy=Path(folder)/'copy.pdf'
            surface=cairo.PDFSurface(str(source),200,100);cr=cairo.Context(surface)
            cr.move_to(20,40);cr.show_text('SECRET');cr.show_page();surface.finish()
            original=source.read_bytes()
            doc=Poppler.Document.new_from_bytes(GLib.Bytes.new(original),None)
            self.assertIn('SECRET',doc.get_page(0).get_text())
            save_pdf(source,copy,original,[Mark('redact','#ff5a4f',(0,0),(200,80))])
            result=Poppler.Document.new_from_file(copy.as_uri(),None)
            self.assertNotIn('SECRET',result.get_page(0).get_text() or '')
            self.assertEqual(source.read_bytes(),original)
    def test_clipboard_pdf_keeps_all_pages_and_removes_redacted_text(self):
        stream=io.BytesIO();surface=cairo.PDFSurface(stream,200,100);cr=cairo.Context(surface)
        for text in ('SECRET','SECOND PAGE'):
            cr.move_to(20,40);cr.show_text(text);cr.show_page()
        surface.finish();original=stream.getvalue()
        result=compose_pdf(original,[Mark('redact','#111111',(0,0),(200,80))])
        copy=Poppler.Document.new_from_bytes(GLib.Bytes.new(result),None)
        self.assertEqual(copy.get_n_pages(),2)
        self.assertNotIn('SECRET',copy.get_page(0).get_text() or '')
        source=Poppler.Document.new_from_bytes(GLib.Bytes.new(original),None)
        self.assertIn('SECRET',source.get_page(0).get_text())
        self.assertEqual(copy.get_page(1).get_size(),(200,100))

    def test_image_crop_and_rotation_applied_to_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'source.png';copy=Path(folder)/'copy.png'
            pixbuf=GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB,False,8,40,20);pixbuf.fill(0xff0000ff)
            pixbuf.savev(str(source),'png',[],[]);original=source.read_bytes()
            save_image(source,copy,pixbuf,[],crop=(10,0,20,10),rotation=90)
            output=GdkPixbuf.Pixbuf.new_from_file(str(copy))
            self.assertEqual((output.get_width(),output.get_height()),(10,20))
            self.assertEqual(source.read_bytes(),original)

    def test_form_export_changes_only_edited_field(self):
        objects=[b'<< /Type /Catalog /Pages 2 0 R /AcroForm 9 0 R >>',
            b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
            b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R /Annots [6 0 R 7 0 R] >>',
            b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
            b'<< /Length 0 >>\nstream\n\nendstream',
            b'<< /Type /Annot /Subtype /Widget /FT /Tx /T (tenant) /V (Original tenant) /Rect [10 60 190 80] /P 3 0 R /DA (/F1 10 Tf 0 g) >>',
            b'<< /Type /Annot /Subtype /Widget /FT /Tx /T (date) /V (Untouched date) /Rect [10 20 190 40] /P 3 0 R /DA (/F1 10 Tf 0 g) >>',
            b'null',b'<< /Fields [6 0 R 7 0 R] /NeedAppearances true /DR << /Font << /F1 4 0 R >> >> /DA (/F1 10 Tf 0 g) >>']
        data=b'%PDF-1.4\n';offsets=[0]
        for number,obj in enumerate(objects,1):
            offsets.append(len(data));data+=str(number).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
        start=len(data);data+=b'xref\n0 10\n0000000000 65535 f \n'
        for offset in offsets[1:]:data+=f'{offset:010d} 00000 n \n'.encode()
        data+=b'trailer\n<< /Size 10 /Root 1 0 R >>\nstartxref\n'+str(start).encode()+b'\n%%EOF\n'
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'form.pdf';copy=Path(folder)/'copy.pdf';source.write_bytes(data)
            document=Poppler.Document.new_from_bytes(GLib.Bytes.new(data),None)
            fields={m.field.get_name():m.field for m in document.get_page(0).get_form_field_mapping()}
            save_pdf(source,copy,data,[],fields={str(fields['tenant'].get_id()):'Edited tenant'})
            result=Poppler.Document.new_from_file(copy.as_uri(),None)
            values={m.field.get_name():m.field.text_get_text() for m in result.get_page(0).get_form_field_mapping()}
            self.assertEqual(values,{'tenant':'Edited tenant','date':'Untouched date'})
            self.assertEqual(source.read_bytes(),data)
