# SPDX-License-Identifier: GPL-3.0-only
"""Check authoritative workspace ordering, bounds, and persistence in isolation."""
import argparse
import json
from pathlib import Path
import tempfile
import time
from browser_services import BrowserServices
from engine_pipe import EnginePipe


def qualify(executable):
    with tempfile.TemporaryDirectory(prefix='viola-workspace-order-') as root:
        profile = Path(root) / 'profile'
        expected = None
        for reopening in (False, True):
            engine = EnginePipe(executable, profile, lambda _: None, reuse_profile=reopening)
            services = BrowserServices(engine, lambda _: None)
            try:
                engine.call('Target.createTarget', {'url': 'about:blank', 'newWindow': True})
                services.connect()
                if reopening:
                    assert [row['id'] for row in services.state['spaces']] == expected, 'Order did not persist'
                    continue
                for count, name in enumerate(('Order B', 'Order C'), 2):
                    services.send('sidebar', 'space:create', {'name': name, 'icon': '◫'})
                    services.wait_state(lambda state: len(state['spaces']) == count)
                initial = [row['id'] for row in services.state['spaces']]
                expected = [initial[2], initial[0], initial[1]]
                services.send('sidebar', 'space:update', {'spaceId': initial[2], 'patch': {'index': 0}})
                services.wait_state(lambda state: [row['id'] for row in state['spaces']] == expected)
                for patch in ({'index': -1}, {'index': 3}, {'index': True}, {'index': 1.5},
                              {'index': 1, 'name': 'Not permitted'}):
                    services.send('sidebar', 'space:update', {'spaceId': initial[2], 'patch': patch})
                    services.send('sidebar', 'ui:ready')
                    time.sleep(.1)
                    assert [row['id'] for row in services.state['spaces']] == expected, 'Invalid patch changed order'
            finally:
                services.close()
                engine.close()
    return {'workspace_reorder': True, 'invalid_indices_rejected': True, 'order_persisted_after_restart': True}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--engine', type=Path, required=True)
    print(json.dumps(qualify(parser.parse_args().engine), indent=2))
