# SPDX-License-Identifier: GPL-3.0-only
"""Physical mouse Back/Forward over page content and native browser chrome."""
from gi.repository import GLib, Gtk


class MouseNavigationQualification:
    def __init__(self, controls):
        self.controls=controls
        self.phase=0
        self.steps=[(275,1,'page_back'),(276,2,'page_forward'),(275,1,'chrome_back'),(276,2,'chrome_forward'),(275,1,'repeat_back'),(275,0,'second_back'),(276,1,'first_forward'),(276,2,'second_forward')]
        observer=Gtk.GestureClick(button=0)
        observer.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        observer.connect('pressed',lambda gesture,*_:controls.report.setdefault('physical_buttons',[]).append(gesture.get_current_button()))
        controls.window.add_controller(observer)
        # Trusted interaction is required: Chromium intentionally skips history
        # entries inserted without user activation when its Back command runs.
        adapter=controls.window.page_input
        expression="(()=>{history.replaceState({},'', '?step=0');history.pushState({},'', '?step=1');history.pushState({},'', '?step=2');return location.search})()"
        controls.window.submit(lambda:adapter.engine.call('Runtime.evaluate',
            {'expression':expression,'userGesture':True,'returnByValue':True},session=adapter.session),
            lambda _:GLib.timeout_add(250,self.next))

    def next(self):
        if self.phase==len(self.steps):
            self.controls.report['checks'].append({'history_buttons_not_left_held':self.controls.window.page_input.buttons==0})
            passed=all(all(check.values()) for check in self.controls.report['checks'])
            self.controls.finish(None if passed else 'Mouse navigation did not match history')
            return False
        button,expected,name=self.steps[self.phase]
        widget=self.controls.window.address if name.startswith('chrome_') else self.controls.window.page
        self.controls.point(widget,(.5,.5),click=False)
        GLib.timeout_add(150,lambda:self.controls.device.click(button) or False)
        GLib.timeout_add(650,lambda:self.controls.evaluate('location.search',self.checked) or False)
        return False

    def checked(self,value):
        button,expected,name=self.steps[self.phase]
        self.controls.report['checks'].append({name:value=='?step='+str(expected)})
        self.controls.report.setdefault('history_results',[]).append(value)
        self.phase+=1
        GLib.timeout_add(100,self.next)
