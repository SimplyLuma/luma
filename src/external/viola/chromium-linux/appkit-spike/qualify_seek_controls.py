# SPDX-License-Identifier: GPL-3.0-only
"""Physical seek-bar clicks and captured drags, including a shadow-DOM control."""
from gi.repository import GLib


class SeekQualification:
    def __init__(self, controls):
        self.controls = controls
        controls.evaluate("""(() => {
          document.body.innerHTML='<div id="host" style="position:absolute;left:80px;top:160px;width:500px;height:100px"></div>';
          const root=host.attachShadow({mode:'open'});
          root.innerHTML='<div id="seek" role="slider" style="height:50px;width:500px;background:#456;touch-action:none;user-select:none"></div>';
          const bar=root.querySelector('#seek');
          window.seekTest={values:[],events:[],errors:[]};
          const position=e=>(e.clientX-bar.getBoundingClientRect().left)/500;
          for(const type of ['pointerdown','pointermove','pointerup','gotpointercapture','lostpointercapture','click'])bar.addEventListener(type,e=>seekTest.events.push({type:e.type,button:e.button,buttons:e.buttons,x:e.clientX,id:e.pointerId}));
          bar.onpointerdown=e=>{try{bar.setPointerCapture(e.pointerId)}catch(error){seekTest.errors.push(String(error))}};
          bar.onpointerup=e=>{if(bar.hasPointerCapture(e.pointerId)){seekTest.values.push(position(e));bar.releasePointerCapture(e.pointerId)}};
          return true;
        })()""", lambda _: GLib.timeout_add(250, self.click))

    def point(self,x):
        page=self.controls.window.page
        self.controls.point(page,(x/page.get_width(),185/page.get_height()),click=False)

    def click(self):
        self.point(430)
        GLib.timeout_add(150,lambda:self.controls.device.click() or False)
        GLib.timeout_add(450,lambda:self.controls.evaluate('seekTest',self.clicked) or False)
        return False

    def clicked(self,result):
        self.controls.report['seek_click']=result
        passed=len(result['values'])==1 and abs(result['values'][0]-.7)<.05
        self.controls.report['checks'].append({'captured_seek_click':passed})
        self.point(180)
        d=self.controls.device
        def button(pressed):d.call(d.session,d.service+'.Session','NotifyPointerButton',GLib.Variant('(ib)',(272,pressed)))
        def motion():d.call(d.session,d.service+'.Session','NotifyPointerMotionRelative',GLib.Variant('(dd)',(250.,0.)))
        GLib.timeout_add(150,lambda:button(True) or False)
        GLib.timeout_add(300,lambda:motion() or False)
        GLib.timeout_add(450,lambda:button(False) or False)
        GLib.timeout_add(750,lambda:self.controls.evaluate('seekTest',self.dragged) or False)

    def dragged(self,result):
        self.controls.report['seek_drag']=result
        passed=len(result['values'])==2 and abs(result['values'][-1]-.7)<.05
        self.controls.report['checks'].append({'captured_seek_drag':passed})
        if not passed:
            self.controls.finish('Captured seek did not commit')
            return
        page=self.controls.window.page
        self.controls.point(page,(20/page.get_width(),40/page.get_height()),click=False)
        d=self.controls.device
        def right(pressed):d.call(d.session,d.service+'.Session','NotifyPointerButton',GLib.Variant('(ib)',(273,pressed)))
        def popup():
            self.controls.report['context_was_mapped']=bool(self.controls.window.open_popover and self.controls.window.open_popover.get_mapped())
            right(False)
            return False
        GLib.timeout_add(150,lambda:right(True) or False)
        GLib.timeout_add(500,popup)
        GLib.timeout_add(650,lambda:d.key(0xff1b) or False)
        GLib.timeout_add(900,self.after_context)

    def after_context(self):
        self.controls.report['buttons_after_context']=self.controls.window.page_input.buttons
        self.controls.report['checks'].append({'popup_releases_page_buttons':self.controls.window.page_input.buttons==0 and self.controls.report.get('context_was_mapped')})
        self.point(430)
        GLib.timeout_add(150,lambda:self.controls.device.click() or False)
        GLib.timeout_add(450,lambda:self.controls.evaluate('seekTest',self.final) or False)
        return False

    def final(self,result):
        self.controls.report['seek_after_context']=result
        passed=len(result['values'])==3 and abs(result['values'][-1]-.7)<.05
        self.controls.report['checks'].append({'captured_seek_after_context':passed})
        self.controls.finish(None if passed and self.controls.window.page_input.buttons==0 else 'Context menu left seek input stuck')
