# SPDX-License-Identifier: Apache-2.0
"""Sandbox adapter to the host's bounded preference lifecycle."""
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
from gi.repository import Gio, GLib
from .errors import LumaModsError
from .profile_host import BUS, OBJECT, MAX_OUTPUT
from .resolver import HostContext, Catalog, resolve_mod
from .profile import PreferenceProfile
from .trust import VerificationResult
from .lifecycle import Authorization

class Client:
    def __init__(self):
        try: self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error as error: raise LumaModsError('The host session is unavailable.') from error
        self.presentation = 'desktop'
        self.store = SimpleNamespace(read=lambda: self.call({'operation':'read'}))
        self.lifecycle = self
        self.paths = None
        self.snapshot = None
        self.catalog_root = None
        self.profiles = {}
        self._public_catalog = None
        self._verifications = {}
        self.system_snapshot = None

    def catalog(self):
        result = self.call({'operation':'catalog'})
        if self._public_catalog is not None: self._public_catalog.cleanup()
        self._public_catalog = tempfile.TemporaryDirectory(prefix='luma-mods-public-')
        root = Path(self._public_catalog.name)
        for identifier, text in result['manifests'].items():
            from .manifest import ID_PATTERN
            if not ID_PATTERN.fullmatch(identifier): raise LumaModsError('Invalid catalog identity.')
            (root/(identifier+'.mod.json')).write_text(text)
        self.snapshot = result['snapshot']
        self.profiles = {k:PreferenceProfile(**v) for k,v in result['profiles'].items()}
        self.catalog_root = root
        return root, Catalog.from_directory(root)

    def review(self, identifier, catalog):
        context = self.host_context()
        plan = resolve_mod(identifier,catalog,context)
        result = self.call({'operation':'review','identifier':identifier,'presentation':self.presentation,'snapshot':self.snapshot})
        if plan.as_dict() != result['plan']: raise LumaModsError('The review changed. Refresh Mods before continuing.')
        verifications = {k:VerificationResult(**v) for k,v in result['verifications'].items()}
        if len(self._verifications) >= 32: self._verifications.pop(next(iter(self._verifications)))
        self._verifications[plan.composition_sha256] = verifications
        self.system_snapshot = result.get('system_snapshot')
        return plan, verifications, self.profiles.get(identifier)

    def authority(self, plan, confirmed):
        unverified = {k for k,v in self._verifications[plan.composition_sha256].items() if not v.verified}
        if unverified and not confirmed: raise LumaModsError('Local / Unverified confirmation is required.')
        # This conveys only the UI confirmation bit. Host verification produces
        # all real authority afresh; no serialized trust level is sent.
        return Authorization(trust_levels={},confirmed_unverified=frozenset(unverified))

    def call(self, request):
        try:
            reply = self.connection.call_sync(BUS, OBJECT, BUS, 'Call',
                GLib.Variant('(s)', (json.dumps(request, allow_nan=False),)), GLib.VariantType.new('(s)'),
                Gio.DBusCallFlags.NONE, 30000, None)
            if reply.get_size() > MAX_OUTPUT + 64: raise LumaModsError('The Mods response is too large.')
            return json.loads(reply.unpack()[0])
        except (GLib.Error, ValueError, RecursionError) as error:
            raise LumaModsError('Mods could not reach its host service. Refresh to check the current state before trying again.') from error

    def host_context(self, presentation=None):
        selected = presentation or os.environ.get('LUMA_PRESENTATION_MODE', 'desktop')
        if selected == 'fullscreen-mobile': selected = 'handheld'
        if selected not in ('desktop','tablet','handheld'): selected = 'desktop'
        self.presentation = selected
        return HostContext.from_dict(self.call({'operation':'context','presentation':selected}))

    def _apply(self, operation, plan, profile, authority, expected_generation):
        return self.call({'operation':operation,'identifier':plan.target_id,'generation':expected_generation,
            'presentation':self.presentation,'composition':plan.composition_sha256,'host':plan.host_sha256,
            'profile':profile.source_sha256,'confirmed_unverified':bool(authority.confirmed_unverified),'snapshot':self.snapshot})
    def install(self, plan, profile, authority, *, expected_generation):
        return self._apply('install', plan, profile, authority, expected_generation)
    def update(self, plan, profile, authority, *, expected_generation):
        return self._apply('update', plan, profile, authority, expected_generation)
    def set_enabled(self, identifier, enabled, *, expected_generation):
        return self.call({'operation':'enable','identifier':identifier,'enabled':enabled,'generation':expected_generation})
    def remove(self, identifier, *, expected_generation):
        return self.call({'operation':'remove','identifier':identifier,'generation':expected_generation})
    def recover(self): return self.call({'operation':'recover'})
