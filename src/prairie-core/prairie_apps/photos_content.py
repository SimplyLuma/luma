# SPDX-License-Identifier: Apache-2.0
"""Photos image I/O. The shared MediaGrid/ImageViewport own all drawing."""
import gi

gi.require_version('GdkPixbuf','2.0')
from gi.repository import GdkPixbuf


def decode_photo(path, maximum_side=2048):
    """Read a bounded, oriented preview in a worker, never changing the file."""
    _,width,height = GdkPixbuf.Pixbuf.get_file_info(str(path))
    if width < 1 or height < 1 or width * height > 100_000_000:
        raise ValueError('Image dimensions are too large')
    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path),maximum_side,maximum_side,True)
    return pixbuf.apply_embedded_orientation() or pixbuf


def decode_export_photo(path):
    """Decode full resolution with the same bounds/orientation as the preview."""
    _, width, height = GdkPixbuf.Pixbuf.get_file_info(str(path))
    if width < 1 or height < 1 or width * height > 100_000_000:
        raise ValueError('Image dimensions are too large')
    return decode_photo(path, max(width, height))


def edited_photo_node(pixbuf, values):
    """Use the installed preview transforms without viewport chrome.

    This function and GSK rendering must run on the GTK owner thread. The
    decoder and encoded-file publication remain worker operations. Photos
    deliberately binds the existing shared preview's internal colour/crop
    helpers so export cannot grow a second, different adjustment engine.
    """
    gi.require_version('Gdk', '4.0')
    gi.require_version('Gtk', '4.0')
    gi.require_version('Graphene', '1.0')
    from gi.repository import Gdk, Graphene, Gtk
    from luma_appkit.media_viewport import normalise_adjustments, _colour_matrix, _cropped_size
    values = normalise_adjustments(values)
    width, height = pixbuf.get_width(), pixbuf.get_height()
    if 'crop_rect' in values:
        x, y, w, h = values['crop_rect']
        left, top, cw, ch = x * width, y * height, w * width, h * height
    else:
        cw, ch = _cropped_size(width, height, values['crop'])
        left, top = (width - cw) / 2, (height - ch) / 2
    # A saved fractional crop becomes an integer pixel canvas. It is scaled
    # only by this sub-pixel rounding, never by the preview's 2048px limit.
    iw, ih = max(1, round(cw)), max(1, round(ch))
    ow, oh = (ih, iw) if values['rot'] in (90, 270) else (iw, ih)
    snap = Gtk.Snapshot()
    snap.translate(Graphene.Point().init(ow / 2, oh / 2))
    snap.rotate(float(values['rot']))
    if values['flip']:
        snap.scale(-1, 1)
    snap.translate(Graphene.Point().init(-iw / 2, -ih / 2))
    snap.push_clip(Graphene.Rect().init(0, 0, iw, ih))
    colour = _colour_matrix(values)
    if colour is not None:
        snap.push_color_matrix(*colour)
    snap.append_texture(Gdk.Texture.new_for_pixbuf(pixbuf),
                        Graphene.Rect().init(-left * iw / cw, -top * ih / ch,
                                             width * iw / cw, height * ih / ch))
    if colour is not None:
        snap.pop()
    snap.pop()
    node = snap.to_node()
    if node is None:
        raise ValueError('The edited photo produced no image')
    return node, Graphene.Rect().init(0, 0, ow, oh)


def decode_square(path, side=54):
    """Supply a sharp centre crop without increasing a thumbnail's layout size."""
    source=decode_photo(path,min(2048,side*4))
    width,height=source.get_width(),source.get_height()
    crop=min(width,height)
    square=source.new_subpixbuf((width-crop)//2,(height-crop)//2,crop,crop)
    return square.scale_simple(side,side,GdkPixbuf.InterpType.BILINEAR)


def decode_face(path, x, y, zoom, side=128):
    """Fixture portrait crop from v70 faceImg's normalized centre and zoom.

    The kit draws the avatar; this only supplies its photograph, in memory.
    """
    source=decode_photo(path,2048)
    width,height=round(side*zoom),round(side*zoom*9/16)
    scaled=source.scale_simple(width,height,GdkPixbuf.InterpType.BILINEAR)
    output=GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB,True,8,side,side)
    output.fill(0)
    sx,sy=round(x*width-side/2),round(y*height-side/2)
    left,top=max(0,sx),max(0,sy)
    dx,dy=max(0,-sx),max(0,-sy)
    scaled.copy_area(left,top,min(side-dx,width-left),min(side-dy,height-top),output,dx,dy)
    return output


def prepare_print_pdf(path, output, width, height):
    """Make a temporary print document; the original is read, never modified."""
    import cairo
    gi.require_foreign('cairo')
    gi.require_version('Gdk','4.0')
    from gi.repository import Gdk
    photo=decode_photo(path,4096)
    surface=cairo.PDFSurface(str(output),width,height)
    try:
        context=cairo.Context(surface)
        scale=min(width/photo.get_width(),height/photo.get_height())
        context.translate((width-photo.get_width()*scale)/2,(height-photo.get_height()*scale)/2)
        context.scale(scale,scale)
        Gdk.cairo_set_source_pixbuf(context,photo,0,0)
        context.paint()
        context.show_page()
    finally:
        surface.finish()
    return output
