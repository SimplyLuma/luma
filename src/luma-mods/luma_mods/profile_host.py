# SPDX-License-Identifier: Apache-2.0
"""Fixed preference bridge. No paths, values, policy or executable payload RPC.

The host re-resolves reviews using the existing PreferenceLifecycle. Privileged
Mods remain owned by ModTransactions1 and its polkit/TUF checks.
"""
from dataclasses import asdict
import hashlib
import json
from .errors import LumaModsError
from .manifest import ID_PATTERN

BUS = 'org.projectluma.ModProfiles1'
OBJECT = '/org/projectluma/ModProfiles1'
MAX_INPUT = 65536
MAX_OUTPUT = 8 * 1024 * 1024

def decode(text):
    if not isinstance(text, str) or len(text.encode('utf-8')) > MAX_INPUT:
        raise LumaModsError('The Mods request is too large.')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise LumaModsError('Duplicate request field.')
            result[key] = value
        return result
    def constant(_): raise LumaModsError('Invalid number.')
    try: value = json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, RecursionError) as error:
        raise LumaModsError('Invalid Mods request.') from error
    if not isinstance(value, dict): raise LumaModsError('Invalid Mods request.')
    return value

def encode(value):
    text = json.dumps(value, allow_nan=False, separators=(',', ':'))
    if len(text.encode('utf-8')) > MAX_OUTPUT: raise LumaModsError('The Mods result is too large.')
    return text

class Profiles:
    def __init__(self, runtime, catalog_loader=None):
        self.runtime = runtime
        self.catalog_loader = catalog_loader or self._catalog

    @staticmethod
    def _catalog():
        from .catalog_runtime import SYSTEM_CLIENT_CONFIG, CatalogClientConfig, user_catalog_cache
        from .runtime import SYSTEM_CATALOG_ROOT, load_catalog
        if SYSTEM_CLIENT_CONFIG.is_file():
            trusted = CatalogClientConfig.from_file(SYSTEM_CLIENT_CONFIG).open(user_catalog_cache())
            catalog = trusted.refresh()
            return trusted.snapshot.index_path.parent, catalog, trusted
        root, catalog = load_catalog(SYSTEM_CATALOG_ROOT)
        return root, catalog, None

    @staticmethod
    def _snapshot(root, catalog, trusted):
        from .runtime import profile_for
        manifests, profiles = {}, {}
        for inspection in catalog.inspections():
            if len(manifests) >= 256: raise LumaModsError('Choose a smaller Mod catalog.')
            identity = inspection.mod.identity.id
            payload = inspection.path.read_bytes()
            if hashlib.sha256(payload).hexdigest() != inspection.source_sha256:
                raise LumaModsError('The catalog changed during review.')
            manifests[identity] = payload.decode('utf-8')
            try: profile = trusted.profile(identity) if trusted else profile_for(root, identity)
            except LumaModsError: continue
            profiles[identity] = asdict(profile)
        identity = {'manifests':manifests, 'profiles':profiles,
            'trusted_snapshot':trusted.snapshot.snapshot_id if trusted else None}
        digest = hashlib.sha256(encode(identity).encode('utf-8')).hexdigest()
        return dict(identity, snapshot=digest)

    def dispatch(self, request):
        from .resolver import resolve_mod
        from .runtime import authorization_for_plan, profile_for, verify_plan
        operation = request.get('operation')
        if operation == 'read' and set(request) == {'operation'}: return self.runtime.store.read()
        if operation == 'context' and set(request) == {'operation', 'presentation'}:
            if request['presentation'] not in ('desktop', 'tablet', 'handheld'): raise LumaModsError('Unknown presentation.')
            return asdict(self.runtime.host_context(request['presentation']))
        if operation == 'recover' and set(request) == {'operation'}: return self.runtime.lifecycle.recover()
        if operation == 'catalog' and set(request) == {'operation'}:
            return self._snapshot(*self.catalog_loader())
        if operation == 'review' and set(request) == {'operation','identifier','presentation','snapshot'}:
            if request['presentation'] not in ('desktop','tablet','handheld') or not isinstance(request['identifier'],str) or not ID_PATTERN.fullmatch(request['identifier']):
                raise LumaModsError('Invalid review request.')
            root, catalog, trusted = self.catalog_loader()
            if self._snapshot(root,catalog,trusted)['snapshot'] != request['snapshot']:
                raise LumaModsError('The catalog changed. Refresh Mods before reviewing it.')
            context = self.runtime.host_context(request['presentation'])
            if trusted:
                review = trusted.plan(request['identifier'],context)
                plan, verifications = review.plan, review.verifications
            else:
                plan = resolve_mod(request['identifier'],catalog,context)
                verifications = verify_plan(plan,None)
            return {'plan':plan.as_dict(),'verifications':{k:v.as_dict() for k,v in verifications.items()},
                'system_snapshot':trusted.snapshot.snapshot_id if trusted else None}
        fields = {'operation', 'identifier', 'generation'}
        if operation in ('install', 'update'): fields |= {'presentation', 'composition', 'host', 'profile', 'confirmed_unverified', 'snapshot'}
        elif operation == 'enable': fields.add('enabled')
        elif operation != 'remove': raise LumaModsError('Unsupported Mods operation.')
        if set(request) != fields: raise LumaModsError('Invalid Mods operation fields.')
        identifier, generation = request['identifier'], request['generation']
        if not isinstance(identifier, str) or not ID_PATTERN.fullmatch(identifier): raise LumaModsError('Invalid Mod identity.')
        if type(generation) is not int or not 0 <= generation <= 2**53: raise LumaModsError('Invalid state generation.')
        if operation == 'remove': return self.runtime.lifecycle.remove(identifier, expected_generation=generation)
        if operation == 'enable':
            if type(request['enabled']) is not bool: raise LumaModsError('Invalid enabled value.')
            return self.runtime.lifecycle.set_enabled(identifier, request['enabled'], expected_generation=generation)
        if request['presentation'] not in ('desktop', 'tablet', 'handheld') or type(request['confirmed_unverified']) is not bool:
            raise LumaModsError('Invalid review confirmation.')
        root, catalog, trusted = self.catalog_loader()
        if self._snapshot(root,catalog,trusted)['snapshot'] != request['snapshot']:
            raise LumaModsError('The catalog changed after review.')
        context = self.runtime.host_context(request['presentation'])
        if trusted is None:
            plan = resolve_mod(identifier, catalog, context)
            profile = profile_for(root, identifier)
            authority = authorization_for_plan(plan, None, confirmed_unverified=request['confirmed_unverified'])
        else:
            transaction = trusted.prepare_transaction(identifier, context,
                reviewed_composition_sha256=request['composition'], reviewed_profile_sha256=request['profile'])
            plan, profile = transaction.trusted.plan, transaction.profile
            authority = transaction.trusted.authorization(confirmed_unverified=request['confirmed_unverified'])
            if self._snapshot(root,trusted.catalog,trusted)['snapshot'] != request['snapshot']:
                raise LumaModsError('The catalog changed while preparing this operation.')
        if (plan.composition_sha256 != request['composition'] or plan.host_sha256 != request['host']
                or profile.source_sha256 != request['profile']):
            raise LumaModsError('This review has changed. Open the Mod again before continuing.')
        return getattr(self.runtime.lifecycle, operation)(plan, profile, authority, expected_generation=generation)
