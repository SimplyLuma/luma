# SPDX-License-Identifier: GPL-3.0-only
"""Render and exercise settings with disposable services on the build server."""
import argparse
import base64
import json
from pathlib import Path
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from engine_pipe import EnginePipe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', type=Path, required=True)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    class Fixture(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(args.repo / 'src/ui'), **kw)
        def do_GET(self):
            if self.path == '/host-bootstrap.js':
                self.send_response(200)
                self.send_header('Content-Type','application/javascript')
                self.end_headers()
                self.wfile.write((args.repo / 'test/fixtures/luma-settings-host.js').read_bytes())
            else:
                super().do_GET()
        def log_message(self, *a):
            pass
    server = ThreadingHTTPServer(('127.0.0.1',0),Fixture)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    errors, checks = [], []
    with tempfile.TemporaryDirectory(prefix='viola-settings-') as directory:
        engine = EnginePipe(args.engine,Path(directory)/'profile',
                            lambda event: errors.append(event['params']) if event.get('method') == 'Runtime.exceptionThrown' else None,
                            native_frame_probe=True)
        try:
            target = engine.call('Target.createTarget',{'url':f'http://127.0.0.1:{server.server_port}/settings.html'})['targetId']
            session = engine.call('Target.attachToTarget',{'targetId':target,'flatten':True})['sessionId']
            engine.call('Runtime.enable',session=session)
            def evaluate(expression):
                result = engine.call('Runtime.evaluate',{'expression':expression,'returnByValue':True,'awaitPromise':True},session=session)
                if result.get('exceptionDetails'):
                    raise AssertionError(result['exceptionDetails'])
                return result.get('result',{}).get('value')
            def wait(expression):
                for _ in range(100):
                    if evaluate(expression):return
                    time.sleep(.05)
                raise AssertionError('Timed out: '+expression)
            wait('!!document.querySelector("h1")')
            sections = evaluate('activeSections()')
            for width, dark in [(1180,False),(1180,True),(640,False)]:
                engine.call('Emulation.setDeviceMetricsOverride',{'width':width,'height':820,'deviceScaleFactor':1,'mobile':False},session=session)
                engine.call('Emulation.setEmulatedMedia',{'features':[{'name':'prefers-color-scheme','value':'dark' if dark else 'light'}]},session=session)
                for name in sections:
                    evaluate('section='+json.dumps(name)+';render();true')
                    time.sleep(.12)
                    state = evaluate('({heading:document.querySelector("h1")?.textContent,error:document.body.innerText.includes("Could not load this section"),overflow:document.documentElement.scrollWidth>innerWidth||document.querySelector("main").scrollWidth>document.querySelector("main").clientWidth+1})')
                    assert state['heading'] and not state['error'] and not state['overflow'], (name,width,dark,state)
                    if dark:
                        assert evaluate('document.body.classList.contains("dark") && (!document.querySelector(".card") || getComputedStyle(document.querySelector(".card")).backgroundColor !== "rgb(255, 255, 255)")'), 'Dark palette retained a light card'
                    filename = f'{width}-{"dark" if dark else "light"}-{name.replace(" & ","-").replace(" ","-")}.png'
                    png = engine.call('Page.captureScreenshot',{'format':'png'},session=session)['data']
                    (args.output/filename).write_bytes(base64.b64decode(png))
                checks.append(f'All {len(sections)} sections: {width}px, dark={dark}, no overflow or render errors')
            evaluate("section='Account & Sync';render();true")
            evaluate("document.querySelector('[data-field=email]').value='qa@example.test';document.querySelector('[data-field=password]').value='fixture-password';document.querySelector('[data-field=password]').focus();true")
            engine.call('Input.dispatchKeyEvent',{'type':'keyDown','key':'Enter','code':'Enter','windowsVirtualKeyCode':13},session=session)
            engine.call('Input.dispatchKeyEvent',{'type':'keyUp','key':'Enter','code':'Enter','windowsVirtualKeyCode':13},session=session)
            wait("fixtureCalls.some(c=>c.name==='account:emailStart')")
            assert evaluate("fixtureCalls.filter(c=>c.name==='account:emailStart').length===1 && fixtureCalls.find(c=>c.name==='account:emailStart').payload.intent==='signin'"), 'Enter must only sign in'
            assert evaluate("document.querySelector('[data-field=password]').value==='fixture-password' && !document.querySelector('[data-action=create]').disabled"), 'Failed sign-in must preserve inputs and allow retry'
            checks.append('Physical Enter submits Sign in once; failed sign-in preserves inputs and re-enables retry')
            evaluate("document.querySelector('[data-action=forgot]').click();true")
            assert evaluate("emailSignup.mode==='reset' && fixtureCalls.filter(c=>c.name==='account:emailStart').length===1"), 'Forgot password must not reset immediately'
            checks.append('Forgot password selects a separate reset flow without invoking reset')
            evaluate("section='Downloads';render();true")
            wait("!!document.querySelector('[data-download-id]')")
            evaluate("globalThis.savedRow=document.querySelector('[data-download-id]');fixtureLibrary.downloads[0].received+=1000;fixtureEmit('download:update',{downloads:fixtureLibrary.downloads});true")
            assert evaluate("savedRow===document.querySelector('[data-download-id]')"), 'Progress must preserve row widgets'
            checks.append('Download progress preserves the existing DOM row')
            evaluate("section='History';render();true")
            wait("!!document.querySelector('.library-search')")
            evaluate("let input=document.querySelector('.library-search');input.focus();input.value='guide';input.setSelectionRange(2,2);input.dispatchEvent(new Event('input'));true")
            time.sleep(.25)
            assert evaluate("document.activeElement.classList.contains('library-search') && document.activeElement.selectionStart===2"), 'Search caret moved'
            checks.append('Library search preserves focus and caret')
            evaluate("globalThis.testHost=document.createElement('div');document.body.append(testHost);globalThis.testRoot=testHost.attachShadow({mode:'open'});let control=document.createElement('button');control.textContent='Use Google Translate';control.onclick=()=>globalThis.controlClicked=true;testRoot.append(control);let help=document.createElement('a');help.href='https://support.example.test/';help.textContent='Help';testRoot.append(help);true")
            evaluate((args.repo/'src/ui/browser-tools-luma.js').read_text())
            assert evaluate("getComputedStyle(testRoot.querySelector('button')).display!=='none' && getComputedStyle(testRoot.querySelector('a')).display!=='none'"), 'Advanced tool controls were hidden'
            evaluate("testRoot.querySelector('button').click();true")
            assert evaluate('globalThis.controlClicked === true'), 'Advanced control handler replaced'
            checks.append('Advanced tool theme retains shadow-root controls, help links and handlers')
            assert not errors, errors
        finally:
            engine.close()
            server.shutdown()
            server.server_close()
    report = {'checks':checks,'runtime_errors':errors,'exit_code':engine.process.returncode}
    (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__ == '__main__':
    main()
