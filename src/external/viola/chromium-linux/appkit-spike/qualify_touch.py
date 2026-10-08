# SPDX-License-Identifier: GPL-3.0-only
"""Virtual touchscreen → private Mutter → GTK → real Chromium page checks."""
from gi.repository import GLib, Gtk
from private_input import PrivateInput
from qualify_native_windows import WindowQualification


class TouchQualification(WindowQualification):
    def __init__(self, manager, report, complete):
        self.manager, self.report, self.complete = manager, report, complete
        self.report['checks'] = []
        self.primary = manager.primary['window']
        self.device = PrivateInput()
        self.device.start(touch=True)
        self.wait(lambda: self.primary.page_input.valid_identity() and self.device.touch_node is not None, self.setup)

    def evaluate(self, source, done):
        def work():
            response = self.manager.engine.call('Runtime.evaluate',
                {'expression': source, 'returnByValue': True}, session=self.primary.page_input.session)
            if response.get('exceptionDetails'):
                raise AssertionError(response['exceptionDetails'])
            return response['result'].get('value')
        self.primary.submit(work, done)

    def setup(self):
        self.primary.present();self.primary.page.grab_focus()
        # The first notification creates Mutter's virtual touchscreen. Allow
        # GTK to bind the new Wayland seat capability before the real gesture.
        self.device.touch('Down',9,0,0)
        self.device.touch('Up',9)
        self.evaluate('''document.body.innerHTML='<button id="tap" style="position:fixed;left:20px;top:20px;width:140px;height:60px">Touch target</button><div style="height:4000px"></div>';document.body.style.touchAction='auto';window.tapCount=0;window.touchProof=[];document.getElementById('tap').onclick=()=>window.tapCount++;document.addEventListener('touchstart',e=>{const t=e.touches[0];window.lastTouch={x:t.clientX,y:t.clientY,target:e.target.id}});for(const k of ['touchstart','touchmove','touchend','touchcancel'])document.addEventListener(k,e=>window.touchProof.push([k,e.touches.length]),{passive:true});window.scrollTo(0,0);true''',
            lambda _: GLib.timeout_add(400, lambda: self.swipe(1, self.one_ready) or False))

    def point(self, x, y):
        w=self.primary
        ok, bounds=w.page.compute_bounds(w)
        if not ok: raise RuntimeError('Page not allocated')
        return ((1600-w.get_width())/2+bounds.get_x()+x,
                (1000-w.get_height())/2+bounds.get_y()+y)

    def swipe(self, fingers, done):
        self.fingers=fingers
        w=self.primary
        x,y=w.page.get_width()*.72,w.page.get_height()*.72
        for slot in range(fingers):
            self.device.touch('Down',slot,*self.point(x+slot*60,y))
        step=[0]
        def move():
            step[0]+=1
            for slot in range(fingers):
                self.device.touch('Motion',slot,*self.point(x+slot*60,y-step[0]*10))
            if step[0] == 20:
                for slot in range(fingers):self.device.touch('Up',slot)
                GLib.timeout_add(700,lambda: self.evaluate(
                    '({scroll:window.scrollY,events:window.touchProof,clicks:window.tapCount})',done) or False)
                return False
            return True
        GLib.timeout_add(25,move)

    def one_ready(self, result):
        self.report['one_finger']=result
        self.report['native_touch_metrics']=dict(self.primary.page_input.metrics)
        self.report['contacts']=len(self.primary.page_input.touch.contacts)
        self.report['ignored_contacts']=len(self.primary.page_input.touch.ignored)
        self.check('native_touch_contacts_reach_chromium',self.primary.page_input.metrics.get('touch_events',0)>10)
        self.check('one_finger_scrolls_page',result['scroll']>100)
        self.check('scroll_does_not_synthesize_mouse_click',result['clicks']==0)
        self.evaluate('window.scrollTo(0,0);window.touchProof=[];true',
            lambda _: GLib.timeout_add(400,lambda: self.swipe(2,self.two_ready) or False))

    def two_ready(self, result):
        self.report['two_finger']=result
        self.check('two_fingers_scroll_page',result['scroll']>80)
        self.check('all_contacts_released',not self.primary.page_input.touch.contacts and
                   any(e==['touchend',0] for e in result['events']))
        self.evaluate('window.scrollTo(0,0);true',lambda _:GLib.timeout_add(400,self.tap))

    def tap(self):
        self.device.touch('Down',0,*self.point(90,50))
        GLib.timeout_add(80,lambda:self.device.touch('Up',0) or False)
        GLib.timeout_add(450,lambda:self.evaluate('({count:window.tapCount,lastTouch:window.lastTouch})',self.tapped) or False)
        return False

    def tapped(self, result):
        self.report['tap']=result
        self.report['surface_transform']=list(self.primary.get_surface_transform())
        self.check('tap_clicks_once_at_correct_page_coordinates',result['count']==1)
        self.evaluate("document.body.style.touchAction='none';window.scrollTo(0,0);true",
            lambda _:GLib.timeout_add(400,lambda:self.swipe(1,self.none_ready) or False))

    def none_ready(self, result):
        self.check('chromium_respects_site_touch_action_none',result['scroll']==0)
        self.wait(lambda:self.primary.page_input.pending==0,self.finished)

    def finished(self):
        self.check('input_queue_and_contacts_settle',self.primary.page_input.touch.inflight==0 and
                   not self.primary.page_input.touch.contacts)
        self.finish()
