# SPDX-License-Identifier: Apache-2.0
"""MONITOR: resource activity on the LumaUI frame.

Shared surfaces: AppWindow/Island, TableHeader/Selection, ActionCenter,
ModeSwitch, Menu, DestructiveDialog and ToastHost/Toast. Resource plots,
activity rows, core plots and process inspection are MONITOR-specific surfaces
(§2.2), using kit tokens only. Resource navigation uses shared sidebar parts;
the temporary resource plot adapter will move to shared DT1 when it lands.
"""
from __future__ import annotations
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from collections import deque
from .v70_data import (FixtureSource, sorted_apps, format_value, fixture_totals,
                       fixture_properties, filler_processes)

_fixture_home=None

def isolate_fixture():
    global _fixture_home
    if os.environ.get('LUMA_MONITOR_FIXTURE') and _fixture_home is None:
        _fixture_home=tempfile.TemporaryDirectory(prefix='luma-monitor-fixture-')
        for name,leaf in [('XDG_CONFIG_HOME','config'),('XDG_STATE_HOME','state'),('XDG_CACHE_HOME','cache'),('XDG_DATA_HOME','data')]:
            os.environ[name]=str(Path(_fixture_home.name)/leaf)

isolate_fixture()
import gi
gi.require_version('Gtk','4.0');gi.require_version('Adw','1')
from gi.repository import Adw,Gdk,Gio,GLib,GObject,Gtk,Pango
from luma_appkit import (AppWindow,Island,ToastHost,Toast,TableHeader,Column,Selection,
    ActionCenter,BarAction,BarChip,BarWidget,TextButton,TextField,Command,CommandGroup,CommandRegistry,
    DestructiveDialog,ModeSwitch,Card,CountBadge,ScrollView,install_appkit,install_lumaui,
    add_style_sheet,icons,SidebarToggle,SEPARATOR,NavigationSidebar,SidebarRow,RowLead,ProgressLine,BarSearch,RichMenuItem,
    PanelRow,panel_list)
from luma_appkit.action_bubble import FloatingMenu,MenuItem,float_at
from luma_appkit import lumaui,lumaui_tokens
from .visuals import label,ResourceChart,ResourceCells,ActivityRow,CoreMeter,mark_selected,app_icon,disclosure_icon,PhoneCells

APP_ID='io.luma.Monitor.LumaUIPreview' if os.environ.get('LUMA_MONITOR_FIXTURE') or os.environ.get('LUMA_MONITOR_PREVIEW') else 'io.luma.Monitor'
RESOURCE_NAMES={'cpu':'CPU','mem':'Memory','disk':'Disk','net':'Network','en':'Energy'}
RESOURCES=[('cpu','CPU','cpu'),('mem','Memory','layers'),('disk','Disk','hard-drive'),('net','Network','wifi'),('en','Energy','battery-medium')]
MAXIMUM={'cpu':30,'mem':2400,'disk':90,'net':5,'en':45}


def clear(box):
    while box.get_first_child():box.remove(box.get_first_child())


class MonitorWindow(AppWindow):
    wide_layout=GObject.Property(type=bool,default=True)
    phone_layout=GObject.Property(type=bool,default=False)
    def __init__(self,application,source=None):
        fixture=os.environ.get('LUMA_MONITOR_FIXTURE')
        self.source=source or (FixtureSource(fixture) if fixture else None)
        self.fixture=self.source is not None
        self.resource='cpu';self.sort=('v','descending');self.all_processes=False;self.selected=None
        initial_query=os.environ.get('LUMA_MONITOR_QUERY','') if self.fixture else ''
        self.query=''
        self.open_apps=set();self.groups_open={'sys'};self.more_groups=set();self.process_details=None
        self.properties={};self.properties_working=set()
        self.closed=False;self.generation=0;self.working=False;self.timer=0;self._menu_widget=None
        self.apps=self.source.apps if self.fixture else [];self.groups=self.source.groups if self.fixture else []
        self.totals=fixture_totals(self.source) if self.fixture else {}
        self.sample_failed=False
        self.histories={k:deque(maxlen=60) for k in RESOURCE_NAMES}
        if self.fixture:self._fixture_history()
        commands=CommandRegistry((CommandGroup(None,(
            Command('monitor.find','Find an app or process',lambda:self._active_search().focus(),'search',shortcut=('Ctrl','F')),
            Command('monitor.quit','Quit Monitor',self.close,'log-out',shortcut=('Ctrl','Q')),
        )),))
        super().__init__(application=application,app_id=APP_ID,title='Monitor',icon_name='luma-v3-monitor',commands=commands,
                         default_width=1180,default_height=740,minimum_width=360,minimum_height=420)
        self.body_row=Gtk.Box();self.set_body(self.body_row)
        self.sidebar=NavigationSidebar(variant='resources');self.sidebar.set_name('mn-sidebar')
        self.sidebar.set_hexpand(False);self.body_row.append(self.sidebar)
        self.sidebar.list.connect('row-activated',lambda _list,row:self._resource(row.resource) if hasattr(row,'resource') else None)
        self.island=Island();self.island.set_hexpand(True);self.island.set_vexpand(True);self.island.set_name('mn-island')
        self.host=ToastHost(self.island);self.host.set_hexpand(True);self.body_row.append(self.host)
        self.page=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,margin_top=30,margin_bottom=110,margin_start=30,margin_end=30)
        self.page_clamp=Adw.Clamp(child=self.page,maximum_size=860,tightening_threshold=860)
        self.scroll=ScrollView(self.page_clamp,fade_top=False,fade_bottom=False);self.scroll.set_name('mn-body');self.island.append(self.scroll)
        self.hero=Gtk.Box(orientation=Gtk.Orientation.VERTICAL);self.hero.set_name('mn-hero');self.page.append(self.hero)
        self.list=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,margin_top=28);self.list.set_name('mn-list');self.page.append(self.list)
        self.center=ActionCenter().attach(self.host);self.center.bar.set_name('mn-bar')
        self.search=self._make_search()
        self._narrow_widgets=[]
        narrow=Adw.Breakpoint.new(Adw.BreakpointCondition.parse('max-width: 820px'))
        narrow.add_setter(self,'wide-layout',False);narrow.add_setter(self.sidebar,'width-request',202);self.add_breakpoint(narrow)
        phone=Adw.Breakpoint.new(Adw.BreakpointCondition.parse('max-width: 680px'))
        phone.add_setter(self,'wide-layout',False);phone.add_setter(self,'phone-layout',True);phone.add_setter(self.sidebar,'width-request',202);phone.add_setter(self.sidebar,'visible',False);self.add_breakpoint(phone)
        self.connect('notify::wide-layout',lambda *_:self._layout_metrics())
        self.connect('notify::phone-layout',lambda *_:self._layout_metrics())
        self.connect('map',self._mapped);self.connect('unmap',self._unmapped);self.connect('close-request',self._closing)
        key=Gtk.EventControllerKey();key.connect('key-pressed',self._key);self.add_controller(key)
        self._render()
        # v71 tiers: under 560 the phone shape (a picker in the bar, wells, the detail shown); crossing redraws.
        self.tier_watch.connect('tier-changed',lambda *_:self._render())
        # A window between 560 and 680 has no sidebar either, so its bar carries the picker too.
        self.connect('notify::phone-layout',lambda *_:(self._render_bar(),self._render_body(),self._adapt()))
        if initial_query:self.search.entry.set_text(initial_query)
        if not self.fixture:self._sample()

    def _fixture_history(self):
        t=self.totals
        pairs={'cpu':(t['appsCpu'],t['sysCpu']),'mem':(t['appsMem'],t['sysMem']),
               'disk':(t['read'],t['write']),'net':(t['down'],t['up']),'en':(t['en'],0)}
        for k,pair in pairs.items():self.histories[k]=deque([pair]*60,maxlen=60)
        self.cores=[max(2,min(98,t['cpu']*(.6+(i*37%9)/10))) for i in range(8)]

    def _phone(self):
        # v71: under 560 Monitor is the phone shape; a window between 560 and 680 only loses its sidebar.
        return self.tier_watch.tier=='phone'

    def _all(self):
        # v71 phone: no \"all processes\" toggle, the detail (cores, every process group) shows by default.
        return self.all_processes or self._phone()

    def _make_search(self):
        if self._phone():return BarSearch('Search',label='Find an app or process',text=self.query,keep=True,span='narrow',on_change=self._search_changed)
        return BarSearch('Find an app or process',text=self.query,on_change=self._search_changed)

    def _active_search(self):return self.search

    def _chart_max(self,k):
        if k=='cpu':return 100
        if k=='mem':return self.source.totals['mem'] if self.fixture else self.totals.get('totalMem',1)
        return max([1,*[max(a or 0,b or 0) for a,b in self.histories[k]]])*1.25

    def _spark_max(self,key):
        baseline=100 if key=='cpu' else self._chart_max(key) if key=='mem' else 1
        return max([baseline,*[(a or 0)+(b or 0) for a,b in self.histories[key]]])

    def _render(self):
        self._render_sidebar();self._render_body();self._render_bar();self._adapt()

    def _render_sidebar(self):
        initial=not hasattr(self,'resource_rows')
        t=self.totals
        values={'cpu':f"{int(t.get('cpu',0)+.5)}%" if t.get('cpu') is not None else '—',
                'mem':f"{t.get('mem',0)/1024:.1f} of {(16384 if self.fixture else t.get('totalMem',0))/1024:g} GB" if t.get('mem') is not None else '—',
                'disk':format_value((t.get('read') or 0)+(t.get('write') or 0),'disk') if t.get('read') is not None else '—',
                'net':'↓ '+format_value(t.get('down'),'net'),'en':('74% · '+('4 h' if t['en']>60 else '6 h')+' left') if self.fixture else t.get('batteryText','—')}
        if initial:self.resource_rows={}
        for k,title,icon in RESOURCES:
            if not initial:
                row=self.resource_rows[k];row.set_subtitle(values[k]);row.spark.update(self.histories[k],self._spark_max(k));Selection.mark(row,k==self.resource);continue
            spark=ResourceChart(self.histories[k],self._spark_max(k),spark=True)
            row=SidebarRow(title,lead=RowLead.well(icon),subtitle=values[k],trail=spark);row.resource=k;row.spark=spark;row.set_name('mn-resource-'+k)
            Selection.mark(row,k==self.resource);self.sidebar.append_row(row);self.resource_rows[k]=row
        self.sidebar.list.select_row(self.resource_rows[self.resource])

    def _resource(self,key):
        self.resource=key;self.sort=('v','descending');self._render();self.scroll.get_vadjustment().set_value(0)

    def _hero_text(self):
        t=self.totals;apps=sorted_apps(self._visible_apps(),self.resource)
        top=apps[0]['n'] if apps else None;r=self.resource
        if not self.fixture:
            if self.sample_failed:
                return ('Activity couldn’t refresh.' if t else 'System activity is unavailable.',
                        'Showing the last reading. Retrying…' if t else 'Trying again…')
            if r=='cpu':
                measured=t.get('cpu') is not None
                busiest=top if apps and (apps[0].get('cpu') or 0)>0 else None
                return ('Plenty of headroom.' if measured and t['cpu']<25 else f'{busiest} is doing most of the work.' if measured and busiest else 'CPU activity.',f"{int(t['cpu']+.5)}% of {len(getattr(self,'cores',[]))} cores in use" if measured else 'Loading…')
            if r=='mem':return ('Memory activity.' if t.get('mem') is None else 'Memory is comfortable.' if t['mem']/max(1,t.get('totalMem',1))<.7 else 'Memory is getting full.',f"{format_value(t.get('mem'),'mem')} of {format_value(t.get('totalMem'),'mem')} in use")
            return ({'disk':'Disk activity.','net':'Network activity.','en':'Energy activity.'}[r],
                    (t.get('batteryText','')+' · ' if t.get('batteryText') else '')+'Per-app energy use is unavailable.' if r=='en' else 'Per-app network use is unavailable.' if r=='net' else '')
        if r=='cpu':return ('Plenty of headroom.' if t['cpu']<25 else f'{top} is doing most of the work.',f"{int(t['cpu']+.5)}% of 8 cores in use · 3.8 GHz · 58 °C")
        if r=='mem':return ('Memory is comfortable.' if t['mem']/16384<.7 else f'Memory is getting full. {top} uses the most.',f"{format_value(t['mem'],'mem')} of 16 GB in use · pressure is low")
        if r=='disk':return ('Your disk is quiet.' if 'filer' in self.source.removed else 'Filer is copying 3.1 GB to Backup.','Nothing big is reading or writing' if 'filer' in self.source.removed else 'About 40 seconds left at this speed')
        if r=='net':return ('The network is quiet.' if 'depot' in self.source.removed else 'Depot is downloading updates.','Wi‑Fi · Studio North · strong signal')
        awake=next((a['n'] for a in apps if a.get('awake')),None)
        return (f"At this pace, the battery lasts about {4 if t['en']>60 else 6} hours.",f'{top} uses the most energy'+(f' · {awake} is keeping the screen awake' if awake else ''))

    def _render_body(self):
        clear(self.hero);clear(self.list);self._narrow_widgets=[];self.core_grid=None;self.advanced_grid=None
        phone=self._phone();everything=self._all()
        # v71 retains the content gutter on phones; only the top spacing changes.
        for side in ('start','end'):getattr(self.page,'set_margin_'+side)(lumaui_tokens.MONITOR['content_gutter'])
        self.page.set_margin_top(18 if phone else 30);self.page.set_margin_bottom(0 if phone else 110)
        title,sub=self._hero_text();self.hero_title=label(title,'monitor-story-phone' if phone else 'monitor-story',name='mn-title',wrap=True);self.hero.append(self.hero_title)
        self.hero_subtitle=label(sub,name='mn-subtitle',css='mn-hero-subtitle',wrap=True);self.hero_subtitle.set_margin_top(4);self.hero_subtitle.set_margin_bottom(18);self.hero.append(self.hero_subtitle)
        plot=Gtk.Overlay();self.chart=ResourceChart(self.histories[self.resource],self._chart_max(self.resource),stacked=self.resource in ('cpu','mem'),single=self.resource=='en',height=120 if phone else 150)
        self.chart.set_name('mn-chart');plot.set_child(self.chart)
        caption=label('60 s','caption',css='mn-chart-caption');caption.set_halign(Gtk.Align.START);caption.set_valign(Gtk.Align.END);caption.set_margin_start(12);caption.set_margin_bottom(8);plot.add_overlay(caption);self.hero.append(Card(plot,recessed=True,padded=False))
        self._stats()
        cols=[Column('n','Apps',expand=True)]
        if everything and not phone:cols += [Column(None,'Procs',width=64,end=True),Column(None,'Threads',width=64,end=True)]
        cols += [Column('v', 'Energy impact' if self.resource=='en' else RESOURCE_NAMES[self.resource],width=128,end=True),Column(None,'',width=26)]
        sort=self.sort
        self.header=TableHeader(cols,sort=sort,on_sort=self._sort);self.header.set_name('mn-header')
        self.header.cells[0].set_name('mn-sort-name');self.list.append(self.header)
        self.header.cells[-2].set_name('mn-sort-value')
        # v71 phone: no sort header (it still aligns the rows' columns, so it stays, hidden).
        self.header.set_visible(not phone)
        if everything and not phone:self._narrow_widgets.extend(self.header.cells[1:3])
        for app in sorted_apps(self._visible_apps(),self.resource,key=sort[0],descending=sort[1]=='descending',query=self.query):
            shown=dict(app);shown['tags']=[]
            if self.fixture:
                if app['id'] in self.source.stopped:shown['tags'].append('Stopped')
                priority=self.source.priority.get(app['id'],'0')
                if priority!='0':shown['tags'].append(dict(self.source.priorities)[priority]+' priority')
            value=app.get(self.resource);row=ActivityRow(shown,format_value(value,self.resource),(value or 0)/MAXIMUM[self.resource],self._pick,self._expand,all_processes=everything and not phone,expanded=app['id'] in self.open_apps,phone=phone)
            self.header.align(row.line)
            mark_selected(row.button,self.selected=='a:'+app['id']);self.list.append(row)
            self._narrow_widgets.extend(row.counters)
            if app['id'] in self.open_apps and not phone:self._app_details(row,app)
        if not any(activity_matches(a,self.query) for a in self._visible_apps()):
            empty=label(f'No apps match “{self.query}”.',wrap=True,muted=True)
            empty.set_margin_start(12);empty.set_margin_end(12);self.list.append(empty)
        if everything:self._render_groups()
        else:
            count=sum(g['total'] for g in self.groups)
            reveal=Gtk.Button();reveal.set_name('mn-all');reveal.add_css_class('mn-reveal');line=Gtk.Box(spacing=12,margin_top=14,margin_bottom=14,margin_start=16,margin_end=16)
            line.append(icons.image('layers'));line.append(label(f'{count} processes keep Luma running. You don’t need to manage them, but you can.',wrap=True));line.append(label('Show them',css='mn-value'))
            reveal.set_child(line);reveal.set_margin_top(14);reveal.connect('clicked',lambda *_:self._toggle_all());self.list.append(reveal)

    def _resource_caption(self, title, index, resource):
        line=Gtk.Box(spacing=7)
        if index < (1 if resource=='en' else 2):
            dot=Gtk.Box(width_request=8,height_request=8,valign=Gtk.Align.CENTER)
            dot.add_css_class('mn-series-marker')
            dot.add_css_class('primary' if index==0 else 'secondary')
            line.append(dot)
        caption=label(title,'small',wrap=True);caption.set_wrap_mode(Pango.WrapMode.WORD)
        line.append(caption)
        return line

    def _stats(self):
        r=self.resource;t=self.totals;facts=[]
        if r=='disk':facts=[('Reading',format_value(t.get('read'),'disk')),('Writing',format_value(t.get('write'),'disk')),('Free space','212 GB of 512' if self.fixture else t.get('free','—'))]
        if r=='net':facts=[('Down',format_value(t.get('down'),'net')),('Up',format_value(t.get('up'),'net')),('This session','1.4 GB' if self.fixture else t.get('session','—'))]
        if r=='en':facts=[('Battery','74%' if self.fixture else t.get('percentage','—')),('Time left',('4 h 05 m' if t['en']>60 else '6 h 10 m') if self.fixture else t.get('remaining','—'))]
        phone=self._phone()
        if facts:
            # v71 phone: the figures are inset wells (20 round, 17/700 numbers); Performance mode spans the row.
            grid=PhoneCells(energy=r=='en') if phone else ResourceCells(energy=r=='en');grid.set_name('mn-resource-cells');self.hero.append(grid)
            for i,(key,value) in enumerate(facts):
                if phone:
                    cell=grid.cell();box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL);cell.append(box)
                    caption=self._resource_caption(key,i,r);box.append(caption)
                    number=label(value,'condition',css='mn-cell-value');box.append(number);continue
                card=Card(padded=False);card.set_margin_top(0);box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,margin_top=10,margin_bottom=12,margin_start=14,margin_end=14)
                caption=self._resource_caption(key,i,r);box.append(caption)
                number=label(value,'numeric');number.set_ellipsize(Pango.EllipsizeMode.NONE);number.set_margin_top(2);box.append(number);card.append(box);grid.append(card)
            if r=='en':
                if phone:card=grid.cell(wide=True);box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=6);card.append(box)
                else:
                    card=Card(padded=False);card.set_margin_top(0)
                    box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=6,margin_top=10,margin_bottom=12,margin_start=14,margin_end=14)
                caption=label('Performance mode','small',wrap=True);caption.set_wrap_mode(Pango.WrapMode.WORD);box.append(caption)
                if self.fixture or getattr(self,'profile',None):
                    mode=ModeSwitch([('saver','Battery saver'),('bal','Balanced'),('perf','Performance')],current=getattr(self,'profile','bal'),on_change=self._profile,label='Performance mode',fill=True,size='small')
                    for key,button in mode.buttons.items():button.set_name('mn-profile-'+key)
                    mode.set_sensitive(self.fixture);box.append(mode)
                else:box.append(label('—','caption'))
                if not phone:card.append(box);grid.append(card)
        if not self._all():return
        if r=='cpu':
            cores=Gtk.Grid(column_homogeneous=True,column_spacing=8,row_spacing=8,margin_top=12);self.hero.append(cores)
            for i,value in enumerate(getattr(self,'cores',[])):
                well=Gtk.Overlay(height_request=70);bar=CoreMeter((value or 0)/100);well.set_child(bar)
                caption=label(i,'caption',css='mn-core-caption');caption.set_halign(Gtk.Align.CENTER);caption.set_valign(Gtk.Align.START);caption.set_margin_top(5);well.add_overlay(caption);cores.attach(well,i%8,i//8,1,1)
            self.core_grid=cores
            return
        details=({'mem':[('Cached','3.0 GB'),('Swap','0.2 of 8 GB'),('zram','1.1 GB → 380 MB'),('Committed','14.8 GB')],
                 'disk':[('Device','nvme0n1'),('Root','btrfs · /'),('Queue','2'),('Busy',f"{min(99,int(t['write']/1.1+.5))}%")],
                 'net':[('Interface','wlp0s20f3'),('Address','10.0.4.12'),('Link','1.2 Gb/s · Wi‑Fi 6'),('Packets','2.1 M in · 380 K out')],
                 'en':[('Draw',f"{7+t['en']/12:.1f} W"),('Profile','Balanced'),('Health','94%'),('Cycles','212')]}[r] if self.fixture else getattr(self,'advanced_facts',{}).get(r,[(key,'—') for key in {'mem':['Cached','Swap','zram','Committed'],'disk':['Device','Root','Queue','Busy'],'net':['Interface','Address','Link','Packets'],'en':['Draw','Profile','Health','Cycles']}[r]]))
        row=Gtk.Grid(column_spacing=10,row_spacing=10,column_homogeneous=True,margin_top=12);self.hero.append(row);self.advanced_grid=row
        for i,(key,value) in enumerate(details):
            card=Card(recessed=True,padded=False);words=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,margin_top=9,margin_bottom=9,margin_start=12,margin_end=12);words.append(label(key,'caption',css='mn-fact-key'));words.append(label(value,css='mn-advanced-value'));card.append(words);row.attach(card,i%4,i//4,1,1)

    def _app_details(self,row,app):
        children=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,margin_start=10)
        details=Gtk.Overlay(child=children,margin_start=44,margin_bottom=8,margin_top=2)
        guide=Gtk.Box(width_request=1,halign=Gtk.Align.START);guide.add_css_class('mn-group-rule');details.add_overlay(guide)
        reveal=Gtk.Revealer(child=details,reveal_child=True,transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        row.append(reveal)
        if self.fixture:self._properties(children,fixture_properties(self.source,app))
        else:self._properties(children,self._live_properties(app['procs'][0][1]))
        heading=label('Processes','label')
        heading.set_margin_start(12);heading.set_margin_end(12)
        heading.set_margin_top(6);heading.set_margin_bottom(2)
        children.append(heading)
        for index,(name,pid,cpu,mem) in enumerate(app['procs']):
            value=format_value(mem,'mem') if self.resource=='mem' else f'{cpu:.1f}%' if self.resource=='cpu' and cpu is not None else '—'
            process=next((p for p in getattr(self,'live_processes',[]) if p.pid==pid),None) if not self.fixture else None
            user='nick' if self.fixture else self.sampler.users.get(process.uid,str(process.uid)) if process else '—'
            child=self._process_row([name,pid,user,cpu,mem,process.threads if process else None],None,value=value,child=True)
            if index:
                row=Gtk.Overlay(child=child)
                rule=Gtk.Box(height_request=1,valign=Gtk.Align.START);rule.add_css_class('mn-group-rule');rule.set_can_target(False)
                rule.set_visible(not child.has_css_class('lumaui-selected'));row.add_overlay(rule)
                child.connect('state-flags-changed',lambda button,*_args,rule=rule:rule.set_visible(
                    not button.has_css_class('lumaui-selected') and not (button.get_state_flags() & Gtk.StateFlags.PRELIGHT)))
                children.append(row)
            else:children.append(child)

    def _properties(self,where,facts,*,process=False):
        grid=Gtk.Grid(column_homogeneous=True,column_spacing=1,row_spacing=1,margin_top=2 if process else 4,margin_bottom=10,margin_start=40 if process else 0,margin_end=12 if process else 0);where.append(grid)
        grid.add_css_class('mn-properties')
        line=0;column=0
        for key,value in facts:
            wide=key in ('Command line','Control group')
            if wide and column:line+=1;column=0
            box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=2,margin_top=9,margin_bottom=10,margin_start=12,margin_end=12)
            box.append(label(key,'caption',css='mn-fact-key'));box.append(label(value,css='mn-mono' if wide else 'mn-fact-value',wrap=True))
            cell=Gtk.Box(orientation=Gtk.Orientation.VERTICAL);cell.add_css_class('mn-property-cell');cell.append(box)
            grid.attach(cell,column,line,3 if wide else 1,1)
            column+=3 if wide else 1
            if column>=3:line+=1;column=0

    def _render_groups(self):
        needle=self.query.strip().casefold()
        for group in self.groups:
            container=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,margin_top=18);self.list.append(container)
            rule=Gtk.Box(height_request=1);rule.add_css_class('mn-group-rule');container.append(rule)
            button=Gtk.Button(margin_top=11);button.set_name('mn-group-'+group['id']);button.add_css_class('mn-group')
            line=Gtk.Box(spacing=10,margin_top=6,margin_bottom=6,margin_start=12,margin_end=12)
            expanded=group['id'] in self.groups_open or bool(needle)
            line.append(disclosure_icon(expanded,group=True))
            words=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,hexpand=True);words.append(label(group['n'],css='mn-app-name'))
            if not self._phone():words.append(label(group['why'],'caption',css='mn-subtitle'))  # v71 phone: names only
            else:button.add_css_class('mn-phone-row')
            line.append(words);line.append(CountBadge(group['total']))
            button.set_child(line);button.connect('clicked',lambda _b,g=group['id']:self._toggle_group(g));container.append(button)
            if not expanded:continue
            header=Gtk.Box(spacing=12,margin_start=40,margin_top=8,margin_bottom=4,margin_end=12);header.append(label('Process','label'));header.get_first_child().set_hexpand(True)
            for text,width in [('PID',64),('User',64),('Threads',64),(RESOURCE_NAMES[self.resource] if self.resource in ('cpu','mem') else 'CPU',128)]:
                cell=label(text,'label',end=True);cell.set_size_request(width,-1);header.append(cell)
                if width==64:self._narrow_widgets.append(cell)
            header.set_visible(not self._phone());container.append(header)  # v71 phone: no column headings
            processes=group['p']+(filler_processes(group) if self.fixture and group['id'] in self.more_groups else [])
            processes=[p for p in processes if not needle or needle in p[0].casefold()]
            metric=4 if self.resource=='mem' else 3
            processes=sorted(processes,key=lambda p:(p[metric] is not None,p[metric] or 0),reverse=True)
            for process in processes:
                container.append(self._process_row(process,group))
                if self.process_details==process[1] and not self._phone():self._properties(container,self._process_properties(process,group),process=True)
            if group['id'] not in self.more_groups and not needle and group['total']>len(group['p']):
                more=Gtk.Button(label=f"Show {group['total']-len(group['p'])} more",height_request=30,halign=Gtk.Align.START,margin_start=40,margin_top=6)
                more.set_name('mn-more-'+group['id']);more.connect('clicked',lambda _b,g=group['id']:self._more_group(g));container.append(more)

    def _process_row(self,p,group,*,value=None,child=False):
        name,pid,user,cpu,mem,threads=p
        button=Gtk.Button(height_request=36 if child else 38);button.set_name('mn-process-'+str(pid));button.add_css_class('mn-process-row');line=Gtk.Box(spacing=12,margin_start=12 if child else 40,margin_end=12,margin_top=5,margin_bottom=5)
        if child:button.add_css_class('mn-child-process')
        text=label(name,css='mn-mono');text.set_hexpand(True);line.append(text)
        if not child or self._all():
            for item in ([pid or '—',user] if child else [pid,user,threads]):
                cell=label(item,'caption',end=True);cell.set_size_request(64,-1);line.append(cell);self._narrow_widgets.append(cell)
        v=value if value is not None else format_value(mem,'mem') if self.resource=='mem' and mem is not None else f'{cpu:.1f}%' if self.resource!='mem' and cpu is not None else '—'
        val=label(v,end=True,css='mn-value');val.set_size_request(128,-1)
        if child:val.set_margin_end(26)
        line.append(val);button.set_child(line)
        button.connect('clicked',lambda *_:self._pick('p:'+str(pid)));mark_selected(button,self.selected=='p:'+str(pid));return button

    def _process_properties(self,p,g):
        if not self.fixture:return self._live_properties(p[1])
        from .v70_data import fixture_process_properties
        return fixture_process_properties(self.source,p,g)

    def _render_bar(self):
        self._bar_selection=self.selected
        self._close_menu()
        phone=self._phone()
        if not self.selected:
            self.search=self._make_search()
            # v71: what you're looking at is a picker in the bar wherever the sidebar is gone (a phone, a narrow window).
            actions=([self._resource_picker()] if self.phone_layout or phone else [])+[self.search]
            if self.all_processes and not phone:actions.append(BarAction('layers','Apps only',on_activate=self._toggle_all))
            self.center.show_bar(actions,fill=phone)
            self.search.widget.set_name('mn-search')
            self._name_bar()
            return
        app,p,g=self._subject()
        if phone:
            # v71 phone: the primary fills the row, Force quit is a red glyph that confirms in the bar,
            # Details grows the bar with the app's facts, and ✕ is Done. No chip, no ⋯.
            done=BarAction('x',tooltip='Done',on_activate=self._deselect)
            if app:
                stopped=self.fixture and app['id'] in self.source.stopped
                if not self.fixture:self._live_properties(app['procs'][0][1])  # start reading, so Details opens filled in
                actions=[BarAction('play' if stopped else '','Continue' if stopped else f"Quit {app['n']}",primary=True,fill=True,keep_label=True,
                                   tooltip=None if stopped else f"Asks {app['n']} to close and handle unsaved work",on_activate=lambda:self._control('resume' if stopped else 'quit')),
                         BarAction('octagon-x',tooltip='Force quit',danger=True,on_activate=self._ask_force),
                         BarAction('info',tooltip='Details',key='details',panel=lambda a=app:self._details_panel(a)),done]
            elif p:
                if g and g.get('kernel'):actions=[BarAction('lock',"Part of the kernel. It can't be ended",sensitive=False,keep_label=True,fill=True),done]
                else:actions=[BarAction('','End process',tooltip='SIGTERM',primary=True,fill=True,sensitive=self.fixture,on_activate=lambda:self._control('end')),
                              BarAction('octagon-x',tooltip='Kill',danger=True,on_activate=self._ask_force),done]
            else:self.selected=None;self._render_bar();return
            self.center.show_bar(actions,fill=True);self._name_bar();return
        if app:
            stopped=self.fixture and app['id'] in self.source.stopped
            actions=[BarChip(app['n'],lead=app_icon(app,24),on_dismiss=self._deselect),
                     BarAction('play' if stopped else '', 'Continue' if stopped else 'Quit',tooltip=None if stopped else f"Asks {app['n']} to close and handle unsaved work",primary=True,on_activate=lambda:self._control('resume' if stopped else 'quit')),
                     self._raised_force('Force quit', 'Stops it immediately (SIGKILL)'),
                     BarAction('list','Hide details' if app['id'] in self.open_apps else 'Details',active=app['id'] in self.open_apps,on_activate=lambda:self._expand(app['id'])),
                     SEPARATOR,BarAction('ellipsis',tooltip='More',on_activate=self._more)]
        elif p:
            kernel=g and g.get('kernel')
            actions=[BarChip(p[0][:25]+'…' if len(p[0])>26 else p[0],meta=str(p[1]) if p[1] else None,on_dismiss=self._deselect)]
            if not kernel:actions += [BarAction('', 'End process',tooltip='SIGTERM',primary=True,sensitive=self.fixture,on_activate=lambda:self._control('end')),self._raised_force('Kill', 'SIGKILL')]
            else:actions += [BarAction('lock',"Part of the kernel. It can't be ended",sensitive=False)]
            if g:actions += [BarAction('list','Details',on_activate=self._toggle_process_details)]
            actions += [SEPARATOR,BarAction('ellipsis',tooltip='More',on_activate=self._more)]
        else:self.selected=None;self._render_bar();return
        self.center.show_bar(actions)
        self._name_bar()

    def _raised_force(self,label,tooltip):
        control=TextButton(label,icon='octagon-x',style='raised',danger=True,on_click=self._ask_force)
        control.set_name('mn-force');control.set_tooltip_text(tooltip)
        return BarWidget(control)

    def _name_bar(self):
        names={'Force quit':'mn-force','Kill':'mn-force','More':'mn-more','Details':'mn-details','Hide details':'mn-details','Quit':'mn-quit','Continue':'mn-continue','End process':'mn-end','Done':'mn-done','Apps only':'mn-all'}
        for widget in list_children(self.center.bar_row):
            item=getattr(widget,'bar_item',None)
            if isinstance(item,BarAction):
                key=item.label or item.tooltip
                if item.dropdown:widget.set_name('mn-resource-picker')
                elif key and key.startswith('Quit '):widget.set_name('mn-quit')
                elif key in names:widget.set_name(names[key])

    def _resource_values(self):
        t=self.totals
        return {'cpu':f"{int(t.get('cpu',0)+.5)}%" if t.get('cpu') is not None else '—',
                'mem':f"{t.get('mem',0)/1024:.1f} GB" if t.get('mem') is not None else '—',
                'disk':format_value((t.get('read') or 0)+(t.get('write') or 0),'disk') if t.get('read') is not None else '—',
                'net':format_value(t.get('down'),'net'),
                'en':'74%' if self.fixture else t.get('percentage','—')}

    def _resource_picker(self):
        _key,name,icon=next(r for r in RESOURCES if r[0]==self.resource)
        return BarAction(icon,name,tooltip=f'Showing {name}',dropdown=True,key='resource',panel=self._resource_panel)

    def _resource_panel(self):
        values=self._resource_values();rows=[]
        for k,title,icon in RESOURCES:
            # Switch after the panel has folded; switching rebuilds the bar the row sits in.
            row=PanelRow(title,icon=icon,detail=values[k],current=k==self.resource,on_activate=lambda k=k:GLib.idle_add(self._pick_resource,k))
            row.set_name('mn-pick-'+k);rows.append(row)
        return panel_list(rows,label='Resource')

    def _pick_resource(self,key):
        if not self.closed:self._resource(key)
        return GLib.SOURCE_REMOVE

    def _details_panel(self,app):
        """v71 phone: the app's facts as a two-column list under its icon, name and process count."""
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=10)
        head=Gtk.Box(spacing=12,margin_start=4,margin_end=4,margin_top=2);head.append(app_icon(app,40))
        words=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,valign=Gtk.Align.CENTER)
        count=len(app['procs']);words.append(label(app['n'],'monitor-detail-title'))
        words.append(label(f"{count} process{'' if count==1 else 'es'} · {app['th']} threads",'monitor-detail-meta',muted=True));head.append(words);box.append(head)
        facts=fixture_properties(self.source,app) if self.fixture else self._live_properties(app['procs'][0][1])
        grid=Gtk.Grid(column_spacing=16,row_spacing=8,margin_start=4,margin_end=4,margin_bottom=4)
        for row,(key,value) in enumerate(facts):
            name=label(key,css='mn-details-key');name.set_valign(Gtk.Align.START);grid.attach(name,0,row,1,1)
            wide=key in ('Command line','Control group')
            text=label(value,'monitor-detail-mono' if wide else 'body',wrap=True,end=True,ink='secondary' if wide else None)
            text.set_justify(Gtk.Justification.RIGHT)
            text.set_hexpand(True);text.set_selectable(False);grid.attach(text,1,row,1,1)
        box.append(grid)
        return box

    def _subject(self):
        if not self.selected:return None,None,None
        kind,identifier=self.selected.split(':',1)
        if kind=='a':return next((a for a in self.apps if a['id']==identifier),None),None,None
        for a in self.apps:
            for p in a['procs']:
                if str(p[1])==identifier:return None,p,None
        for g in self.groups:
            for p in g['p']+(filler_processes(g) if self.fixture else []):
                if str(p[1])==identifier:return None,p,g
        return None,None,None

    def _more(self):
        app,p,g=self._subject();kernel=g and g.get('kernel')
        rows=[]
        if not kernel:
            stopped=self.fixture and (app['id'] if app else 'p'+str(p[1])) in self.source.stopped
            rows += [MenuItem('Continue (SIGCONT)' if stopped else 'Stop (SIGSTOP)',icon='play' if stopped else 'pause',on_activate=lambda:self._control('resume' if stopped else 'pause')),MenuItem('Change priority…',icon='arrow-down',on_activate=self._priority)]
        rows += [MenuItem('Memory maps',icon='layers',on_activate=lambda:self._open_sheet('maps')),MenuItem('Open files',icon='file-text',on_activate=lambda:self._open_sheet('files')),MenuItem('Copy PID',icon='copy',on_activate=self._copy_pid)]
        self._show_menu(rows)
        self._name_menu({'Change priority…':'mn-priority','Open files':'mn-files','Memory maps':'mn-maps','Stop (SIGSTOP)':'mn-stop','Continue (SIGCONT)':'mn-resume','Copy PID':'mn-copy'})
        if not self.fixture:
            for button in self._menu_buttons():
                if button.get_name() in ('mn-stop','mn-resume','mn-priority'):button.set_sensitive(False)

    def _show_menu(self,rows):
        self._close_menu()
        anchor=self.center.bar_row.get_last_child()
        menu=FloatingMenu(rows).popup(anchor,align='center');self._menu_widget=menu
        if not lumaui.is_phone_width(self.layer_host):
            # mnPop centres its card on More. Place through the kit's public layout
            # helper after the new action bar has received its allocation.
            def position():
                if self._menu_widget is not menu or not menu.is_open:return GLib.SOURCE_REMOVE
                ok,bounds=anchor.compute_bounds(self.layer_host)
                if not ok:return GLib.SOURCE_REMOVE
                rect=Gdk.Rectangle();rect.x=int(bounds.origin.x);rect.y=int(bounds.origin.y)
                rect.width=int(bounds.size.width);rect.height=int(bounds.size.height)
                float_at(self.layer_host,menu,rect,prefer='above',align='center',offset=8,edge=8)
                return GLib.SOURCE_REMOVE
            lumaui.on_next_frame(self.layer_host,lambda:GLib.idle_add(position))

    def _close_menu(self):
        menu=self._menu_widget;self._menu_widget=None
        if menu:menu.close()

    def _menu_buttons(self):
        roots=[]
        if self._menu_widget:roots.append(self._menu_widget)
        for host in (self.host,self.layer_host):
            if host.modal:roots.append(host.modal.card)
        for root in roots:
            for child in descendants(root):
                if isinstance(child,Gtk.Button):yield child

    def _name_menu(self,names):
        for button in self._menu_buttons():
            labels=[c.get_label() for c in descendants(button) if isinstance(c,Gtk.Label)]
            for text,name in names.items():
                if text in labels:button.set_name(name)

    def _priority(self):
        app,p,_g=self._subject();key=app['id'] if app else 'p'+str(p[1])
        rows=['Priority',*[RichMenuItem(title,subtitle=f'nice {value}'+(' · needs your password' if int(value)<0 else ''),selected=self.source.priority.get(key,'0')==value,on_activate=lambda v=value:self._set_priority(v)) for value,title in self.source.priorities]]
        self._show_menu(rows)
        self._name_menu({title:'mn-priority-'+value for value,title in self.source.priorities})

    def _set_priority(self,value):
        app,p,g=self._subject()
        if not self.fixture:return
        if app:self.source.set_priority(app['id'],value)
        elif p:self.source.set_process_priority(p[1],value)
        Toast.show(self.host,f"{app['n'] if app else p[0]} now runs at {dict(self.source.priorities)[value].lower()} priority (nice {value})")
        self._render()

    def _open_sheet(self,kind):
        app,p,g=self._subject();name=app['n'] if app else p[0];pid=app['procs'][0][1] if app else p[1];identifier=app['id'] if app else 'x'
        if not self.fixture:self._read_inspection(kind,app,p,g);return
        if kind=='files':
            cols=['FD','Type','Object'];rows=[['0','file','/dev/null'],['1','pipe','pipe:[48213]'],['3','socket','socket:[51720] · wayland-0'],['7','socket','socket:[51733] · pipewire-0'],['12','file',f'/home/nick/.var/app/org.luma.{identifier.capitalize()}/cache/index.db'],['14','file',f'/home/nick/.var/app/org.luma.{identifier.capitalize()}/config/settings.json'],['19','socket','TCP 10.0.4.12:51022 → simplyluma.com:443'],['22','memfd','memfd:wayland-shm (deleted)']]
        else:
            cols=['Address','Flags','Size','File'];rows=[['5578a3c10000','r-xp','1.9 MB',f'/usr/bin/luma-{identifier}'],['7f3a1c000000','rw-p','312 MB','[heap]'],['7f3a9e200000','r-xp','1.9 MB','/usr/lib64/libc.so.6'],['7f3a9e5c0000','r-xp','6.4 MB','/usr/lib64/libgtk-4.so.1'],['7f3a9f100000','r-xp','3.1 MB','/usr/lib64/libglib-2.0.so.0'],['7f3aa0400000','rw-s','32 MB','/memfd:mesa-shared (deleted)'],['7ffd5c1e0000','rw-p','132 KB','[stack]'],['7ffd5c3f8000','r-xp','8 KB','[vdso]']]
        self._present_sheet(kind,name,pid,cols,rows)

    def _present_sheet(self,kind,name,pid,cols,rows):
        card=Card(padded=False);card.set_name('mn-sheet');card.set_size_request(0,-1)
        content=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,margin_top=8,margin_bottom=12,margin_start=8,margin_end=8)
        heading=Gtk.Box(spacing=12,margin_start=12,margin_end=8,margin_top=8,margin_bottom=10)
        words=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,hexpand=True);words.append(label('Open files' if kind=='files' else 'Memory maps','title-2',css='mn-sheet-title'));words.append(label(f'{name} · PID {pid}','caption',css='mn-sheet-subtitle'));heading.append(words)
        close=Gtk.Button(child=icons.image('x'),tooltip_text='Close');close.set_name('mn-sheet-close');heading.append(close);content.append(heading)
        table=Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        # Intrinsic v70 columns, less the TableHeader's shared 8px gaps.
        widths=[31,61] if kind=='files' else [106,46,61]
        header=TableHeader([Column(None,col,expand=i==len(cols)-1,width=widths[i] if i<len(widths) else None) for i,col in enumerate(cols)])
        table.append(header)
        body=Selection.apply(Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE));table.append(body)
        for row in rows:
            line=Gtk.Box()
            for colnum,text in enumerate(row):
                cell=label(text,css='mn-sheet-value',wrap=colnum==len(cols)-1);cell.set_margin_top(7);cell.set_margin_bottom(7);line.append(cell)
            header.align(line);body.append(line);line.get_parent().add_css_class('mn-sheet-row')
        content.append(ScrollView(table,propagate_natural_height=True));card.append(content)
        sheet_width=min(720,max(1,self.island.get_width()-48))
        clamp=Adw.Clamp(child=card,maximum_size=sheet_width,tightening_threshold=sheet_width,width_request=sheet_width,hexpand=True);self.sheet_handle=self.host.present_modal(clamp,drawer=False);close.connect('clicked',lambda *_:self.sheet_handle.close())

    def _ask_force(self):
        app,p,g=self._subject()
        if not app and not p:return
        if self.fixture:
            confirm=lambda _:self._control('force' if app else 'kill')
        else:
            target=self.live_rows.get(app['id']) if app else next((process for process in self.live_processes if process.pid==p[1]),None)
            selection=self.selected
            confirm=lambda _:self._request_force(target,app['n'] if app else p[0],selection)
        title=f"Force quit {app['n']}?" if app else f'Kill {p[0]}?'
        body='It stops at once, and anything unsaved is lost.' if app else 'It stops at once, without a chance to save or clean up.'
        if self._phone() and app:
            # App Force quit grows the phone bar; process Kill uses the modal confirmation.
            self.confirm_dialog=DestructiveDialog.in_bar(self.center,title=title,body=body,action='Force quit' if app else 'Kill',on_confirm=lambda:confirm(False))
            return
        self.confirm_dialog=DestructiveDialog.ask(self.host,title=title,body=body,action='Force quit' if app else 'Kill',icon='triangle-alert',on_confirm=confirm)

    def _request_force(self,target,name,selection):
        if self.closed or target is None or self.selected!=selection:return
        def read():
            from .integration import ForceQuitError,force_quit_processes,force_quit_row
            try:
                count,total=force_quit_row(target) if isinstance(target,dict) else force_quit_processes((target,))
                if not count:message=f'{name} exited or changed before it could be killed';kind='error'
                elif isinstance(target,dict):
                    message=f'Sent force quit to {count} of {total} {name} processes';kind='done'
                else:message=f'Killed {name}';kind='done'
            except ForceQuitError as error:
                count=error.sent;message=str(error);kind='error'
            except (OSError,ValueError,PermissionError,ProcessLookupError) as error:
                count=0;message=str(error);kind='error'
            def show_result():
                if not self.closed:
                    if count and self.selected==selection:self.selected=None;self._render_bar()
                    Toast.show(self.host,message,kind=kind)
                return GLib.SOURCE_REMOVE
            GLib.idle_add(show_result)
        threading.Thread(target=read,daemon=True,name='monitor-force-quit').start()

    def _control(self,action):
        app,p,g=self._subject()
        if not self.fixture:
            if action=='quit' and app:self._request_quit(app)
            return
        if app:
            name=app['n'];identifier=app['id']
            if action in ('quit','force'):
                self.source.quit(identifier);self.selected=None;self.totals=fixture_totals(self.source);self._fixture_history();Toast.show(self.host,f"{name} {'was force quit (SIGKILL)' if action=='force' else 'quit'}",undo=lambda:self._reopen(identifier))
            elif action=='pause':self.source.pause(identifier);self.totals=fixture_totals(self.source);self._fixture_history();Toast.show(self.host,f"{name} is paused. Nothing it's doing is lost (SIGSTOP)",kind='paused')
            elif action=='resume':self.source.resume(identifier);self.totals=fixture_totals(self.source);self._fixture_history();Toast.show(self.host,f'{name} is running again (SIGCONT)')
        elif p:
            if g and g.get('kernel'):return
            if action=='pause':
                self.source.pause_process(p[1]);Toast.show(self.host,f"{p[0]} is paused. Nothing it's doing is lost (SIGSTOP)",kind='paused')
            elif action=='resume':
                self.source.resume_process(p[1]);Toast.show(self.host,f'{p[0]} is running again (SIGCONT)')
            else:
                Toast.show(self.host,f"{'Killed' if action=='kill' else 'Ended'} {p[0]} ({p[1]})");self.selected=None
        self._render()

    def _reopen(self,identifier):self.source.reopen(identifier);self.totals=fixture_totals(self.source);self._fixture_history();self._render()
    def _copy_pid(self):
        app,p,g=self._subject();pid=app['procs'][0][1] if app else p[1];self.get_clipboard().set(str(pid));Toast.show(self.host,f'Copied PID {pid}',kind='copied')
    def _visible_apps(self):return self.source.visible_apps() if self.fixture else self.apps
    def _pick(self,key):self.selected=None if self.selected==key else key;self._render()
    def _deselect(self):self.selected=None;self.process_details=None;self._render()
    def _expand(self,key):
        self.open_apps.symmetric_difference_update({key})
        if not self.selected or not self.selected.startswith('p:'):self.selected='a:'+key
        self._render()
    def _toggle_all(self):self.all_processes=not self.all_processes;self._render()
    def _toggle_group(self,key):self.groups_open.symmetric_difference_update({key});self._render()
    def _more_group(self,key):self.more_groups.add(key);self._render_body();self._adapt()
    def _toggle_process_details(self):
        app,p,g=self._subject();self.process_details=None if self.process_details==p[1] else p[1];self._render()
    def _sort(self,key,direction):self.sort=(key,direction);self._render_body();self._adapt()
    def _search_changed(self,text):
        self.query=text
        if text and not self.all_processes and not any(text.casefold() in a['n'].casefold() for a in self._visible_apps()):self.all_processes=True
        if hasattr(self,'list'):self._render_body();self._adapt()
    def _profile(self,value):
        if not self.fixture:return
        self.profile=value;Toast.show(self.host,{'saver':'Battery saver: dimmer, slower, and it lasts longer','bal':'Balanced','perf':'Performance: faster, warmer, and it drains sooner'}[value]);self._render_body();self._adapt()
    def _layout_metrics(self):
        for attr,columns in [('core_grid',4 if self.phone_layout else 8),('advanced_grid',4 if self.wide_layout else 2)]:
            grid=getattr(self,attr,None)
            if grid is None:continue
            children=sorted(list_children(grid),key=lambda child:(grid.query_child(child)[1],grid.query_child(child)[0]))
            for child in children:grid.remove(child)
            for i,child in enumerate(children):
                if attr=='core_grid':child.set_size_request(-1,51 if self.phone_layout else 70)
                grid.attach(child,i%columns,i//columns,1,1)

    def _adapt(self):
        self._layout_metrics()
        for widget in self._narrow_widgets+[r.spark for r in getattr(self,'resource_rows',{}).values()]:
            if not getattr(widget,'_mn_width_bound',False):
                self.bind_property('wide-layout',widget,'visible',GObject.BindingFlags.SYNC_CREATE)
                widget._mn_width_bound=True
    def _key(self,_controller,key,_code,_state):
        if key==Gdk.KEY_Escape and self.selected:self._deselect();return True
        return False
    def _mapped(self,*_):
        if not self.fixture and not self.timer:
            self._sample()
            self.timer=GLib.timeout_add_seconds(1,self._sample)
    def _unmapped(self,*_):
        if self.timer:GLib.source_remove(self.timer);self.timer=0
        self.generation+=1
    def _closing(self,*_):
        self.closed=True;self._close_menu();self._unmapped()
        if hasattr(self,'catalog'):self.catalog.close()
        return False

    def _sample(self):
        if self.fixture or self.closed or self.working:return GLib.SOURCE_CONTINUE
        self.working=True;self.generation+=1;generation=self.generation
        def read():
            try:
                from .model import Sampler
                from .integration import DesktopCatalog,battery_info,performance_profile
                if not hasattr(self,'catalog'):
                    if Path('/.flatpak-info').exists():
                        from .host_sampler import HostCatalog, HostSampler
                        self.catalog=HostCatalog(); self.sampler=HostSampler()
                    else:
                        self.catalog=DesktopCatalog();self.sampler=Sampler(resolve=self.catalog.resolve)
                if self.closed:
                    self.catalog.close();return
                self.catalog.refresh_running()
                if self.sampler.previous is None:
                    # Publish real process/RSS data before optional service
                    # activation and expensive proportional-memory reads.
                    # CPU needs two samples; its initial absence stays honest.
                    initial=self.sampler.sample(memory=False)
                    initial['battery']=None;initial['profile']=None
                    from .live_data import resource_facts
                    initial['advanced_facts']=initial.get('advanced_facts') or resource_facts(initial)
                    GLib.idle_add(self._sampled,generation,initial,None,False)
                    time.sleep(.2)
                    counters=self.sampler.sample(memory=False)
                    counters['battery']=None;counters['profile']=None
                    counters['advanced_facts']=counters.get('advanced_facts') or resource_facts(counters)
                    GLib.idle_add(self._sampled,generation,counters,None,False)
                snapshot=self.sampler.sample(memory=True)
                if not getattr(self.sampler, 'host', False):
                    snapshot['battery']=battery_info();snapshot['profile']=performance_profile()
                from .live_data import resource_facts
                snapshot['advanced_facts']=snapshot.get('advanced_facts') or resource_facts(snapshot)
                try:
                    if not getattr(self.sampler, 'host', False):
                        disk=os.statvfs('/');snapshot['disk_free']=(disk.f_bavail*disk.f_frsize,disk.f_blocks*disk.f_frsize)
                except OSError:snapshot['disk_free']=None
                error=None
            except Exception as failure:snapshot=None;error=str(failure)
            GLib.idle_add(self._sampled,generation,snapshot,error)
        threading.Thread(target=read,daemon=True,name='monitor-sample').start();return GLib.SOURCE_CONTINUE

    def _sampled(self,generation,snapshot,error,complete=True):
        if complete:self.working=False
        if self.closed or generation!=self.generation:return GLib.SOURCE_REMOVE
        if error:
            if not self.sample_failed:Toast.show(self.host,'Couldn’t read system activity',kind='error')
            self.sample_failed=True
            title,subtitle=self._hero_text()
            self.hero_title.set_label(title);self.hero_subtitle.set_label(subtitle)
            return GLib.SOURCE_REMOVE
        self.sample_failed=False
        self.live_rows={};self.apps=[];self.live_processes=snapshot['processes']
        for row in snapshot['rows']:
            if row['background']:continue
            self.live_rows[row['id']]=row
            procs=[[p.name,p.pid,p.cpu,(p.memory or p.rss)/1024**2 if p.memory is not None or p.rss is not None else None] for p in row['members']]
            self.apps.append({'id':row['id'],'n':row['name'],'icon':row['icon'],'sub':f"{len(procs)} processes · {row['threads']} threads",'procs':procs,'th':row['threads'],'cpu':row['cpu'],'mem':row['memory']/1024**2 if row['memory'] is not None else None,'disk':(row['reading']+row['writing'])/1e6 if row['reading'] is not None and row['writing'] is not None else None,'net':None,'en':None})
        from .live_data import process_groups
        app_pids={p[1] for app in self.apps for p in app['procs']}
        self.groups=process_groups(snapshot['processes'],app_pids,self.sampler.users)
        self.advanced_facts=snapshot['advanced_facts'];self.profile=snapshot.get('profile')
        self.advanced_facts['en'][1]=('Profile',{'saver':'Battery saver','bal':'Balanced','perf':'Performance'}.get(self.profile,'—'))
        self.memory_info=snapshot['memory']
        valid={(p.pid,p.start,p.cgroup) for p in snapshot['processes']}
        self.properties={k:v for k,v in self.properties.items() if k in valid}
        mem=snapshot['memory'];used=(mem.get('MemTotal',0)-mem.get('MemAvailable',0))/1024**2
        self.totals={'cpu':snapshot['cpu'],'appsCpu':snapshot['apps_cpu'],'sysCpu':snapshot['system_cpu'],'mem':used,'totalMem':mem.get('MemTotal',0)/1024**2,
                     'read':snapshot['disk_rates'][0]/1e6 if snapshot['disk_rates'][0] is not None else None,'write':snapshot['disk_rates'][1]/1e6 if snapshot['disk_rates'][1] is not None else None,
                     'down':snapshot['network_rates'][0]/1e6 if snapshot['network_rates'][0] is not None else None,'up':snapshot['network_rates'][1]/1e6 if snapshot['network_rates'][1] is not None else None}
        battery=snapshot.get('battery') or {}
        pct=battery.get('percentage');remaining=battery.get('remaining')
        self.totals['percentage']=f'{int(pct+.5)}%' if pct is not None else '—'
        self.totals['remaining']=f'{remaining//3600} h {remaining%3600//60:02d} m' if remaining is not None else '—'
        self.totals['batteryText']=self.totals['percentage']+(' · '+self.totals['remaining']+' left' if remaining else '')
        if snapshot.get('disk_free'):
            free,total=snapshot['disk_free'];self.totals['free']=f'{free/1e9:.0f} GB of {total/1e9:.0f}'
        received=snapshot.get('network_received')
        if received is not None:
            if not hasattr(self,'network_start'):self.network_start=received
            self.totals['session']=f'{max(0,received-self.network_start)/1e9:.1f} GB'
        self.cores=snapshot['cores']
        for k,pair in [('cpu',(snapshot['apps_cpu'],snapshot['system_cpu'])),('mem',(used,0)),('disk',(self.totals['read'],self.totals['write'])),('net',(self.totals['down'],self.totals['up'])),('en',(None,None))]:self.histories[k].append(pair)
        title,subtitle=self._hero_text()
        self.hero_title.set_label(title);self.hero_subtitle.set_label(subtitle)
        if self.selected and not self._subject()[0] and not self._subject()[1]:self.selected=None
        self._render_sidebar()
        self.chart.update(self.histories[self.resource],self._chart_max(self.resource))
        interacting=(self._menu_widget and self._menu_widget.is_open) or self.host.modal or self.layer_host.modal
        if not interacting:
            adjustment=self.scroll.get_vadjustment();position=adjustment.get_value()
            focus=self.get_focus()
            focus_name=focus.get_name() if focus and focus.is_ancestor(self.list) else None
            self._render_body();self._adapt();adjustment.set_value(position)
            if focus_name:
                replacement=next((widget for widget in descendants(self.list) if widget.get_name()==focus_name and widget.get_focusable()),None)
                if replacement:replacement.grab_focus()
        if self.selected!=getattr(self,'_bar_selection',self.selected):self._render_bar()
        return GLib.SOURCE_REMOVE

    def _request_quit(self,app):
        row=self.live_rows.get(app['id'])
        if not row:return
        def read():
            from .integration import quit_row
            try:ok=quit_row(row);message=f"{app['n']} was asked to quit" if ok else f"{app['n']} doesn’t accept quit requests"
            except Exception as error:message=str(error)
            def show_result():
                if not self.closed:Toast.show(self.host,message)
                return GLib.SOURCE_REMOVE
            GLib.idle_add(show_result)
        threading.Thread(target=read,daemon=True,name='monitor-quit').start()

    def _live_properties(self,pid):
        process=next((p for p in getattr(self,'live_processes',[]) if p.pid==pid),None)
        if process is None:return [('Status','Process exited')]
        key=(process.pid,process.start,process.cgroup)
        if key in self.properties:return self.properties[key]
        if key not in self.properties_working:
            self.properties_working.add(key)
            def read():
                from .inspection import inspect_properties
                try:facts=inspect_properties(process)
                except (OSError,ValueError):facts=[('Status','Couldn’t inspect process')]
                GLib.idle_add(self._properties_ready,key,facts)
            threading.Thread(target=read,daemon=True,name='monitor-properties').start()
        return [('Status','Loading…')]

    def _properties_ready(self,key,facts):
        self.properties_working.discard(key)
        if self.closed:return GLib.SOURCE_REMOVE
        if not any((p.pid,p.start,p.cgroup)==key for p in getattr(self,'live_processes',[])):return GLib.SOURCE_REMOVE
        self.properties[key]=facts
        if self.open_apps or self.process_details:self._render_body();self._adapt()
        return GLib.SOURCE_REMOVE

    def _read_inspection(self,kind,app,p,g):
        pid=app['procs'][0][1] if app else p[1]
        process=next((p for p in getattr(self,'live_processes',[]) if p.pid==pid),None)
        if process is None:return
        identity=(process.pid,process.start,process.cgroup)
        selection=self.selected;self.inspection_generation=getattr(self,'inspection_generation',0)+1
        token=self.inspection_generation
        def read():
            from .inspection import inspect_table
            try:columns,rows=inspect_table(process,kind);error=None
            except (OSError,ValueError) as failure:columns,rows=[],[];error=str(failure)
            GLib.idle_add(self._inspection_ready,token,selection,kind,app['n'] if app else p[0],identity,columns,rows,error)
        threading.Thread(target=read,daemon=True,name='monitor-inspection').start()

    def _inspection_ready(self,token,selection,kind,name,identity,columns,rows,error):
        if self.closed or token!=self.inspection_generation or selection!=self.selected:return GLib.SOURCE_REMOVE
        if not any((p.pid,p.start,p.cgroup)==identity for p in getattr(self,'live_processes',[])):return GLib.SOURCE_REMOVE
        if error:Toast.show(self.host,'Couldn’t inspect process',kind='error')
        else:self._present_sheet(kind,name,identity[0],columns,rows)
        return GLib.SOURCE_REMOVE


def list_children(widget):
    result=[];child=widget.get_first_child()
    while child:result.append(child);child=child.get_next_sibling()
    return result

def descendants(widget):
    for child in list_children(widget):yield child;yield from descendants(child)

from .v70_data import activity_matches

class MonitorApplication(Adw.Application):
    def __init__(self):super().__init__(application_id=APP_ID,flags=Gio.ApplicationFlags.NON_UNIQUE if os.environ.get('LUMA_MONITOR_FIXTURE') else Gio.ApplicationFlags.DEFAULT_FLAGS)
    def do_startup(self):
        Adw.Application.do_startup(self);install_appkit();install_lumaui()
        app_icons=Path(__file__).resolve().parents[3]/'assets/icon-theme/Prairie/scalable/apps'
        if app_icons.is_dir():Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(str(app_icons))
        add_style_sheet(os.environ.get('LUMA_MONITOR_STYLE_PATH',str(Path(__file__).resolve().parents[1]/'data/monitor.css')))
    def do_activate(self):(self.get_active_window() or MonitorWindow(self)).present()

def main():
    if '--this-machine' in sys.argv and not os.environ.get('LUMA_MONITOR_FIXTURE'):
        os.environ.setdefault('LUMA_MONITOR_STYLE_PATH',str(Path(__file__).resolve().parents[1]/'data/machine.css'))
        from .machine_application import main as machine_main
        return machine_main()
    return MonitorApplication().run([])

if __name__=='__main__':raise SystemExit(main())
