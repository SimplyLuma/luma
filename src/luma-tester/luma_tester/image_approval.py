# SPDX-License-Identifier: Apache-2.0
"""Explicit image curation, independent of the Hub's historical download index."""
import json
from pathlib import Path
import re
from urllib.parse import urlparse
from urllib.request import Request, build_opener

from .core import HTTPSRedirects, TesterError
from .vm import atomic_json

URL = 'https://raw.githubusercontent.com/SimplyLuma/try/main/channels/images.json'


def validate(document):
    if (not isinstance(document,dict) or type(document.get('schema')) is not int or document['schema'] != 1
            or type(document.get('generation')) is not int or document['generation'] < 1
            or not isinstance(document.get('images'),list) or len(document['images']) > 2):
        raise TesterError('Invalid approved-image channel.')
    architectures=set()
    for item in document['images']:
        if not isinstance(item,dict) or set(item)!={'artifact_id','sha256'}:
            raise TesterError('Invalid approved-image entry.')
        match=re.fullmatch(r'os/(nightly|beta|stable)/[A-Za-z0-9._-]{1,64}/(x86_64|aarch64)',str(item.get('artifact_id','')))
        if not match or match[2] in architectures:
            raise TesterError('Approve at most one ISO per architecture.')
        architectures.add(match[2])
        if not re.fullmatch('[0-9a-f]{64}',str(item['sha256'])):
            raise TesterError('Invalid approved-image checksum.')
    return document


def load(root, refresh=False):
    policy=validate(json.loads(Path(__file__).with_name('approved-images.json').read_text()))
    cache=Path(root)/'approved-images.json'
    try:
        saved=validate(json.loads(cache.read_text()))
        if saved['generation'] > policy['generation'] or saved == policy:policy=saved
    except (OSError,ValueError,TypeError,TesterError):pass
    if refresh:
        try:
            request=Request(URL,headers={'Accept':'application/json','Cache-Control':'no-cache'})
            with build_opener(HTTPSRedirects).open(request,timeout=3) as response:
                if urlparse(response.url).netloc != urlparse(URL).netloc:
                    raise TesterError('Unexpected approval channel origin.')
                body=response.read(65537)
            if len(body)>65536:raise TesterError('Approval channel exceeds its limit.')
            new=validate(json.loads(body))
            if new['generation'] > policy['generation']:
                atomic_json(cache,new);policy=new
        except (OSError,ValueError,TypeError,TesterError):pass
    return policy


def allows(artifact,policy):
    return any(item['artifact_id']==artifact.get('artifact_id')
        and artifact.get('arch')==item['artifact_id'].rsplit('/',1)[1]
        and item['sha256']==artifact.get('sha256')
        for item in policy['images'])
