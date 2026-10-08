# SPDX-License-Identifier: GPL-3.0-only
"""Check packaged Settings and existing browser capabilities in a fresh profile."""
import argparse
import json
import os
from collections import deque
from types import SimpleNamespace
from pathlib import Path
import tempfile
import time
from browser_services import BrowserServices
from engine_pipe import EnginePipe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', type=Path, required=True)
    args = parser.parse_args()
    errors, checks, dialogs = [], [], deque()
    def event_received(event):
        if event.get("method") == "Runtime.exceptionThrown":
            errors.append(event["params"])
        elif event.get("method") in ("Page.javascriptDialogOpening", "Page.javascriptDialogClosed"):
            dialogs.append(event)
    with tempfile.TemporaryDirectory(prefix='viola-settings-services-') as root:
        engine = EnginePipe(args.engine,Path(root)/'profile',event_received,
                            native_frame_probe=True)
        services = BrowserServices(engine,lambda _:None)
        try:
            def session_for(prefix):
                for _ in range(100):
                    targets = engine.call('Target.getTargets')['targetInfos']
                    target = next((target for target in targets if target['url'].startswith(prefix)),None)
                    if target:
                        session = engine.call('Target.attachToTarget',{'targetId':target['targetId'],'flatten':True})['sessionId']
                        engine.call('Runtime.enable',session=session)
                        return session
                    time.sleep(.05)
                raise AssertionError('Missing target '+prefix)
            def evaluate(session,expression):
                result = engine.call('Runtime.evaluate',{'expression':expression,'returnByValue':True,'awaitPromise':True},session=session)
                if result.get('exceptionDetails'):
                    raise AssertionError(result['exceptionDetails'])
                return result.get('result',{}).get('value')
            def wait(session,expression):
                for _ in range(100):
                    if evaluate(session,expression):return
                    time.sleep(.05)
                raise AssertionError('Timed out '+expression)
            engine.call('Target.createTarget',{'url':'chrome://viola/settings.html'})
            session = session_for('chrome://viola/settings.html')
            wait(session,'typeof D !== "undefined" && !!D && !!document.querySelector("h1")')
            services.connect()
            for name in evaluate(session,'activeSections()'):
                evaluate(session,'section='+json.dumps(name)+';render();true')
                time.sleep(.15)
                result = evaluate(session,'({heading:document.querySelector("h1")?.textContent,error:document.body.innerText.includes("Could not load this section")})')
                assert result['heading'] and not result['error'],(name,result)
            checks.append('All packaged settings sections render with actual Chromium service data')
            evaluate(session,"vela.send('settings:set',{key:'tabSpacing',value:6});true")
            services.wait_state(lambda state: state.get('settings',{}).get('tabSpacing') == 6)
            data = evaluate(session,"vela.invoke('settings:data')")
            assert data['settings']['tabSpacing'] == 6
            checks.append('Appearance setting persists and is published to the native sidebar')
            # Exercise a real renderer confirmation through the actual Adw
            # bridge, in the disposable compositor/profile only.
            os.environ['WAYLAND_DISPLAY'] = engine.display.display
            import gi
            gi.require_version('Gtk','4.0')
            from gi.repository import Gtk, GLib
            from native_dialogs import NativeDialogs
            window = Gtk.Window()
            window.page_input = SimpleNamespace(session=session,engine=engine)
            bridge = NativeDialogs(window)
            window.present()
            engine.call('Page.enable',session=session)
            def pump():
                while dialogs:bridge.event(dialogs.popleft())
                context = GLib.MainContext.default()
                while context.pending():context.iteration(False)
            for response, expected in [('accept',True),('cancel',False)]:
                future = engine.request('Runtime.evaluate',{'expression':'confirm("Settings qualification")','returnByValue':True},session=session)
                deadline = time.monotonic()+10
                while bridge.dialog is None and time.monotonic()<deadline:
                    pump();time.sleep(.01)
                assert bridge.dialog is not None,'Real confirmation did not reach native dialog'
                bridge.dialog.emit('response',response)
                assert future.result(5)['result']['value'] is expected
                pump()
            bridge.dismiss(answer=False)
            window.destroy()
            checks.append('Real Chromium confirmations accept and cancel through the native AppKit dialog')
            old = services.state['activeTabId']
            # The command may replace its own WebUI, so its evaluator reply is
            # deliberately not a prerequisite for discovering the destination.
            engine.request('Runtime.evaluate',{'expression':"vela.send('browser:openPage',{page:'sitePermissions'});true"},session=session)
            target = session_for('chrome://settings/content')
            wait(target,'document.documentElement.dataset.violaThemed === "true"')
            assert evaluate(target,'!!document.querySelector("#viola-luma-browser-tool-theme")')
            assert evaluate(target,'!document.querySelector("#viola-engine-settings-rail")')
            services.wait_state(lambda state: state.get('activeUrl','').startswith('chrome://settings/content'))
            assert services.state['activeTabId'] == old
            checks.append('Permissions uses the real Chromium page, themed without replacing the tab or hiding navigation')
            assert not errors, errors
        finally:
            services.close()
            engine.close()
    report = {'checks':checks,'runtime_errors':errors,'exit_code':engine.process.returncode}
    print(json.dumps(report,indent=2))


if __name__ == '__main__':
    main()
