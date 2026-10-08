# SPDX-License-Identifier: Apache-2.0
"""Viewer's v70 samples. No user store, thumbnail cache, or persistent writes."""
from dataclasses import dataclass
from pathlib import Path
import io
import json

from .formats import FileFacts, TIER_ONE
from .recents import RecentFile, Recents


@dataclass
class FixtureFile(RecentFile):
    uid: str = ''
    group: str = ''
    gone: bool = False
    shown_size: str = ''

    @property
    def exists(self):
        return not self.gone

    @property
    def size_text(self):
        return self.shown_size


class FixtureFacts(FileFacts):
    @property
    def name(self):
        return self.extra['name']

    @property
    def where(self):
        return self.extra['where']


class FixtureRecents(Recents):
    """Recents' interface with state held only in memory, including edits."""
    def __init__(self, filename):
        self.root = Path(filename).resolve().parent
        raw = json.loads(Path(filename).read_text())
        self.people=[]
        for person in raw.get('people',[]):
            if not person.get('asset'):
                self.people.append(dict(person,path=None));continue
            target=(self.root/person['asset']).resolve()
            if not target.is_relative_to(self.root):raise ValueError('Fixture portraits must stay beside the fixture.')
            if not target.is_file():raise ValueError('Missing fixture portrait: '+person['asset'])
            self.people.append(dict(person,path=target))
        self.selected = raw.get('selected', 'offsite')
        self.samples = {}
        self.entries = []
        for f in raw['files']:
            uid = f['uid']
            asset = f.get('asset', 'viewer-v70/' + uid)
            target = (self.root / asset).resolve()
            if not target.is_relative_to(self.root):
                raise ValueError('Fixture assets must stay beside the fixture.')
            if 'asset' in f and not target.is_file():
                raise ValueError('Missing fixture asset: ' + asset)
            if uid in self.samples:
                raise ValueError('Duplicate fixture ID: ' + uid)
            self.samples[uid] = dict(f, path=target)
            self.entries.append(FixtureFile(str(target), f['name'], f.get('kind', ''),
                uid=uid, group=f['group'], gone=f.get('gone', False), shown_size=f.get('size_text', '')))
        if self.selected not in self.samples:
            raise ValueError('Fixture selection is missing.')

    def sample(self, path):
        target = Path(path).resolve()
        return next((f for f in self.samples.values() if f['path'] == target), None)

    def facts_for(self, path):
        f = self.sample(path)
        if f is None:
            raise ValueError('Fixture mode cannot open real files.')
        facts = FixtureFacts(f['path'], TIER_ONE, 'PDF document' if f.get('pages') else 'Image')
        facts.extra = dict(f)
        if f.get('width'):
            facts.dimensions = f"{f['width']} × {f['height']}"
        return facts

    def record(self, path, **_kwargs):
        entry = self.find(path)
        if entry is None:
            raise ValueError('Fixture mode cannot record real files.')
        return entry

    def grouped(self, text=''):
        groups = []
        for entry in self.search(text):
            if groups and groups[-1][0] == entry.group:
                groups[-1][1].append(entry)
            else:
                groups.append((entry.group, [entry]))
        return groups

    def save(self):
        pass

    def cached_thumbnail(self, path):
        f = self.sample(path)
        return f['path'] if f and f.get('asset') else None

    def store_thumbnail(self, _path, _pixbuf):
        return None

    def _drop_thumbnail(self, _path):
        pass

    def clear(self):
        self.entries = []

    def pdf_bytes(self, path):
        sample=self.sample(path)
        if not sample or not sample.get("pages"):
            raise ValueError("Not a fixture PDF.")
        fields=[]
        data=lease_pdf(fields=fields)
        self.pdf_field_positions=fields
        return data

    def remember_position(self, path, *, page):
        entry=self.find(path)
        if entry:entry.page=page

    def pixbuf(self, path):
        f = self.sample(path)
        if not f or not f.get('generated'):
            return None
        import cairo
        import gi
        gi.require_version('GdkPixbuf', '2.0')
        from gi.repository import GdkPixbuf
        w, h = f['width'], f['height']
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        cr = cairo.Context(surface)
        if f['generated'] == 'frame':
            import math
            cr.set_source_rgb(125/255, 51/255, 83/255)
            for cx,cy,start in ((w-44,44,-math.pi/2), (w-44,h-44,0),
                                (44,h-44,math.pi/2), (44,44,math.pi)):
                cr.arc(cx,cy,44,start,start+math.pi/2)
            cr.close_path();cr.fill()
            cr.set_source_rgb(1,1,1); cr.rectangle(0,150,1920,780); cr.fill()
            cr.set_source_rgb(224/255,58/255,134/255); cr.rectangle(560,290,800,500);cr.fill()
            cr.set_source_rgb(1,1,1);cr.rectangle(790,440,340,170);cr.fill()
        else:
            draw_receipt(cr)
        stream = io.BytesIO(); surface.write_to_png(stream)
        loader=GdkPixbuf.PixbufLoader.new_with_type('png');loader.write(stream.getvalue());loader.close()
        return loader.get_pixbuf()


RECEIPT_LINES = [
    ('PARCEL & POST',170,38,'center',True),
    ('118 Alameda Ave, Studio 4',222,22,'center',False),
    ('Oakland CA 94607',252,22,'center',False),
    ('(510) 555-0183',282,22,'center',False),
    ('ORDER #2048',372,26,'left',False),('Sep 18, 2026  10:14 AM',410,26,'left',False),
    ('Linen notebook A5 x2',496,26,'left',False),('24.00',496,26,'right',False),
    ('Brass pen, medium nib',540,26,'left',False),('38.00',540,26,'right',False),
    ('Shipping',584,26,'left',False),('0.00',584,26,'right',False),
    ('TOTAL',682,32,'left',True),('$62.00',682,32,'right',True),
    ('Paid with card',724,22,'left',False),('Returns within 30 days.',980,22,'center',False),
    ('Thank you, Nick!',1030,26,'center',False),
]


def draw_receipt(cr):
    import cairo
    cr.set_source_rgb(46/255,41/255,36/255);cr.paint()
    gradient=cairo.RadialGradient(360,372,50,450,620,900)
    gradient.add_color_stop_rgba(0,1,220/255,170/255,.10)
    gradient.add_color_stop_rgba(1,0,0,0,.25)
    cr.set_source(gradient);cr.paint()
    for inset in range(40,0,-1):
        cr.set_source_rgba(0,0,0,.45/40);cr.rectangle(150-inset/2,98-inset/2,600+inset,1080+inset);cr.fill()
    cr.set_source_rgb(244/255,240/255,230/255);cr.rectangle(150,80,600,1080);cr.fill()
    import random
    noise=random.Random(70)
    for _ in range(1800):
        cr.set_source_rgba(0,0,0,noise.random()*.025);cr.rectangle(150+noise.random()*600,80+noise.random()*1080,2,2);cr.fill()
    cr.set_source_rgb(38/255,35/255,31/255)
    for y in (318,442,620):cr.rectangle(190,y,520,2);cr.fill()
    for text,y,size,align,bold in RECEIPT_LINES:
        cr.set_source_rgb(*(value/255 for value in ((90,85,76) if y in (222,252,282,724,980) else (38,35,31))))
        cr.select_font_face('monospace',cairo.FONT_SLANT_NORMAL,cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(size);width=cr.text_extents(text).x_advance
        x=710-width if align=='right' else 450-width/2 if align=='center' else 190
        cr.move_to(x,y);cr.show_text(text)
    cr.set_source_rgb(38/255,35/255,31/255)
    for i in range(44):cr.rectangle(250+i*9.2,1070,(i*7)%3+1.5,50);cr.fill()


def lease_pdf(*, fields=None):
    """Create the sample's three real PDF pages and optional native field bounds.

    Empty inline boxes reserve the fields' space in the typeset paragraph;
    the Viewer overlays editable text there without baking it into the original.
    """
    import cairo
    import gi
    gi.require_version('PangoCairo','1.0');gi.require_version('Pango','1.0')
    from gi.repository import Pango, PangoCairo
    stream=io.BytesIO();surface=cairo.PDFSurface(stream,612,792);cr=cairo.Context(surface)
    positions=[]
    field_specs={'tenant':('Tenant name',220),'orig':('Original lease date',150),'date':('MM/DD/YYYY',110)}

    def text(value,x,y,*,size=13,family='Literata, Georgia, serif',weight=400,width=460,
             height=None,justify=False,colour=(34,32,28),page=0,italic=False):
        layout=PangoCairo.create_layout(cr)
        font=Pango.FontDescription();font.set_family(family);font.set_absolute_size(round(size*Pango.SCALE));font.set_weight(Pango.Weight(600 if weight==650 else weight))
        if italic:font.set_style(Pango.Style.ITALIC)
        if weight==650:font.set_variations("wght=650")
        layout.set_font_description(font);layout.set_width(round(width*Pango.SCALE));layout.set_justify(justify)
        # Markup keeps the named landlord and rent bold, as they are on the paper.
        field_order=[]
        for key in field_specs:
            marker='{'+key+'}'
            if marker in value:
                value=value.replace(marker,'\ufffc');field_order.append(key)
        layout.set_markup(value,-1)
        attrs=layout.get_attributes() or Pango.AttrList()
        spacing=Pango.attr_letter_spacing_new(round((.26 if family=='Figtree' and weight==650 else -.078)*Pango.SCALE))
        attrs.insert(spacing)
        if height is not None:attrs.insert(Pango.attr_line_height_new_absolute(round(height*Pango.SCALE)))
        plain=layout.get_text();start=0
        for key in field_order:
            index=plain.index('\ufffc',start);byte_index=len(plain[:index].encode());start=index+1
            placeholder,fw=field_specs[key]
            rect=Pango.Rectangle();rect.x=0;rect.y=-23*Pango.SCALE;rect.width=fw*Pango.SCALE;rect.height=24*Pango.SCALE
            attr=Pango.attr_shape_new(rect,rect);attr.start_index=byte_index;attr.end_index=byte_index+3;attrs.insert(attr)
            # Inline entries reserve their full width. Paragraph letter spacing
            # otherwise shrinks the shaped object's advance by 0.078125pt,
            # letting justification place the last field beyond the paper margin.
            field_spacing=Pango.attr_letter_spacing_new(0)
            field_spacing.start_index=byte_index;field_spacing.end_index=byte_index+3
            attrs.insert(field_spacing)
        layout.set_attributes(attrs)
        start=0
        for key in field_order:
            index=plain.index('\ufffc',start);byte_index=len(plain[:index].encode());start=index+1
            rect=layout.index_to_pos(byte_index);placeholder,fw=field_specs[key]
            positions.append((page,key,placeholder,x+rect.x/Pango.SCALE,y+rect.y/Pango.SCALE,fw))
        cr.set_source_rgb(*(v/255 for v in colour));cr.move_to(x,y);PangoCairo.show_layout(cr,layout)
        return layout.get_size()[1]/Pango.SCALE

    sections=[[
        ('1. Term','The term is extended for twelve (12) months, beginning January 1, 2027 and ending December 31, 2027.'),
        ('2. Rent','Monthly rent remains <b>$4,850.00</b>, due on the first of each month. The annual rent review moves from January to March.'),
        ('3. Use','The premises are to be used as a design and software studio, and for no other purpose without the Landlord’s written consent.')],
        [('4. Maintenance','Tenant keeps the interior in good repair. Landlord maintains the roof, structure, and building systems, and responds to urgent repairs within two business days.'),
         ('5. Insurance','Tenant carries general liability insurance of not less than $1,000,000 per occurrence and names Landlord as an additional insured.'),
         ('6. Notices','Notices are given in writing to the addresses above, or by email to the addresses the parties have agreed.')],
        [('7. Everything else','All other terms of the original lease remain in force. This Renewal may be signed electronically and in counterparts.')]]
    for page,items in enumerate(sections):
        cr.set_source_rgb(253/255,252/255,248/255);cr.paint();y=72
        if page==0:
            y+=text('Commercial Lease Renewal',76,y,size=24,weight=600,height=38.4)+4
            y+=text('Studio 4, 118 Alameda Avenue, Oakland, California',76,y,size=12.5,height=20,colour=(110,104,93))+28
            y+=text('This renewal (the “Renewal”) is made between <b>Marsh &amp; Co. Property</b> (“Landlord”) and {tenant} (“Tenant”), and extends the lease dated {orig} on the terms below.',
                    76,y,height=20.8,justify=True,page=page)
        for heading,paragraph in items:
            y+=22
            y+=text(heading,76,y,family='Figtree',weight=650,height=16)+6
            y+=text(paragraph,76,y,height=20.8,justify=True,page=page)
        if page==2:
            y+=70
            for x in (76,326):
                cr.set_source_rgb(34/255,32/255,28/255);cr.set_line_width(1);cr.move_to(x,y+52);cr.line_to(x+210,y+52);cr.stroke()
            text('Theo Marsh',332,y+16,size=26,family='Newsreader, Georgia, serif',italic=True,colour=(27,42,107),width=198)
            text('Tenant · Date {date}',76,y+58,size=11,family='Figtree',width=210,colour=(110,104,93),page=page)
            text('Landlord · September 21, 2026',326,y+58,size=11,family='Figtree',width=210,colour=(110,104,93))
        # Footer belongs to the document, rather than the application's chrome.
        cr.set_source_rgb(154/255,147/255,133/255);cr.select_font_face('Figtree');cr.set_font_size(11)
        footer=f'Page {page+1} of 3';fw=cr.text_extents(footer).x_advance;cr.move_to((612-fw)/2,756);cr.show_text(footer)
        cr.show_page()
    surface.finish()
    if fields is not None:fields.extend(positions)
    return stream.getvalue()
