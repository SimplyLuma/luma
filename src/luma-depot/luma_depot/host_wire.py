# SPDX-License-Identifier: Apache-2.0
"""Bounded public Depot DTOs, not object deserialization or host instructions."""
from dataclasses import fields, is_dataclass
from datetime import datetime
import json
import math
import re

BUS = 'org.projectluma.DepotHost1'
OBJECT = '/org/projectluma/DepotHost1'
MAX_REQUEST = 8192
MAX_REPLY = 2 * 1024 * 1024
MAX_ITEMS = 4096
TOKEN = re.compile(r'[a-f0-9]{32}\Z')
IDENTITY = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:+-]{0,254}\Z')
COMMIT = re.compile(r'[a-f0-9]{64}\Z')

# Only fixed public view models may cross the boundary. Never load a class,
# module, file, pickled object, executable, URI or repository supplied by a caller.
TYPE_NAMES = frozenset({'App', 'Catalogue', 'Category', 'Collection', 'InstalledApp',
    'Permission', 'Progress', 'Release', 'Screenshot', 'SystemTool',
    'FirmwareUpdate', 'Offer', 'Requirement', 'Failure', 'Review', 'ReviewPage', 'Reply'})

def _classes():
    from . import providers, system_tools, system_updates
    from luma_installer import depot_firmware_safety, depot_reviews
    return {name: cls for module in (providers, system_tools, system_updates, depot_firmware_safety, depot_reviews)
            for name in TYPE_NAMES if isinstance(cls := getattr(module, name, None), type)
            and is_dataclass(cls)}

def dumps(value):
    count = budget = 0
    def charge(size):
        nonlocal budget
        budget += size
        if budget > MAX_REPLY: raise ValueError('Depot response is too large.')
    def mapping(item, depth, *, internal=False):
        if len(item) > MAX_ITEMS or not all(type(k) is str for k in item):
            raise ValueError('Invalid Depot mapping.')
        if not internal and any(k.startswith('$') for k in item):
            raise ValueError('Reserved Depot field.')
        charge(2 + len(item))
        out={}
        for key,value in item.items():
            if len(key.encode('utf-8')) > 65536: raise ValueError('Depot key is too long.')
            charge(len(json.dumps(key,ensure_ascii=False).encode('utf-8'))+1)
            out[key]=encode(value,depth+1)
        return out
    def encode(item, depth=0):
        nonlocal count
        count += 1
        if count > MAX_ITEMS * 32 or depth > 16:
            raise ValueError('Depot response exceeds its bounded view contract.')
        if item is None or type(item) in (bool, int, float):
            if type(item) is float and not math.isfinite(item): raise ValueError('Non-finite Depot value.')
            if type(item) is int and abs(item) >= 2**64: raise ValueError('Depot integer is too large.')
            charge(len(json.dumps(item,allow_nan=False)))
            return item
        if type(item) is str:
            if len(item.encode('utf-8')) > 65536: raise ValueError('Depot text is too long.')
            charge(len(json.dumps(item,ensure_ascii=False).encode('utf-8')))
            return item
        if isinstance(item, datetime): return mapping({'$time':item.isoformat()},depth,internal=True)
        if is_dataclass(item) and type(item).__name__ in TYPE_NAMES:
            return mapping({'$type':type(item).__name__,'fields':
                            {f.name:getattr(item,f.name) for f in fields(item)}},depth,internal=True)
        if isinstance(item, tuple):
            if len(item) > MAX_ITEMS: raise ValueError('Depot tuple is too large.')
            return mapping({'$tuple':list(item)},depth,internal=True)
        if type(item) is list and len(item) <= MAX_ITEMS:
            charge(2+len(item));return [encode(x,depth+1) for x in item]
        if type(item) is dict: return mapping(item,depth)
        raise ValueError('Non-public Depot value.')
    # The aggregate budget is charged while building the public tree, before
    # allocating a final JSON buffer. Many individually valid strings cannot
    # balloon into hundreds of megabytes before a 2 MiB refusal.
    text=json.dumps(encode(value),separators=(',',':'),ensure_ascii=False,allow_nan=False)
    if len(text.encode('utf-8'))>MAX_REPLY:raise ValueError('Depot response is too large.')
    return text


def loads(text):
    if not isinstance(text,str) or len(text.encode('utf-8')) > MAX_REPLY:
        raise ValueError('Depot response is too large.')
    types = _classes()
    count = 0
    def decode(item, depth=0):
        nonlocal count
        count += 1
        if count > MAX_ITEMS * 32 or depth > 16: raise ValueError('Depot response is too complex.')
        if item is None or type(item) in (str, bool, int): return item
        if type(item) is float and math.isfinite(item): return item
        if type(item) is list and len(item) <= MAX_ITEMS: return [decode(x,depth+1) for x in item]
        if type(item) is not dict or len(item) > MAX_ITEMS: raise ValueError('Invalid Depot view.')
        if '$tuple' in item:
            if set(item) != {'$tuple'} or type(item['$tuple']) is not list or len(item['$tuple']) > MAX_ITEMS:
                raise ValueError('Invalid tuple.')
            return tuple(decode(x,depth+1) for x in item['$tuple'])
        if '$time' in item:
            if set(item) != {'$time'}: raise ValueError('Invalid time.')
            return datetime.fromisoformat(item['$time'])
        if '$type' in item:
            cls = types.get(item['$type'])
            values = item.get('fields')
            if set(item) != {'$type','fields'} or cls is None or type(values) is not dict or set(values) != {f.name for f in fields(cls)}:
                raise ValueError('Unrecognized Depot view model.')
            return cls(**{k:decode(v,depth+1) for k,v in values.items()})
        if any(k.startswith('$') for k in item): raise ValueError('Unknown Depot field.')
        return {k:decode(v,depth+1) for k,v in item.items()}
    return decode(json.loads(text, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite value.'))))

# Strict operation shapes keep newly introduced fields from becoming authority.
SHAPES = {
 'Lifecycle': {'event':str,'detail':str,'seconds':float,'window':str},
 'Catalogue': {'refresh':bool}, 'Installed': {'refresh':bool},
 'Permissions': {'app_id':str}, 'FreeBytes': {}, 'Launch': {'app_id':str},
 'ReviewRemoval': {'app_id':str}, 'Install': {'app_id':str},
 'Update': {'app_id':str,'commit':str,'baseline':str,'approve':bool},
 'ReviewChannel': {'app_id':str,'branch':str},
 'SwitchChannel': {'app_id':str,'review':str},
 'Revert': {'app_id':str,'commit':str}, 'Remove': {'app_id':str,'keep_data':bool},
 'SystemChange': {'app_id':str,'action':str}, 'SystemTools': {},
 'RemoveSystemTool': {'name':str}, 'GetSettings': {},
 'SetSettings': {'install_events':bool,'countme':bool,'app_updates':bool},
 'FirmwareLoad': {}, 'FirmwareRefresh': {},
 'FirmwareBlocklistRefresh': {},
 'FirmwareInstall': {'device_id':str,'version':str}, 'FirmwareUnsubscribe': {},
 'ReviewsEnrolled': {}, 'Reviews': {'slug':str,'cursor':str},
 'SaveReview': {'slug':str,'rating':int,'title':str,'body':str}, 'DeleteReview': {'slug':str},
}
MUTATIONS = frozenset({'Install','Update','SwitchChannel','Revert','Remove',
                      'SystemChange','RemoveSystemTool','FirmwareInstall','SaveReview','DeleteReview'})

def request(operation, text):
    if operation not in SHAPES or not isinstance(text,str) or len(text.encode('utf-8')) > MAX_REQUEST:
        raise ValueError('Unsupported Depot operation.')
    def unique(pairs):
        value={}
        for key,item in pairs:
            if key in value: raise ValueError('Duplicate request field.')
            value[key]=item
        return value
    value=json.loads(text, object_pairs_hook=unique)
    shape=SHAPES[operation]
    if type(value) is not dict or set(value) != set(shape) or any(type(value[k]) is not t for k,t in shape.items()):
        raise ValueError('Invalid Depot operation fields.')
    for k,v in value.items():
        limit = {'title':100,'body':4000,'cursor':256,'detail':512}.get(k,255)
        if type(v) is str and (len(v)>limit or '\x00' in v): raise ValueError('Invalid Depot field.')
    if operation == 'Lifecycle':
        if value['event'] not in ('asked','shown','slow','failed','degraded'):
            raise ValueError('Unsupported Depot lifecycle event.')
        if not math.isfinite(value['seconds']) or not 0 <= value['seconds'] <= 600:
            raise ValueError('Invalid Depot lifecycle duration.')
        if value['window'] not in ('','DepotWindow','FailureWindow'):
            raise ValueError('Unsupported Depot window.')
    for key in ('app_id','name','device_id'):
        if key in value and not IDENTITY.fullmatch(value[key]): raise ValueError('Invalid Depot identity.')
    for key in ('commit','baseline'):
        if key in value and not COMMIT.fullmatch(value[key]): raise ValueError('Refresh this update before continuing.')
    if 'review' in value and not TOKEN.fullmatch(value['review']): raise ValueError('Review this channel again.')
    if 'branch' in value and value['branch'] not in ('beta','nightly'): raise ValueError('Unsupported app channel.')
    if 'action' in value and value['action'] not in ('override-remove','override-reset'): raise ValueError('Unsupported system change.')
    if 'slug' in value and not re.fullmatch(r'[a-z][a-z0-9-]{0,63}',value['slug']): raise ValueError('Invalid review application.')
    if 'cursor' in value and value['cursor'] and not re.fullmatch(r'[A-Za-z0-9_.:=-]{1,256}',value['cursor']): raise ValueError('Invalid review cursor.')
    if 'rating' in value and not 1 <= value['rating'] <= 5: raise ValueError('Choose from one to five stars.')
    return value
