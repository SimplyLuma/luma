# SPDX-License-Identifier: Apache-2.0
"""One renderer for interactive ink and exported annotation appearances."""
import math
import gi

gi.require_version("PangoCairo", "1.0")
gi.require_version("Pango", "1.0")
from gi.repository import Pango, PangoCairo


def text_layout(cr, mark):
    layout = PangoCairo.create_layout(cr)
    font = Pango.FontDescription("Figtree")
    font.set_absolute_size(max(12, mark.width * 6) * Pango.SCALE)
    font.set_weight(Pango.Weight.SEMIBOLD)
    font.set_variations("wght=650")
    layout.set_font_description(font)
    layout.set_text(mark.text, -1)
    return layout


def mark_bounds(cr, mark):
    if mark.tool != "text":
        return mark.bounds()
    layout = text_layout(cr, mark)
    ink, logical = layout.get_extents()
    x, y = mark.start
    y-=layout.get_baseline()/Pango.SCALE
    return (x + min(ink.x, logical.x)/Pango.SCALE - 2,
            y + min(ink.y, logical.y)/Pango.SCALE - 2,
            x + max(ink.x+ink.width, logical.x+logical.width)/Pango.SCALE + 2,
            y + max(ink.y+ink.height, logical.y+logical.height)/Pango.SCALE + 2)


def draw_mark(cr, mark, opacity=None):
    cr.save()
    if mark.tool=='highlight':
        import cairo
        cr.set_operator(cairo.OPERATOR_MULTIPLY)
    rgb = tuple(int(mark.ink[i:i+2], 16)/255 for i in (1, 3, 5))
    cr.set_source_rgba(*rgb, opacity if opacity is not None else (0.40 if mark.tool == "highlight" else 1))
    cr.set_line_width(mark.width)
    cr.set_line_cap(1)
    cr.set_line_join(1)
    x1, y1 = mark.start
    x2, y2 = mark.end
    if mark.tool in ("box", "redact", "blur"):
        if mark.tool in ("redact","blur"):cr.set_source_rgb(11/255,11/255,12/255)
        x,y,w,h=min(x1,x2),min(y1,y2),abs(x2-x1),abs(y2-y1)
        if mark.tool=='box':
            radius=min(mark.width*1.2,w/2,h/2)
            for cx,cy,start in ((x+w-radius,y+radius,-math.pi/2),(x+w-radius,y+h-radius,0),
                                (x+radius,y+h-radius,math.pi/2),(x+radius,y+radius,math.pi)):
                cr.arc(cx,cy,radius,start,start+math.pi/2)
            cr.close_path();cr.stroke()
        else:cr.rectangle(x,y,w,h);cr.fill()
    elif mark.tool in ("ink","highlight","sign"):
        points = mark.points or (mark.start, mark.end)
        cr.move_to(*points[0])
        for point in points[1:]:
            cr.line_to(*point)
        if len(points) == 1 or all(p == points[0] for p in points):
            cr.arc(*points[0], mark.width/2, 0, math.tau)
            cr.fill()
        else:
            cr.stroke()
    elif mark.tool == "arrow":
        angle=math.atan2(y2-y1,x2-x1);length=mark.width*4.5
        cr.move_to(x1,y1);cr.line_to(x2-math.cos(angle)*length*.6,y2-math.sin(angle)*length*.6);cr.stroke()
        cr.move_to(x2,y2)
        for side in (-.45,.45):cr.line_to(x2-length*math.cos(angle+side),y2-length*math.sin(angle+side))
        cr.close_path();cr.fill()
    elif mark.tool == 'fixture-sign':
        cr.translate(x1,y1);cr.scale(mark.width,mark.width);cr.set_line_width(2.2)
        cr.move_to(2,30)
        for points in ((10,8,18,4,20,14),(22,26,12,40,10,34),(8,26,30,10,34,22),(36,30,30,34,34,28),(38,22,42,18,44,26),(46,32,50,22,54,20),(58,18,56,30,62,28),(66,26,70,16,74,22),(76,26,78,30,84,24)):cr.curve_to(*points)
        cr.move_to(60,38);cr.curve_to(80,34,110,32,140,30);cr.stroke()
    elif mark.tool == "ellipse":
        cr.translate((x1+x2)/2,(y1+y2)/2);cr.scale(max(abs(x2-x1)/2,.01),max(abs(y2-y1)/2,.01))
        cr.arc(0,0,1,0,math.tau);cr.restore();cr.save()
        rgb=tuple(int(mark.ink[i:i+2],16)/255 for i in (1,3,5));cr.set_source_rgb(*rgb);cr.set_line_width(mark.width);cr.stroke()
    elif mark.tool == "step":
        cr.arc(x1,y1,mark.width*4,0,math.tau);cr.fill_preserve();cr.set_source_rgb(1,1,1);cr.set_line_width(mark.width*.6);cr.stroke()
        from dataclasses import replace
        numbered=replace(mark,tool="text",text=mark.text or "1",width=mark.width*.7)
        layout=text_layout(cr,numbered);font=layout.get_font_description();font.set_weight(Pango.Weight.BOLD);font.set_variations("wght=700");layout.set_font_description(font)
        w,h=layout.get_pixel_size();cr.move_to(x1-w/2,y1+mark.width*1.5-layout.get_baseline()/Pango.SCALE);PangoCairo.show_layout(cr,layout)
    elif mark.tool == "text":
        layout=text_layout(cr,mark)
        cr.move_to(mark.start[0],mark.start[1]-layout.get_baseline()/Pango.SCALE)
        PangoCairo.layout_path(cr,layout)
        cr.set_source_rgba(1,1,1,1) if mark.ink=='#111111' else cr.set_source_rgba(0,0,0,.35)
        cr.set_line_width(mark.width*.5);cr.stroke_preserve()
        cr.set_source_rgba(*rgb, opacity if opacity is not None else 1);cr.fill()
    cr.restore()


def draw_marks(cr, marks, painter):
    """Composite marks with blurred pixels from the source, rather than a cover."""
    import cairo
    for mark in marks:
        if mark.tool!='blur':draw_mark(cr,mark);continue
        x1,y1=mark.start;x2,y2=mark.end
        x,y=min(x1,x2),min(y1,y2);w,h=abs(x2-x1),abs(y2-y1)
        if w<1 or h<1:continue
        small=cairo.ImageSurface(cairo.FORMAT_RGB24,max(1,math.ceil(w/14)),max(1,math.ceil(h/14)))
        sample=cairo.Context(small);sample.scale(small.get_width()/w,small.get_height()/h);sample.translate(-x,-y);painter(sample)
        cr.save();cr.rectangle(x,y,w,h);cr.clip();cr.translate(x,y);cr.scale(w/small.get_width(),h/small.get_height())
        pattern=cairo.SurfacePattern(small);pattern.set_filter(cairo.FILTER_BILINEAR);pattern.set_extend(cairo.EXTEND_PAD)
        cr.set_source(pattern);cr.paint();cr.restore()
