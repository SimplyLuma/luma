# SPDX-License-Identifier: Apache-2.0
"""Bounded cloud request contract shared by the Connect UI and native owner."""
import json

SERVICES = frozenset({'calendar', 'notes', 'contacts', 'photos', 'world-clocks',
                     'weather-places', 'messages', 'calls', 'leaf-books', 'tide-sources'})

def command_plan(request):
    """No executable, path, arbitrary option, hub URL or credential is accepted."""
    if not isinstance(request, dict) or set(request) != {'operation', 'values'}:
        raise ValueError('A typed Connect operation is required.')
    operation, values = request['operation'], request['values']
    if not isinstance(values, dict):
        raise ValueError('Typed operation values are required.')
    if operation in {'status', 'sync', 'invite'} and not values:
        return {'status': (['status', '--json'], 45), 'sync': (['push'], 300),
                'invite': (['invite'], 60)}[operation] + (None,)
    if operation == 'service' and set(values) == {'id', 'enabled'}:
        if values['id'] not in SERVICES or type(values['enabled']) is not bool:
            raise ValueError('A maintained service and explicit switch are required.')
        return ['service', values['id'], 'on' if values['enabled'] else 'off'], 180, None
    if operation == 'sign-out' and set(values) == {'device'}:
        device = values['device']
        if not isinstance(device, str) or len(device) > 128 or any(ord(c) < 32 for c in device):
            raise ValueError('A bounded device identity is required.')
        return ['sign-out'] + (['--device', device] if device else []), 120, None
    if operation == 'profile' and values and set(values) <= {'name', 'email', 'phone', 'discoverable_by_phone'}:
        for key, value in values.items():
            if key == 'discoverable_by_phone':
                if type(value) is not bool: raise ValueError('An explicit discovery switch is required.')
            elif not isinstance(value, str) or len(value) > 256 or any(ord(c) < 32 for c in value):
                raise ValueError('A bounded profile value is required.')
        return ['profile', 'set', '--stdin', '--json'], 60, json.dumps(values)
    if operation == 'connect' and set(values) == {'code', 'name'}:
        import re
        if (not isinstance(values['code'], str) or not re.fullmatch('[A-Z0-9-]{4,128}', values['code'])
                or not isinstance(values['name'], str) or not 1 <= len(values['name']) <= 60
                or any(ord(c) < 32 for c in values['name'])):
            raise ValueError('A bounded enrollment code and device name are required.')
        # Submitting a new one-time code explicitly replaces the saved device
        # registration. The CLI retains its idempotent default for other users.
        return ['enrol', '--hub', 'https://hub.simplyluma.com', '--code', values['code'], '--name', values['name'], '--force'], 60, None
    raise ValueError('That Connect operation is unavailable.')
