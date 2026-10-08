# SPDX-License-Identifier: GPL-3.0-only
"""Compositor checks for form submission and pointer-driven page controls."""
from gi.repository import GLib
from pathlib import Path
import os


class PageControlQualification:
    def __init__(self, controls, complete):
        self.controls, self.complete = controls, complete
        self.copy_phase = 'normal' 
        controls.evaluate("""(() => {
          document.body.innerHTML = '<form id="form" style="position:absolute;left:20px;top:20px"><input id="input" value="message"><button>Send</button></form><button id="dropdown" style="position:absolute;left:20px;top:100px;width:160px;height:40px">Dropdown</button><button id="pill" style="position:absolute;right:20px;top:20px;width:120px;height:40px">Notifications</button>';
          window._pageControls={submits:0,dropdown:0,pill:0,events:[]};
          document.querySelector('#form').onsubmit=e=>{e.preventDefault();_pageControls.submits++};
          document.querySelector('#dropdown').onpointerdown=e=>{
            if(e.button===0&&!e.ctrlKey){e.preventDefault();_pageControls.dropdown++}
          };
          document.querySelector('#pill').onclick=()=>_pageControls.pill++;
          for(const type of ['pointerdown','pointerup','mousedown','mouseup','click','keydown','keypress','keyup'])
            document.addEventListener(type,e=>_pageControls.events.push({type,id:e.target.id,key:e.key,code:e.code,button:e.button,buttons:e.buttons,ctrl:e.ctrlKey,repeat:e.repeat,composing:e.isComposing,defaultPrevented:e.defaultPrevented}));
          return true;
        })()""", lambda _: GLib.timeout_add(200, self.focus_input))

    def point(self, x, y):
        page = self.controls.window.page
        self.controls.point(page, (x/page.get_width(), y/page.get_height()))

    def focus_input(self):
        self.point(50, 30)
        GLib.timeout_add(200, self.enter)
        return False

    def enter(self):
        self.controls.device.key(0xff0d)
        GLib.timeout_add(200, self.prevented_enter)
        return False

    def prevented_enter(self):
        self.controls.evaluate("""(() => {
          document.querySelector('#input').onkeydown=e=>{if(e.key==='Enter')e.preventDefault()};
          return true;
        })()""", lambda _: self.controls.device.key(0xff0d))
        GLib.timeout_add(250, self.textarea_setup)
        return False

    def textarea_setup(self):
        self.controls.evaluate("""(() => {
          const area=document.createElement('textarea');area.id='textarea';
          area.style='position:absolute;left:20px;top:180px;width:300px;height:80px';
          document.body.append(area);return true;
        })()""", lambda _: GLib.timeout_add(100, self.textarea_focus))
        return False

    def textarea_focus(self):
        self.point(50,200)
        GLib.timeout_add(150, self.textarea_enter)
        return False

    def textarea_enter(self):
        self.controls.device.key(0xff0d)
        GLib.timeout_add(200, self.dropdown)
        return False

    def dropdown(self):
        self.point(80, 120)
        GLib.timeout_add(250, self.pill)
        return False

    def pill(self):
        self.point(self.controls.window.page.get_width()-70, 40)
        GLib.timeout_add(250, self.inspect)
        return False

    def inspect(self):
        self.controls.evaluate("({..._pageControls,textarea:document.querySelector('#textarea').value})", self.result)
        return False

    def result(self, result):
        self.controls.report['page_controls'] = result
        checks = dict(page_enter_submits_form=result['submits']==1,
                      page_enter_adds_textarea_newline=result['textarea']=='\n',
                      page_pointerdown_opens_dropdown=result['dropdown']==1,
                      page_click_activates_notification=result['pill']==1)
        self.controls.report['checks'].extend({k:v} for k,v in checks.items())
        self.initial_checks = checks
        bundle = os.environ.get('VIOLA_QA_REACT_BUNDLE')
        if bundle:
            self.controls.evaluate(Path(bundle).read_text() + ';true',
                lambda _: GLib.timeout_add(300, self.radix_dropdown))
        else:
            self.copy_setup()

    def radix_dropdown(self):
        self.point(80, 120)
        GLib.timeout_add(300, lambda: self.controls.evaluate(
            "({state:_radix,menu:!!document.querySelector('[role=menu]')})", self.radix_opened) or False)
        return False

    def radix_opened(self, result):
        self.controls.report['radix_dropdown'] = result
        self.initial_checks['radix_pointer_opens_dropdown'] = result['state']['dropdown'] == 1 and result['menu']
        self.controls.device.key(0xff1b)
        GLib.timeout_add(250, self.radix_pill)

    def radix_pill(self):
        self.point(self.controls.window.page.get_width()-70, 40)
        GLib.timeout_add(300, lambda: self.controls.evaluate(
            "({state:_radix,popover:!!document.querySelector('[role=dialog]')})", self.radix_done) or False)
        return False

    def radix_done(self, result):
        self.controls.report['radix_notification'] = result
        self.initial_checks['radix_pointer_opens_notification'] = result['state']['popover'] == 1 and result['popover']
        self.controls.device.key(0xff1b)
        GLib.timeout_add(250, self.copy_setup)

    def copy_setup(self):
        self.controls.evaluate("""(() => {
          window._radixRoot?.unmount();
          document.body.innerHTML='<input id="copy" value="Viola clipboard fixture" style="position:absolute;left:20px;top:20px;width:300px">';
          window._copyEvents=0;document.querySelector('#copy').oncopy=()=>_copyEvents++;
          return true;
        })()""", lambda _: GLib.timeout_add(200, self.copy_focus))
        return False

    def copy_focus(self):
        self.point(50,30)
        GLib.timeout_add(200, self.copy_select)
        return False

    def copy_select(self):
        self.controls.device.chord(0xffe3, ord('a'))
        GLib.timeout_add(200, self.copy_key)
        return False

    def copy_key(self):
        self.controls.device.chord(0xffe3, ord('c'))
        GLib.timeout_add(350, self.read_clipboard)
        return False

    def read_clipboard(self):
        self.controls.evaluate("({events:window._copyEvents,field:!!document.querySelector('#copy'),start:document.querySelector('#copy')?.selectionStart,end:document.querySelector('#copy')?.selectionEnd,focus:document.activeElement?.tagName})",
            self.read_clipboard_after_dom)
        return False

    def read_clipboard_after_dom(self, result):
        self.controls.report['copy_dom'] = result
        clipboard = self.controls.window.get_display().get_clipboard()
        clipboard.read_text_async(None, self.clipboard_result)
        return False

    def clipboard_result(self, clipboard, result):
        try:
            text = clipboard.read_text_finish(result)
        except Exception as error:
            text = None
            self.controls.report['clipboard_error'] = str(error)
        expected = 'Viola clipboard fixture' if self.copy_phase == 'normal' else 'Custom café 🌒'
        passed = text == expected
        key = 'page_ctrl_c_system_clipboard' if self.copy_phase == 'normal' else 'page_copy_handler_preserves_unicode'
        self.controls.report[key] = passed
        self.initial_checks[key] = passed
        if passed and self.copy_phase == 'normal':
            self.copy_phase = 'custom'
            self.controls.evaluate("""(() => {
              document.querySelector('#copy').oncopy=e=>{
                e.preventDefault();
                e.clipboardData.setData('text/plain','Custom café 🌒');
                e.clipboardData.setData('text/html','<b>Custom café 🌒</b>');
              };return true;
            })()""", lambda _: GLib.timeout_add(100, self.copy_key))
        elif passed:
            clipboard.read_async(['text/html'], GLib.PRIORITY_DEFAULT, None, self.read_html)
        else:
            self.finished()

    def read_html(self, clipboard, result):
        try:
            stream, mime = clipboard.read_finish(result)
            stream.read_bytes_async(4096, GLib.PRIORITY_DEFAULT, None, self.html_result)
        except Exception as error:
            self.controls.report['clipboard_html_error'] = str(error)
            self.initial_checks['page_copy_handler_preserves_html'] = False
            self.finished()

    def html_result(self, stream, result):
        try:
            data = stream.read_bytes_finish(result).get_data()
            self.initial_checks['page_copy_handler_preserves_html'] = data.decode() == '<b>Custom café 🌒</b>'
        finally:
            stream.close(None)
        self.finished()

    def finished(self):
        self.controls.report['checks'].extend({k:v} for k,v in self.initial_checks.items()
                                             if k not in ('page_enter_submits_form','page_enter_adds_textarea_newline','page_pointerdown_opens_dropdown','page_click_activates_notification'))
        if not all(self.initial_checks.values()):
            self.controls.finish('Page control activation failed')
        else:
            self.complete()
