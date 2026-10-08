# SPDX-License-Identifier: Apache-2.0
"""MONITOR activity cells and temporary chart adapters pending DT1/SB1."""
from __future__ import annotations
import math
from pathlib import Path
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, Gdk, Gio, Pango, Gsk, Graphene
from luma_appkit import apply_type, icons, Selection, lumaui_tokens, ProgressLine, Card


def label(text='', role='body', *, name=None, end=False, css=None, wrap=False, muted=False, ink=None):
    if css in ('mn-mono','mn-advanced-value','mn-sheet-value'):role='mono'
    weights={'mn-cell-value':700,'mn-app-name':600,'mn-subtitle':400,'mn-value':600,'mn-core-caption':400,
             'mn-fact-key':400,'mn-fact-value':500,'mn-mono':500,'mn-advanced-value':550,
             'mn-sheet-title':700,'mn-sheet-subtitle':400,'mn-sheet-value':400}
    widget = apply_type(Gtk.Label(label=str(text), xalign=1 if end else 0, wrap=wrap,
                                  ellipsize=Pango.EllipsizeMode.NONE if wrap else Pango.EllipsizeMode.END), role, muted=muted, weight=weights.get(css), ink=ink)
    if wrap:widget.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    if name: widget.set_name(name)
    if css: widget.add_css_class(css)
    return widget


def mark_selected(widget, selected):
    Selection.mark(widget, selected)
    (widget.remove_css_class if selected else widget.add_css_class)('mn-idle')


def color(widget, name):
    found, rgba = widget.get_style_context().lookup_color(name)
    if not found:
        raise RuntimeError(f'Missing kit colour token: {name}')
    return rgba


def ink(cr, rgba, opacity=1):
    cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, rgba.alpha * opacity)


def app_icon(app,size):
    image=Gtk.Image(pixel_size=size,valign=Gtk.Align.CENTER)
    try:
        if app.get('ic'):
            image.set_from_icon_name('luma-v3-'+app['ic'])
        else:image.set_from_gicon(Gio.Icon.new_for_string(app['icon']))
    except Exception:image.set_from_icon_name('lumaui-app-window-symbolic')
    return image


def disclosure_icon(expanded, *, group=False):
    """Keep v70's glyph identity while rotating it through the layout API."""
    width=20 if group else 14
    box=Gtk.Fixed(width_request=width,height_request=14,halign=Gtk.Align.CENTER,valign=Gtk.Align.CENTER)
    glyph=icons.image('chevron-right' if group else 'chevron-down',pixel_size=14)
    box.put(glyph,(width-14)/2,0)
    if expanded:
        transform=Gsk.Transform.new().translate(Graphene.Point().init(width/2,7))
        transform=transform.rotate(90 if group else 180).translate(Graphene.Point().init(-7,-7))
        box.set_child_transform(glyph,transform)
    box.add_css_class('mn-disclosure-glyph')
    return box


class _ResourceCellsLayout(Gtk.LayoutManager):
    """Monitor's three statistic columns, including the 1:1:2 energy row."""
    def __init__(self, weights):
        super().__init__()
        self.weights=tuple(weights)
        self.gap=10

    def _children(self, widget):
        child=widget.get_first_child()
        while child:
            yield child
            child=child.get_next_sibling()

    def do_get_request_mode(self, widget):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def _widths(self, widget, width):
        # v70's fr tracks retain their content minimum on a narrow viewport.
        minima=[child.measure(Gtk.Orientation.HORIZONTAL,-1)[0]
                for child in self._children(widget)]
        available=max(sum(minima),width-self.gap*(len(minima)-1))
        widths=[None]*len(minima);remaining=set(range(len(minima)))
        while remaining:
            share=available/sum(self.weights[i] for i in remaining)
            fixed=[i for i in remaining if minima[i]>share*self.weights[i]]
            if not fixed:
                used=0;edge=0
                for i in sorted(remaining):
                    used+=self.weights[i]
                    next_edge=round(share*used)
                    widths[i]=next_edge-edge;edge=next_edge
                break
            for i in fixed:
                widths[i]=minima[i];available-=minima[i];remaining.remove(i)
        return widths

    def do_measure(self, widget, orientation, for_size):
        children=list(self._children(widget))
        if orientation==Gtk.Orientation.HORIZONTAL:
            natural=0
            for child,weight in zip(children,self.weights):
                _low,high,*_=child.measure(orientation,-1)
                natural=max(natural,math.ceil(high/weight))
            gaps=self.gap*max(0,len(children)-1)
            # The source grid keeps the page width even when its tracks overflow.
            return 0,natural*sum(self.weights)+gaps,-1,-1
        widths=self._widths(widget,for_size) if for_size>=0 else [-1]*len(children)
        sizes=[child.measure(orientation,width) for child,width in zip(children,widths)]
        return max((s[0] for s in sizes),default=0),max((s[1] for s in sizes),default=0),-1,-1

    def do_allocate(self, widget, width, height, baseline):
        x=0
        rtl=widget.get_direction()==Gtk.TextDirection.RTL
        for child,cell_width in zip(self._children(widget),self._widths(widget,width)):
            left=width-x-cell_width if rtl else x
            transform=Gsk.Transform.new().translate(Graphene.Point().init(left,0))
            child.allocate(cell_width,height,baseline,transform)
            x+=cell_width+self.gap


class ResourceCells(Gtk.Box):
    def __init__(self, *, energy=False):
        super().__init__(margin_top=12)
        self.set_layout_manager(_ResourceCellsLayout((1,1,2) if energy else (1,1,1)))


class PhoneCells(Gtk.Grid):
    """v71 phone: the resource's figures as inset wells, three across (two for Energy, whose Performance
    mode takes the whole row under them)."""
    def __init__(self, *, energy=False):
        super().__init__(column_homogeneous=True,column_spacing=8,row_spacing=8,margin_top=14)
        self.columns=2 if energy else 3
        self.count=0

    def cell(self, *, wide=False):
        well=Card(recessed=True,shape='stat')
        if wide:
            self.attach(well,0,(self.count+self.columns-1)//self.columns,self.columns,1)
            self.count=((self.count+self.columns-1)//self.columns+1)*self.columns
        else:
            self.attach(well,self.count%self.columns,self.count//self.columns,1,1)
            self.count+=1
        return well


class ResourceChart(Gtk.DrawingArea):
    """TODO(kit-request monitor-01-resource-charts.md): replace with DT1."""
    def __init__(self, series=(), maximum=100, *, spark=False, stacked=False, single=False, height=150):
        super().__init__(content_width=58 if spark else 0, content_height=22 if spark else height,
                         hexpand=not spark)
        self.series, self.maximum, self.spark, self.stacked = list(series), maximum, spark, stacked
        self.single = single
        self.add_css_class('mn-spark' if spark else 'mn-chart')
        self.set_draw_func(self._draw)
        self.update_property([Gtk.AccessibleProperty.LABEL], ['Resource use over the last 60 seconds'])

    def update(self, series, maximum=None):
        self.series = list(series)
        if maximum is not None: self.maximum = max(1, maximum)
        self.queue_draw()

    def _draw(self, area, cr, width, height):
        if width <= 0 or height <= 0: return
        first = color(self, 'luma_monitor_chart_primary')
        second = color(self, 'luma_monitor_chart_secondary')
        cr.save()
        if not self.spark:
            radius=min(lumaui_tokens.MONITOR['chart_radius'],width/2,height/2)
            for x,y,start in ((width-radius,radius,-math.pi/2),(width-radius,height-radius,0),(radius,height-radius,math.pi/2),(radius,radius,math.pi)):
                cr.arc(x,y,radius,start,start+math.pi/2)
            cr.close_path();cr.clip()
            ink(cr, color(self, 'luma_line')); cr.set_line_width(1)
            for fraction in (.25, .5, .75):
                cr.move_to(0, math.floor(150 * fraction + .5) / 150 * height); cr.line_to(width, math.floor(150 * fraction + .5) / 150 * height)
            cr.stroke()
        maximum = max(1, self.maximum)
        def line(values, tone, fill=False):
            n = max(1, len(values) - 1)
            if fill: cr.move_to(0, height)
            for i, v in enumerate(values):
                x = i / n * width
                y = height - max(0, v or 0) / maximum * (height - (2 if self.spark else height * 6 / 150)) - (1 if self.spark else 0)
                if i == 0 and not fill: cr.move_to(x, y)
                else: cr.line_to(x, y)
            if fill:
                cr.line_to(width, height); cr.close_path(); ink(cr, tone, .26 if tone is second else .34); cr.fill()
            else: ink(cr, tone); cr.set_line_width(1.4 if self.spark else 1.6); cr.stroke()
        a = [p[0] or 0 for p in self.series]; b = [p[1] or 0 for p in self.series]
        total = [x+y for x,y in zip(a,b)]
        if self.spark:
            line(total, color(self, 'luma_muted'));cr.restore();return
        upper = total if self.stacked else b
        for values,tone in ([(a,first)] if self.single else [(upper,second),(a,first)]):
            line(values,tone,True); line(values,tone)
        cr.restore()


class CoreMeter(Gtk.DrawingArea):
    def __init__(self, fraction=0):
        super().__init__(hexpand=True)  # fills its well; never asks the page for height
        self.add_css_class('mn-core-meter')
        self.fraction=max(0,min(1,fraction));self.set_draw_func(self._draw)
    def _draw(self,_area,cr,w,h):
        radius=min(lumaui_tokens.MONITOR['core_radius'],w/2,h/2)
        for x,y,start in ((w-radius,radius,-math.pi/2),(w-radius,h-radius,0),(radius,h-radius,math.pi/2),(radius,radius,math.pi)):
            cr.arc(x,y,radius,start,start+math.pi/2)
        cr.close_path();cr.clip()
        ink(cr,color(self,'luma_well'));cr.rectangle(0,0,w,h);cr.fill()
        ink(cr,color(self,'luma_monitor_chart_primary'),.55);cr.rectangle(0,h*(1-self.fraction),w,h*self.fraction);cr.fill()
        # The plot owns its fill; finish with the shared well's inset hairline.
        inset=.5;edge_radius=max(0,radius-inset)
        for x,y,start in ((w-radius,radius,-math.pi/2),(w-radius,h-radius,0),(radius,h-radius,math.pi/2),(radius,radius,math.pi)):
            cr.arc(x,y,edge_radius,start,start+math.pi/2)
        cr.close_path();ink(cr,color(self,'luma_well_ring'));cr.set_line_width(1);cr.stroke()


class ActivityRow(Gtk.Box):
    def __init__(self, app, value, fraction, on_pick, on_expand, *, all_processes=False, expanded=False, phone=False):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        # v71 phone: quieter rows (icon, name, number, a thin meter): no subtitle, no chevron, 56 tall, 15px names.
        self.button=Gtk.Button(height_request=56 if phone else 54)
        if phone:self.button.add_css_class('mn-phone-row')
        self.button.set_name('mn-app-'+app['id'])
        self.button.add_css_class('mn-app-row')
        # TableHeader.align owns the gaps between aligned columns.
        line=Gtk.Box(margin_top=8,margin_bottom=8)
        image=app_icon(app,32)
        identity=Gtk.Box(spacing=12,hexpand=True)
        identity.append(image)
        words=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,hexpand=True,valign=Gtk.Align.CENTER)
        title=Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,column_spacing=8,row_spacing=0,min_children_per_line=1,max_children_per_line=3,halign=Gtk.Align.START)
        title.add_css_class('mn-title-flow')
        name=label(app['n'],css='mn-app-name',wrap=True);name.set_halign(Gtk.Align.START);title.append(name)
        for tag in app.get('tags',[]):
            badge=label(tag,'label',css='mn-tag',wrap=True);badge.set_halign(Gtk.Align.START);title.append(badge)
        child=title.get_first_child()
        while child:
            child.set_focusable(False);child=child.get_next_sibling()
        words.append(title)
        if not phone:words.append(label(app['sub'],'caption',css='mn-subtitle'))
        identity.append(words);line.append(identity)
        self.counters=[]
        for text in ([len(app['procs']),app['th']] if all_processes else []):
            cell=label(text,'caption',end=True);cell.set_size_request(64,-1);cell.set_visible(all_processes);self.counters.append(cell);line.append(cell)
        metric=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=5,valign=Gtk.Align.CENTER,width_request=128)
        metric.append(label(value,end=True,css='mn-value'));metric.append(ProgressLine(fraction,tone='chart',label=app['n']+' resource use'));line.append(metric)
        # The disclosure is a separate keyboard-operable button, never a button inside a button.
        self.disclosure=Gtk.Button(child=disclosure_icon(expanded),tooltip_text='Show its processes',width_request=26,height_request=26,valign=Gtk.Align.CENTER)
        self.disclosure.set_name('mn-expand-'+app['id']);self.disclosure.add_css_class('mn-disclosure')
        self.disclosure.get_child().add_css_class('mn-disclosure-glyph')
        self.line=line
        spacer=Gtk.Box(width_request=26);spacer.set_visible(not phone);line.append(spacer)
        self.button.set_child(line);self.button.set_hexpand(True);self.button.connect('clicked',lambda *_:on_pick('a:'+app['id']))
        self.disclosure.connect('clicked',lambda *_:on_expand(app['id']))
        outer=Gtk.Overlay(child=self.button)
        if not phone:self.disclosure.set_halign(Gtk.Align.END);self.disclosure.set_margin_end(12);outer.add_overlay(self.disclosure)
        self.append(outer)
        self.update_property([Gtk.AccessibleProperty.LABEL],[app['n']])


from gi.repository import Gio
