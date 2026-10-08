# SPDX-License-Identifier: GPL-3.0-only
"""Exercise existing navigation/state services in an isolated minimized engine."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import html
from urllib.parse import urlsplit, parse_qs
from pathlib import Path
import tempfile
import threading
import time

from browser_services import BrowserServices
from engine_pipe import EnginePipe


class Fixture(BaseHTTPRequestHandler):
    video_path = None
    def do_GET(self):
        if urlsplit(self.path).path == '/late-icon.svg':
            # Reuse the packaged native globe to test a late favicon update.
            body = (Path(__file__).parent / 'assets/org.projectluma.Viola.NativeIntegration.svg').read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'image/svg+xml')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if urlsplit(self.path).path == '/fixture.webm' and self.video_path:
            body = self.video_path.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'video/webm')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if urlsplit(self.path).path == '/download':
            body = b'Viola native layout download fixture\n'
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Disposition', 'attachment; filename="native-layout-fixture.txt"')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        title = parse_qs(urlsplit(self.path).query).get('title', ['Viola native service fixture'])[0]
        body = ('<!doctype html><title>' + html.escape(title) + '</title>'
                '<input aria-label="Text"><button>Action</button>').encode('utf-8')
        if urlsplit(self.path).path == '/cadence':
            body = b'<!doctype html><title>Viola frame-rate diagnostic</title><style>body{background:#eee;font:18px sans-serif}#box{width:100px;height:100px;background:#357;position:absolute;top:150px}</style><p>Measuring page animation. This test window closes automatically.</p><div id="box"></div><script>let n=0;function tick(){document.querySelector("#box").style.transform="translateX("+(300+250*Math.sin(n++/30))+"px)";requestAnimationFrame(tick)}tick()</script>'
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--engine', type=Path, required=True)
    parser.add_argument('--work-root', type=Path, required=True)
    args = parser.parse_args()
    args.work_root.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    profile = Path(tempfile.mkdtemp(prefix='services-', dir=args.work_root))
    engine = EnginePipe(args.engine, profile, lambda _: None, native_frame_probe=True)
    services = BrowserServices(engine, lambda _: None)
    report = {'classification': 'existing browser services probe, not GTK input or visual acceptance'}
    try:
        target = engine.call('Target.createTarget', {'url': 'about:blank', 'newWindow': True,
                                           'background': True, 'windowState': 'minimized'})['targetId']
        def wait_commit(url):
            # A render-state URL can be a pending navigation. Wait for the
            # real history commit before issuing the next history operation.
            attached = {}
            for _ in range(200):
                # Fixture URLs are unique in this disposable profile. This
                # lookup is test-only, never an input-routing identity scheme.
                targets = engine.call('Target.getTargets')['targetInfos']
                for candidate in targets:
                    if candidate['type'] != 'page' or candidate['url'] != url:
                        continue
                    key = candidate['targetId']
                    if key not in attached:
                        attached[key] = engine.call('Target.attachToTarget', {
                            'targetId': key, 'flatten': True})['sessionId']
                    try:
                        history = engine.call('Page.getNavigationHistory', session=attached[key])
                    except RuntimeError:
                        continue
                    if history['entries'][history['currentIndex']]['url'] == url:
                        return
                time.sleep(.05)
            raise TimeoutError('Navigation did not commit: ' + url)
        services.connect()
        origin = 'http://127.0.0.1:' + str(server.server_port)
        first = origin + '/first'
        second = origin + '/second'
        third = origin + '/third'
        services.navigate(first)
        services.wait_state(lambda state: state.get('activeUrl') == first)
        wait_commit(first)
        first_id = services.state['activeTabId']
        services.navigate(second)
        services.wait_state(lambda state: state.get('activeUrl') == second)
        wait_commit(second)
        report['edit_keeps_tab_identity'] = services.state['activeTabId'] == first_id
        services.send('sidebar', 'nav:back')
        services.wait_state(lambda state: state.get('activeUrl') == first)
        wait_commit(first)
        report['existing_back_command'] = True
        services.send('sidebar', 'nav:forward')
        services.wait_state(lambda state: state.get('activeUrl') == second)
        wait_commit(second)
        report['existing_forward_command'] = True
        services.navigate(third, new_tab=True)
        services.wait_state(lambda state: state.get('activeUrl') == third
                            and state.get('activeTabId') != first_id)
        third_id = services.state['activeTabId']
        report['existing_new_tab_commit'] = True
        services.send('sidebar', 'tab:close', {'tabId': third_id})
        services.wait_state(lambda state: state.get('activeTabId') == first_id)
        report['existing_tab_close'] = True
        report['new_tab_model'] = False
    except Exception as error:
        report['error'] = str(error)
    finally:
        services.close()
        engine.close()
        server.shutdown()
        server.server_close()
    (args.work_root / 'service-navigation-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
