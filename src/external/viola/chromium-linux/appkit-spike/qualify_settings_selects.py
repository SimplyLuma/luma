# SPDX-License-Identifier: GPL-3.0-only
"""Physical option selection in the actual packaged Linux settings page."""
import json
import time
from gi.repository import GLib, Gtk, Gdk
from native_menu import native_accelerator


class SettingsSelectQualification:
    def __init__(self, controls):
        self.controls, self.window = controls, controls.window
        self.started = time.monotonic()
        self.phase = 0
        self.window.send('settings:open')
        GLib.timeout_add(100, self.wait_page)

    def wait_page(self):
        if time.monotonic()-self.started > 12:
            self.controls.finish('Settings page did not become ready'); return False
        if not (self.window.state or {}).get('activeUrl','').startswith('chrome://viola/settings.html') or not self.window.page_input.valid_identity():
            return True
        self.controls.evaluate("typeof D !== 'undefined' && !!D && !!document.querySelector('select[aria-label=\"Default search engine\"]')", self.ready)
        return False

    def ready(self, ok):
        if not ok:
            GLib.timeout_add(100,self.wait_page);return
        import os
        if os.environ.get('VIOLA_QA_SETTINGS_DARK') == '1':
            self.controls.evaluate("document.body.classList.add('dark');true",lambda _:self.next_select())
        else:
            self.next_select()

    def next_select(self):
        cases=[('Default search engine','duckduckgo','searchEngine'),('Close idle Mini Viola windows','0','miniViolaAutoCloseHours')]
        self.label,self.value,self.setting=cases[self.phase]
        selector='select[aria-label='+json.dumps(self.label)+']'
        self.selector=selector
        self.controls.evaluate("(() => {const s=document.querySelector("+json.dumps(selector)+");s.scrollIntoView({block:'center'});return true})()",lambda _:GLib.timeout_add(250,self.click_select))

    def point(self, selector, done):
        def move(r):
            page=self.window.page
            self.controls.point(page,(r['x']/page.get_width(),r['y']/page.get_height()))
            GLib.timeout_add(300,done)
        self.controls.evaluate("(() => {const r=document.querySelector("+json.dumps(selector)+").getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()",move)

    def click_select(self):
        self.point(self.selector,self.opened)
        return False

    def opened(self):
        self.controls.evaluate("(() => {const s=document.querySelector("+json.dumps(self.selector)+");return {open:s.matches(':open'),appearance:getComputedStyle(s).appearance,background:getComputedStyle(s,'::picker(select)').backgroundColor,options:[...s.options].map(o=>({value:o.value,height:o.getBoundingClientRect().height}))}})()",self.pick)
        return False

    def pick(self, state):
        self.controls.report.setdefault('selects',[]).append(dict(label=self.label,state=state))
        opaque=state['background'].startswith('rgb(')
        self.controls.report['checks'].append({self.label+' picker is opaque':opaque})
        if not opaque or not state['open'] or state['appearance']!='base-select' or not any(o['height']>0 for o in state['options']):
            self.controls.finish('Settings options are not visible in the page');return
        def select_option(_=None):
            self.point(self.selector+' option[value="'+self.value+'"]',self.chosen)
        if self.phase == 0:
            import os, subprocess, sys
            from pathlib import Path
            output=Path(os.environ['VIOLA_QA_CAPTURE_MARKER']).parent/'settings-picker.png'
            self.controls.report['picker_capture']=str(output)
            self.window.submit(lambda: subprocess.run([sys.executable,
                str(Path(__file__).with_name('capture_private_display.py')),
                '--output',str(output)],check=True,capture_output=True,timeout=15),select_option)
        else:
            select_option()

    def chosen(self):
        self.controls.evaluate("({value:document.querySelector("+json.dumps(self.selector)+").value,settings:D.settings,open:document.querySelector("+json.dumps(self.selector)+").matches(':open')})",self.verify)
        return False

    def verify(self,result):
        # Existing settings change handlers persist through the real service.
        self.controls.report.setdefault('selected_results',[]).append(result)
        passed=(result['value']==self.value and not result['open']
                and str(result['settings'].get(self.setting))==self.value)
        self.controls.report['checks'].append({self.label:passed})
        if not passed:self.controls.finish('Option selection failed');return
        self.phase+=1
        if self.phase<2:self.next_select();return
        parsed=Gtk.accelerator_parse(native_accelerator('Ctrl+,'))
        label=Gtk.accelerator_get_label(parsed[1],parsed[2]) if parsed[0] else ''
        passed=parsed[0] and parsed[1]==Gdk.KEY_comma and bool(label)
        self.controls.report['settings_accelerator_label']=label
        self.controls.report['checks'].append({'settings_shortcut_has_visible_label':passed})
        self.controls.finish(None if passed else 'Settings accelerator has no display label')
