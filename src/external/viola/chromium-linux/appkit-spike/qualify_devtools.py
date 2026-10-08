# SPDX-License-Identifier: GPL-3.0-only
"""Real Chromium Inspect and Developer tools command/window qualification."""
import json
from types import SimpleNamespace
from gi.repository import GLib
from qualify_native_windows import WindowQualification as Base
from qualify_native_controls import ControlQualification
from private_input import PrivateInput


class WindowQualification(Base):
    def __init__(self, manager, report, complete):
        self.manager,self.report,self.complete=manager,report,complete
        self.primary=manager.primary['window']
        self.report['checks']=[]
        self.device=PrivateInput();self.device.start()
        self.wait(lambda:self.primary.page_input.valid_identity(),self.inspect)

    def inspect(self):
        original=self.primary.services.on_native_menu
        def menu(model):
            original(model)
            rows=[r for r in model.get('items',[]) if r.get('label','').replace('&','')=='Inspect']
            if rows:
                GLib.idle_add(self.invoke_inspect,model,rows[0])
        self.primary.services.on_native_menu=menu
        proxy=SimpleNamespace(window=self.primary,report=self.report,device=self.device)
        ControlQualification.point(proxy,self.primary.page,(.35,.35),click=False)
        GLib.timeout_add(200,lambda:self.device.click(273) or False)
        self.wait(lambda:len(self.manager.contexts)==2 and any(c['description'].get('presentation')=='devtools' and c['window'].page_input.valid_identity() for c in self.manager.contexts.values()),self.opened)

    def invoke_inspect(self,model,row):
        self.check('inspect_retained_command',row['enabled'])
        if self.primary.open_popover:
            self.primary.open_popover.invoked=True
            self.primary.open_popover.popdown()
        self.primary.submit(lambda:self.primary.services.activate_menu(model['nonce'],[row['index']]))
        return False

    def opened(self):
        self.dev=next(c['window'] for c in self.manager.contexts.values() if c['description'].get('presentation')=='devtools')
        self.check('inspect_visible_native_window',self.dev.get_mapped())
        self.check('devtools_live_frame_and_identity',self.dev.page_input.valid_identity())
        self.check('devtools_compact_chrome',not self.dev.toolbar.get_visible() and not self.dev.mini_presentation.expand.get_visible())
        self.check('devtools_not_in_workspace','devtools://' not in json.dumps(self.primary.services.state))
        expression="""(async()=>{const SDK=await import('./core/sdk/sdk.js');let target;for(let i=0;i<60;i++){target=SDK.TargetManager.TargetManager.instance().primaryPageTarget();if(target)break;await new Promise(r=>setTimeout(r,100));}const result=await target.runtimeAgent().invoke_evaluate({expression:'6*7',returnByValue:true});return result.result?.value})()"""
        def evaluate():return self.manager.engine.call('Runtime.evaluate',{'expression':expression,'awaitPromise':True,'returnByValue':True},session=self.dev.page_input.session)
        self.dev.submit(evaluate,self.evaluated)

    def evaluated(self,result):
        self.report['frontend_evaluation']=result
        self.check('frontend_connected_to_inspected_page',result.get('result',{}).get('value')==42)
        import os, subprocess, sys
        from pathlib import Path
        output=self.manager.engine.profile.parent / 'devtools-visible.png'
        def capture():
            subprocess.run([sys.executable,str(Path(__file__).with_name('capture_private_display.py')),'--output',str(output)],check=True,timeout=20,stdout=subprocess.DEVNULL)
        def close(_):
            self.report['screenshot']=str(output)
            self.dev.close()
            self.wait(lambda:len(self.manager.contexts)==1,self.menu_open)
        GLib.timeout_add(600,lambda:self.dev.submit(capture,close) and False)

    def menu_open(self):
        self.check('closing_devtools_keeps_browser_alive',self.manager.engine.process.poll() is None and self.primary.get_mapped())
        def command():
            s=self.primary.services
            model=s.open_menu('browser:menu',{'anchorRect':{'x':0,'y':0,'width':28,'height':28}})
            def find(rows,prefix=[]):
                for r in rows:
                    path=prefix+[r['index']]
                    if r.get('label')=='Developer tools':return path
                    result=find(r.get('children',[]),path)
                    if result:return result
            path=find(model['items'])
            if not path:raise RuntimeError('No retained Developer tools command')
            s.activate_menu(model['nonce'],path)
        self.primary.submit(command)
        self.wait(lambda:len(self.manager.contexts)==2 and any(c['description'].get('presentation')=='devtools' and c['window'].page_input.valid_identity() for c in self.manager.contexts.values()),self.menu_ready)

    def menu_ready(self):
        self.check('browser_menu_opens_devtools',True)
        self.finish()

    def finish(self,error=None):
        self.report['completed']=True
        if error:self.report['error']=error
        self.device.close();self.complete()
