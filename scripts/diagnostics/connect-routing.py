#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Read-only, credential-safe enrollment route check; no account content output."""
import json
import os
from pathlib import Path
import stat
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def main():
    directory = Path(os.environ.get('XDG_DATA_HOME', str(Path.home()/'.local/share')))/'luma/connect'
    path = directory/'device.json'
    if not path.is_file():
        print('registration: absent')
        return
    if path.is_symlink() or directory.is_symlink():
        print('registration: unsafe path; no request made')
        return
    for owned in (directory, path):
        facts = owned.stat()
        if facts.st_uid != os.getuid() or stat.S_IMODE(facts.st_mode)&0o077:
            print('registration: permissions need attention; no request made')
            return
    try:
        if path.stat().st_size > 16384:
            raise ValueError()
        identity = json.loads(path.read_text())
        if identity.get('hub', '').rstrip('/') != 'https://hub.simplyluma.com':
            print('registration: another Hub; no request made')
            return
        token = identity['token']
        if not isinstance(token, str) or not token or any(ord(c)<32 for c in token):
            raise ValueError()
    except (OSError, ValueError, KeyError, AttributeError):
        print('registration: invalid; no request made')
        return
    print('registration: present and private')
    opener = build_opener(NoRedirect())
    for label, suffix in (
        ('account', '/account'), ('events', '/events?after=0&wait=0'),
        ('capabilities', '/capabilities'), ('shared-documents', '/collaboration/documents'),
        ('notes', '/notes'), ('contacts', '/contacts'), ('profile', '/profile'),
    ):
        try:
            request = Request('https://hub.simplyluma.com/api/hub/sync'+suffix,
                headers={'Authorization':'Bearer '+token, 'Accept':'application/json',
                    'User-Agent':'ProjectLuma-Connect/1'})
            with opener.open(request, timeout=15) as response:
                print(label+': HTTP '+str(response.status))
        except HTTPError as error:
            print(label+': HTTP '+str(error.code))
            error.close()
        except Exception:
            print(label+': network unavailable')


if __name__ == '__main__':
    main()
