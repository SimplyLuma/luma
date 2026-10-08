# SPDX-License-Identifier: GPL-3.0-only
"""Exercise real GTK menus against retained Chromium delegates, without popup UI."""
import argparse
import json
from pathlib import Path
import tempfile
import time
import threading
from http.server import ThreadingHTTPServer
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk
from luma_appkit import install_appkit
from browser_services import BrowserServices
from engine_pipe import EnginePipe
from native_menu import NativeMenu
from measure_service_navigation import Fixture


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--engine', type=Path, required=True)
    parser.add_argument('--work-root', type=Path, required=True)
    args = parser.parse_args()
    args.work_root.mkdir(parents=True, exist_ok=True)
    Gtk.init()
    install_appkit()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    profile = Path(tempfile.mkdtemp(prefix='native-menus-', dir=args.work_root))
    engine = EnginePipe(args.engine, profile, lambda _: None,
                        native_frame_probe=True, native_menu_probe=True)
    services = BrowserServices(engine, lambda _: None)
    report = {'classification': 'unpresented GTK menu/delegate probe, not visual acceptance'}
    popovers = []
    try:
        engine.call('Target.createTarget', {'url': 'about:blank', 'newWindow': True,
                                           'background': True, 'windowState': 'minimized'})
        services.connect()
        url = 'http://127.0.0.1:' + str(server.server_port) + '/menus'
        services.navigate(url)
        services.wait_state(lambda state: state.get('activeUrl') == url and any(
            tab.get('title') == 'Viola native service fixture'
            and tab['id'] == state['activeTabId'] for tab in state['today']))
        anchor = {'anchorRect': {'x': 10, 'y': 10, 'width': 28, 'height': 28}}
        for channel in ('app:menu', 'browser:menu'):
            description = services.open_menu(channel, anchor)
            menu = NativeMenu(description, services.activate_menu)
            popovers.append(menu)
            report[channel] = {'gtk_popover_menu': isinstance(menu, Gtk.PopoverMenu),
                               'retained_items': len(menu.paths),
                               'top_level_sections': menu.get_menu_model().get_n_items()}
        tab_id = services.state['activeTabId']
        payload = {'tabId': tab_id, 'section': 'today'}

        def muted(state):
            return next(tab['muted'] for tab in state['today'] if tab['id'] == tab_id)

        before = muted(services.state)
        description = services.open_menu('tab:menu', payload)
        menu = NativeMenu(description, services.activate_menu)
        popovers.append(menu)
        item = next(row for row in description['items'] if row['label'] == 'Mute tab')
        report['mute_item'] = item
        path = (item['index'],)
        menu.actions.lookup_action(menu.paths[path]).activate(None)
        report['action_sent'] = True
        services.wait_state(lambda state: muted(state) != before)
        report['gtk_action_invoked_original_mute_delegate'] = True
        services.activate_menu(description['nonce'], list(path))
        time.sleep(.15)
        report['replayed_action_ignored'] = muted(services.state) != before
        fresh = services.open_menu('tab:menu', payload)
        services.activate_menu(fresh['nonce'], [-1])
        fresh_menu = NativeMenu(fresh, services.activate_menu)
        popovers.append(fresh_menu)
        fresh_item = next(row for row in fresh['items'] if row['label'] == 'Mute tab')
        fresh_path = (fresh_item['index'],)
        fresh_menu.actions.lookup_action(fresh_menu.paths[fresh_path]).activate(None)
        services.wait_state(lambda state: muted(state) == before)
        report['invalid_path_rejected_and_valid_action_restores_state'] = True
        report['application_stylesheets'] = []
        report['popup_presented'] = False
    except Exception as error:
        report['error'] = str(error)
    finally:
        popovers.clear()
        services.close()
        engine.close()
        server.shutdown()
        server.server_close()
    (args.work_root / 'native-menu-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
