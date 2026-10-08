# SPDX-License-Identifier: GPL-3.0-only
"""Physical pointer coverage for portal menus and hover-revealed sliders."""
from gi.repository import GLib


class HoverQualification:
    def __init__(self, controls):
        self.controls = controls
        self.window = controls.window
        controls.evaluate("""(() => {
          document.head.innerHTML='<style>html{overflow-y:scroll}body{margin:0;height:8000px}.volume{position:absolute;right:8px;top:240px;width:140px;height:40px;background:#333}.volume button{position:absolute;right:0;width:30px;height:40px}.slider{display:none;position:absolute;right:34px;top:10px;width:95px}@media(hover:hover) and (pointer:fine){.volume:hover .slider{display:block}}</style>';
          document.body.innerHTML='<button id="trigger" style="position:absolute;right:8px;top:100px;width:40px;height:30px">Menu</button><div class="volume" id="volume"><button id="mute">M</button><input class="slider" id="slider" type="range" value="25"></div>';
          window._hoverTest={selected:0,volume:25,events:[]};
          trigger.onpointerdown=e=>{if(e.button!==0)return;e.preventDefault();document.querySelector('#menu')?.remove();const menu=document.createElement('div');menu.id='menu';menu.style='position:absolute;right:8px;top:135px;width:130px;height:70px;background:#aaa';menu.innerHTML='<button id="entry" style="position:absolute;right:0;width:20px;height:35px">Go</button>';document.body.append(menu);entry.onclick=()=>{_hoverTest.selected++;menu.remove()}};
          slider.oninput=()=>_hoverTest.volume=Number(slider.value);
          for(const type of ['pointerdown','pointerup','pointermove','click'])document.addEventListener(type,e=>{if(_hoverTest.events.length<50)_hoverTest.events.push({type,id:e.target.id,buttons:e.buttons,x:e.clientX,y:e.clientY})});
          return {hover:matchMedia('(hover:hover)').matches,anyHover:matchMedia('(any-hover:hover)').matches,fine:matchMedia('(pointer:fine)').matches,coarse:matchMedia('(pointer:coarse)').matches,touch:navigator.maxTouchPoints};
        })()""", self.prepared)

    def prepared(self, capabilities):
        self.controls.report['pointer_capabilities'] = capabilities
        adapter = self.window.page_input
        def ready(result):
            adapter.viewport = dict(result['cssLayoutViewport'], input_zoom=result['cssVisualViewport']['zoom'])
            self.controls.report['pointer_geometry'] = dict(width=self.window.page.get_width(),viewport=adapter.viewport)
            GLib.timeout_add(200, self.open_menu)
        adapter.page_requests.call('Page.getLayoutMetrics', {}, adapter.session, ready)

    def point_element(self, selector, click=False):
        import json
        def move(rect):
            page = self.window.page
            self.controls.point(page, (rect['x']/page.get_width(),rect['y']/page.get_height()),click=click)
        self.controls.evaluate("(() => {const r=document.querySelector("+json.dumps(selector)+").getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()",move)

    def open_menu(self):
        self.point_element('#trigger',True)
        GLib.timeout_add(250,self.choose_entry)
        return False

    def choose_entry(self):
        self.point_element('#entry',True)
        GLib.timeout_add(250,self.hover_volume)
        return False

    def hover_volume(self):
        self.point_element('#mute')
        GLib.timeout_add(450,lambda:self.controls.evaluate("({selected:_hoverTest.selected,visible:slider.getBoundingClientRect().width>0,hovered:volume.matches(':hover'),muteHovered:mute.matches(':hover'),events:_hoverTest.events})",self.hover_result) or False)
        return False

    def hover_result(self, result):
        self.controls.report['hover_controls'] = result
        self.controls.report['checks'].extend([{'portal_menu_entry_click':result['selected']==1},{'hover_reveals_volume_slider':result['visible']}])
        if not result['visible']:
            self.controls.finish('Hover-gated volume slider did not reveal')
            return
        self.point_element('#slider',True)
        GLib.timeout_add(250,lambda:self.controls.evaluate('_hoverTest',self.done) or False)

    def done(self, result):
        self.controls.report['volume_result'] = result['volume']
        self.controls.report['checks'].append({'volume_slider_changes_value':result['volume']!=25})
        self.controls.finish(None if result['selected']==1 and result['volume']!=25 else 'Dropdown or volume click failed')
