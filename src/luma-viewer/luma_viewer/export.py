# SPDX-License-Identifier: Apache-2.0
"""Transactional annotated copies. The opened source is never a destination."""
from pathlib import Path
import math
import os
import tempfile


class CopyDurabilityError(OSError):
    """The new copy is complete, but its directory commit could not be synced."""


def atomic_copy(source: Path, destination: Path, writer):
    """Publish a complete private file without replacing an existing pathname.

    File data and the parent directory are synced. If the directory sync fails
    after publication, retain the complete copy and raise a distinct error;
    callers keep the unsaved session rather than claiming durable success.
    Filesystems without hard links fail safely before publication.
    """
    source, destination = Path(source).absolute(), Path(destination).absolute()
    if source.resolve() == destination.resolve():
        raise ValueError("Choose a new filename. Viewer preserves the original.")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("That filename already exists. Choose a new copy name.")
    directory = os.open(destination.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    temporary = None
    published = False
    try:
        fd, temporary = tempfile.mkstemp(prefix=".luma-viewer-", suffix=destination.suffix,
                                         dir=destination.parent)
        os.close(fd)
        writer(Path(temporary))
        with open(temporary, "rb") as handle:
            if os.fstat(handle.fileno()).st_size == 0:
                raise OSError("The exported copy is empty.")
            os.fsync(handle.fileno())
        os.link(temporary, destination)
        published = True
        os.unlink(temporary)
        temporary = None
        os.fsync(directory)
    except OSError as error:
        if published:
            raise CopyDurabilityError(
                f"A complete copy exists at {destination}, but its directory could not "
                "be fully synced. Keep your annotations until you verify that copy."
            ) from error
        raise
    finally:
        try:
            if temporary is not None:
                os.unlink(temporary)
        finally:
            os.close(directory)


def _page_rotation(document, page, Poppler):
    """Read the rotation through Poppler's public annotation mapping API.

    GLib exposes no page rotation getter. A temporary asymmetric rectangle
    maps from displayed crop coordinates to unrotated crop coordinates; all
    four right-angle transforms are distinguishable. It is removed immediately.
    No PDF parsing or private bindings are involved.
    """
    w,h = page.get_size()
    crop = page.get_crop_box()
    cw,ch = crop.x2-crop.x1, crop.y2-crop.y1
    x1,y1,x2,y2 = w*.13,h*.19,w*.20,h*.30
    rect = Poppler.Rectangle()
    rect.x1,rect.y1,rect.x2,rect.y2 = x1,y1,x2,y2
    probe = Poppler.AnnotStamp.new(document,rect)
    page.add_annot(probe)
    try:
        r = probe.get_rectangle()
        actual = (r.x1,r.y1,r.x2,r.y2)
    finally:
        page.remove_annot(probe)
    candidates = ((0,(x1,y1,x2,y2)), (90,(cw-y2,x1,cw-y1,x2)),
                  (180,(cw-x2,ch-y2,cw-x1,ch-y1)), (270,(y1,ch-x2,y2,ch-x1)))
    for rotation, values in candidates:
        if all(abs(a-b) < .01 for a,b in zip(actual,values)):
            return rotation
    raise ValueError("This PDF's page transform is not supported for safe annotation export.")


def save_pdf(source, destination, source_bytes, marks, *, fields=None):
    import cairo
    import gi
    gi.require_version("Poppler", "0.18")
    from gi.repository import GLib, Poppler
    from .drawing import draw_mark, draw_marks, mark_bounds

    def write(path):
        # A fresh document handle means a failed/repeated save cannot leave
        # duplicated annotations in the on-screen document.
        document = Poppler.Document.new_from_bytes(GLib.Bytes.new(source_bytes), None)
        if fields and not document.get_permissions() & Poppler.Permissions.OK_TO_FILL_FORM:
            raise PermissionError('This PDF does not allow filling forms.')
        for number in range(document.get_n_pages()):
            for mapping in document.get_page(number).get_form_field_mapping():
                field=mapping.field;key=str(field.get_id())
                if key in (fields or {}):
                    if field.is_read_only() or field.get_field_type()!=Poppler.FormFieldType.TEXT:raise PermissionError('That PDF field cannot be edited.')
                    field.text_set_text(fields[key])
        if marks and not document.get_permissions() & Poppler.Permissions.OK_TO_ADD_NOTES:
            raise PermissionError("This PDF does not allow annotations.")
        if any(mark.tool in ('redact','blur') for mark in marks):
            # A cover annotation retains the confidential underlying PDF content.
            # Flatten every page to pixels when any mark removes information.
            first=document.get_page(0);w,h=first.get_size()
            output=cairo.PDFSurface(str(path),w,h);out=cairo.Context(output)
            for number in range(document.get_n_pages()):
                page=document.get_page(number);w,h=page.get_size();output.set_size(w,h)
                scale=min(2.,8192/max(w,h));surface=cairo.ImageSurface(cairo.FORMAT_RGB24,max(1,math.ceil(w*scale)),max(1,math.ceil(h*scale)))
                pc=cairo.Context(surface);pc.scale(scale,scale);pc.set_source_rgb(1,1,1);pc.paint();page.render(pc)
                draw_marks(pc,[m for m in marks if m.page==number],page.render)
                out.save();out.scale(1/scale,1/scale);out.set_source_surface(surface);out.paint();out.restore();out.show_page()
            output.finish()
            check=Poppler.Document.new_from_file(path.as_uri(),None)
            if check.get_n_pages()!=document.get_n_pages():raise OSError('The flattened copy failed page verification.')
            return
        rotations = {}
        for mark in marks:
            page = document.get_page(mark.page)
            if page is None:
                raise ValueError("An annotation refers to an unavailable page.")
            if mark.page not in rotations:
                rotations[mark.page] = _page_rotation(document, page, Poppler)
            rotation = rotations[mark.page]
            width, height = page.get_size()
            probe = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1))
            left, top, right, bottom = mark_bounds(probe, mark)
            left, top = max(0, left), max(0, top)
            right, bottom = min(width, right), min(height, bottom)
            if right <= left or bottom <= top:
                raise ValueError("An annotation lies outside its page.")
            # Only the new annotation appearance is rasterized, at 216 dpi.
            # Pages, original annotations, searchable text and vectors survive.
            scale = min(3.0, 8192 / max(right-left, bottom-top))
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32,
                                         max(1, math.ceil((right-left)*scale)),
                                         max(1, math.ceil((bottom-top)*scale)))
            cr = cairo.Context(surface)
            cr.scale(surface.get_width()/(right-left), surface.get_height()/(bottom-top))
            cr.translate(-left, -top)
            draw_mark(cr, mark, opacity=1.0 if mark.tool == "highlight" else None)
            if rotation:
                from .annotations import Viewport
                sw,sh = surface.get_width(),surface.get_height()
                rw,rh = (sh,sw) if rotation % 180 else (sw,sh)
                oriented = cairo.ImageSurface(cairo.FORMAT_ARGB32,rw,rh)
                oc = cairo.Context(oriented)
                Viewport(sw,sh,rw,rh,(360-rotation)%360).transform(oc)
                oc.set_source_surface(surface,0,0)
                oc.paint()
                surface = oriented
            rect = Poppler.Rectangle()
            rect.x1, rect.x2 = left, right
            rect.y1, rect.y2 = height-bottom, height-top
            annot = Poppler.AnnotStamp.new(document, rect)
            annot.set_flags(Poppler.AnnotFlag.PRINT)
            if mark.tool == "highlight":
                annot.set_opacity(0.30)
            page.add_annot(annot)
            # Geometry/flag changes invalidate Poppler's appearance. Set the
            # custom image last, after the page applies crop/rotation mapping.
            if not annot.set_custom_image(surface):
                raise OSError("The PDF annotation appearance could not be created.")
        if not document.save(path.as_uri()):
            raise OSError("The PDF copy could not be written.")
        check = Poppler.Document.new_from_file(path.as_uri(), None)
        if check.get_n_pages() != document.get_n_pages():
            raise OSError("The saved PDF failed page verification.")
    atomic_copy(source, destination, write)


def compose_pdf(source_bytes, marks=(), *, fields=None):
    """A rendered PDF copy for the clipboard, entirely in memory.

    Every page becomes pixels so redacted content cannot remain recoverable.
    The source and on-screen document are never mutated.
    """
    import io
    import cairo
    import gi
    gi.require_version('Poppler','0.18')
    from gi.repository import GLib,Poppler
    from .drawing import draw_marks
    document=Poppler.Document.new_from_bytes(GLib.Bytes.new(source_bytes),None)
    if not document.get_permissions() & Poppler.Permissions.OK_TO_COPY:
        raise PermissionError('This PDF does not allow copying.')
    for number in range(document.get_n_pages()):
        for mapping in document.get_page(number).get_form_field_mapping():
            field=mapping.field;key=str(field.get_id())
            if key in (fields or {}):
                if not document.get_permissions() & Poppler.Permissions.OK_TO_FILL_FORM or field.is_read_only() or field.get_field_type()!=Poppler.FormFieldType.TEXT:
                    raise PermissionError('That PDF field cannot be edited.')
                field.text_set_text(fields[key])
    stream=io.BytesIO();first=document.get_page(0);w,h=first.get_size()
    output=cairo.PDFSurface(stream,w,h);cr=cairo.Context(output)
    for number in range(document.get_n_pages()):
        page=document.get_page(number);w,h=page.get_size();output.set_size(w,h)
        scale=min(2.,8192/max(w,h))
        sheet=cairo.ImageSurface(cairo.FORMAT_RGB24,max(1,math.ceil(w*scale)),max(1,math.ceil(h*scale)))
        painter=cairo.Context(sheet);painter.scale(scale,scale);painter.set_source_rgb(1,1,1);painter.paint()
        page.render(painter);draw_marks(painter,[mark for mark in marks if mark.page==number],page.render)
        cr.save();cr.scale(1/scale,1/scale);cr.set_source_surface(sheet);cr.paint();cr.restore();cr.show_page()
    output.finish();return stream.getvalue()


def compose_image(pixbuf, marks, *, crop=None, rotation=0, flip=False, straighten=0):
    """The exact edited pixels used for clipboard and PNG export, held in memory."""
    import cairo
    import gi
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk
    from .drawing import draw_marks
    from .processing import crop_bounds
    from .annotations import Viewport
    surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,pixbuf.get_width(),pixbuf.get_height())
    cr=cairo.Context(surface)
    def painter(context):
        Gdk.cairo_set_source_pixbuf(context,pixbuf,0,0);context.paint()
    painter(cr);draw_marks(cr,marks,painter)
    x,y,w,h=crop_bounds(crop,pixbuf.get_width(),pixbuf.get_height()) if crop else (0,0,pixbuf.get_width(),pixbuf.get_height())
    rw,rh=(h,w) if rotation%180 else (w,h)
    output=cairo.ImageSurface(cairo.FORMAT_ARGB32,rw,rh);oc=cairo.Context(output)
    Viewport(w,h,rw,rh,rotation).transform(oc)
    if flip:oc.translate(w,0);oc.scale(-1,1)
    if straighten:oc.translate(pixbuf.get_width()/2-x,pixbuf.get_height()/2-y);oc.rotate(math.radians(straighten));oc.translate(-pixbuf.get_width()/2+x,-pixbuf.get_height()/2+y)
    oc.set_source_surface(surface,-x,-y);oc.paint()
    return output


def save_image(source, destination, pixbuf, marks, *, crop=None, rotation=0, flip=False, straighten=0):
    def write(path):compose_image(pixbuf,marks,crop=crop,rotation=rotation,flip=flip,straighten=straighten).write_to_png(str(path))
    atomic_copy(source,destination,write)
