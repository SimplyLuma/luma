# SPDX-License-Identifier: GPL-3.0-only
"""Exercise real native New Tab identities on an isolated builder compositor."""
import argparse
import json
from pathlib import Path
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from browser_services import BrowserServices
from engine_pipe import EnginePipe


class Fixture(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(b'<title>New tab destination</title><p>Local destination</p>')

    def log_message(self, *args):
        pass


def tabs(state):
    return {tab['id']: tab for section in ('favorites', 'pinned', 'today')
            for tab in state.get(section, [])}


def qualify(executable):
    report = {}
    http = ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix='viola-new-tabs-') as root:
            engine = EnginePipe(executable, Path(root) / 'profile', lambda _: None,
                                native_frame_probe=True)
            services = BrowserServices(engine, lambda _: None)
            try:
                engine.call('Target.createTarget', {'url': 'about:blank', 'newWindow': True})
                services.connect()
                initial = set(tabs(services.state))
                for index in range(3):
                    state = services.create_new_tab()
                    identity = state['activeTabId']
                    visible = tabs(state)
                    assert set(visible) - initial == {identity}, 'New Tab must add exactly one sidebar identity'
                    assert visible[identity]['title'] == 'New Tab', visible[identity]
                    assert state['activeUrl'] == 'about:blank'
                    url = f'http://127.0.0.1:{http.server_port}/?tab={index}'
                    services.navigate(url)
                    state = services.wait_state(lambda s: s.get('activeUrl') == url)
                    assert state['activeTabId'] == identity, 'Navigation inserted another tab'
                    assert set(tabs(state)) == set(visible), 'Navigation left an extra tab'
                    initial = set(visible)
                report['three_visible_new_tabs_and_same_identity_navigation'] = True
                url = f'http://127.0.0.1:{http.server_port}/?programmatic=1'
                services.navigate(url, new_tab=True)
                state = services.wait_state(lambda s: s.get('activeUrl') == url)
                assert set(tabs(state)) - initial == {state['activeTabId']}
                report['programmatic_navigation_adds_exactly_one_tab'] = True
                report['gpu_crashes'] = engine.call('SystemInfo.getInfo')['gpu']['auxAttributes'].get('processCrashCount')
            finally:
                services.close()
                engine.close()
                report['exit_code'] = engine.process.returncode
    finally:
        http.shutdown()
        http.server_close()
    assert report['exit_code'] == 0 and report['gpu_crashes'] == 0, report
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', type=Path, required=True)
    print(json.dumps(qualify(parser.parse_args().engine.resolve()), indent=2))
