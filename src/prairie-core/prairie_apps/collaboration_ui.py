# SPDX-License-Identifier: Apache-2.0
"""Shared native recipient/permission composition for Notes and Tasks."""
from threading import Thread
from gi.repository import GLib
from luma_appkit import Collaborator, Person, ShareSheet, ShareSubject, ShareResult, Toast
from .collaboration import CollaborationCache, CollaborationClient, PeopleDirectory, public_name


class NativeCollaborationShare:
    def __init__(self, window, anchor, *, title, kind, local_id, content, copy_choice, on_changed=None):
        self.window, self.anchor = window, anchor
        self.title, self.kind, self.local_id = title, kind, local_id
        self.content, self.copy_choice, self.on_changed = content, copy_choice, on_changed
        self.sheet = None
        self.people = {}
        self.query_generation = 0
        self.busy = False
        self.lookup_query = None
        self._run(self._prepare, self._ready)

    def _run(self, operation, success):
        def worker():
            try:
                result, error = operation(), None
            except Exception as caught:
                result, error = None, caught
            GLib.idle_add(self._finished, result, error, success)
        Thread(target=worker, daemon=True, name='luma-collaboration').start()

    def _finished(self, result, error, success):
        try:
            if getattr(self.window, '_closed', False) or getattr(self.window, 'closed', False):
                return False
            if error:
                Toast.show(self.window.layer_host, 'Could not collaborate: ' + str(error), kind='error')
            else:
                success(result)
        finally:
            if success == self._membership_done:
                self.busy = False
        return False

    def _prepare(self):
        self.client = CollaborationClient()
        directory = PeopleDirectory()
        cache = CollaborationCache()
        try:
            existing = next((item for item in cache.mappings(self.client, self.kind) if item['local_id'] == self.local_id or item['id'] == self.local_id.removeprefix('luma-shared:')), None)
            snapshot = self.client.read(existing['id']) if existing else self.client.create(self.kind, self.content)
            if existing is None:
                cache.remember(self.client, snapshot, self.local_id)
            import os
            if os.environ.get('FLATPAK_ID'):
                from .collaboration_transport import call
                response = call('GetCollaborationFavorites')
                if set(response) != {'handles'} or not isinstance(response['handles'], list):
                    raise ValueError('Connect recipient favorites are unavailable.')
                favorites = set(response['handles'])
            else:
                from .eds_backend import load_contacts
                favorites = set(sorted({r.handle.casefold() for r in load_contacts() if r.favourite and r.handle})[:128])
            friends = directory.friends()
            frequent = cache.frequent(self.client)
            rank = {p['account']: i for i, p in enumerate(frequent)}
            known = {p['account'] for p in friends}
            known_handles = {str(p.get('handle') or '').casefold() for p in friends + frequent}
            # A cached name is never an identity lookup result. Revalidate
            # recent non-friends with the ordinary exact-username endpoint.
            from concurrent.futures import ThreadPoolExecutor
            def verified(person):
                try:
                    fresh = directory.lookup(person['handle'])
                    return fresh if not person.get('account') or fresh['account'] == person['account'] else None
                except Exception:
                    return None
            candidates = [p for p in frequent if p['account'] not in known]
            candidates.extend({'handle': handle} for handle in sorted(favorites - known_handles))
            with ThreadPoolExecutor(max_workers=4) as pool:
                for person in pool.map(verified, candidates):
                    if person and person['account'] not in known and person['account'] != snapshot.get('account'):
                        friends.append(person)
                        known.add(person['account'])
            friends.sort(key=lambda person: (str(person.get("handle") or "").casefold() not in favorites,
                                              rank.get(person['account'], len(rank)), public_name(person).casefold()))
            return snapshot, friends
        finally:
            cache.close()

    def _person(self, raw):
        person = Person(public_name(raw), username=raw.get('handle') or '', hue=raw.get('hue'))
        self.people[person.username.casefold() or raw['account']] = raw
        return person

    def _ready(self, prepared):
        # Receiving a live update can redraw a list while the asynchronous
        # invitation lookup is running. Its old Share button is then detached;
        # present on the persistent window host instead of losing the action.
        if self.anchor.get_root() is None:
            self.anchor = self.window.layer_host
        snapshot, friends = prepared
        self.snapshot = snapshot
        members = snapshot['members']
        owner = next((m for m in members if m['role'] == 'owner'), {'display_name': 'You', 'account': ''})
        collaborators = [Collaborator(self._person(m), m['role']) for m in members if m['role'] != 'owner']
        people = [self._person(person) for person in friends]
        self.sheet = ShareSheet.present(self.anchor,
            document=ShareSubject(self.title, 'Notes' if self.kind == 'note' else 'Tasks', kind='doc',
                                  icon='org.projectluma.Notes' if self.kind == 'note' else 'org.projectluma.Tasks',
                                  mime_type='text/markdown' if self.kind == 'note' else 'text/calendar',
                                  share_link_available=False),
            choices=('work-together', 'send-copy'), owner=self._person(owner), people=people,
            collaborators=collaborators, suggest=self._suggest, on_choice=self._choice,
            manage_access=snapshot['role'] == 'owner', owner_is_you=snapshot['role'] == 'owner')
        self.sheet.set_name('nt-share-sheet' if self.kind == 'note' else 'task-share-sheet')
        if self.on_changed:
            self.on_changed(snapshot)

    def _suggest(self, query):
        matches = [self._person(p) for p in list(self.people.values()) if query.casefold().lstrip('@') in (public_name(p) + ' ' + (p.get('handle') or '')).casefold()]
        if query != self.lookup_query:
            self.lookup_query = query
            self.query_generation += 1
            generation = self.query_generation
            def lookup():
                try:
                    return generation, query, PeopleDirectory().lookup(query), None
                except Exception as error:
                    return generation, query, None, str(error)
            self._run(lookup, self._lookup_done)
            if self.sheet:
                GLib.idle_add(self.sheet.set_suggestions, query, matches, "Looking up this Luma username…")
        return matches

    def _lookup_done(self, result):
        generation, query, raw, error = result
        if not self.sheet or generation != self.query_generation or self.sheet.query != query:
            return
        people = [self._person(raw)] if raw else []
        # Public shared-platform entry point applies only the current query.
        self.sheet.set_suggestions(query, people, status=error or '')

    def _choice(self, choice, value):
        if choice not in ('invite', 'role', 'remove'):
            return self.copy_choice(choice, value)
        if self.busy:
            return ShareResult(False, 'Waiting for Luma Hub…')
        if self.snapshot['role'] != 'owner':
            return ShareResult(False, 'Only the owner can change sharing access.')
        collaborator, role = value if choice == 'role' else (value, value.role)
        raw = self.people.get(collaborator.person.username.casefold())
        if not raw or not raw.get('account'):
            return ShareResult(False, 'Choose a verified Luma account.')
        action = 'revoke' if choice == 'remove' else 'invite'
        data = {'account': raw['account']}
        if action == 'invite':
            data['role'] = role
        self.busy = True
        self._membership_choice = choice
        def operation():
            self.client.operation(self.snapshot['id'], action, data)
            if action == 'invite':
                cache = CollaborationCache()
                try:
                    cache.used_recipient(self.client, raw)
                finally:
                    cache.close()
            return self.client.read(self.snapshot['id'])
        self._run(operation, self._membership_done)
        return ShareResult(False, 'Updating sharing…')

    def _membership_done(self, snapshot):
        self.snapshot = snapshot
        if self.sheet:
            if self._membership_choice == 'invite':
                self.sheet.query = ''
                self.lookup_query = None
                self.query_generation += 1
            self.sheet.collaborators = [Collaborator(self._person(m), m['role']) for m in snapshot['members'] if m['role'] != 'owner']
            self.sheet._draw()
        if self.on_changed:
            self.on_changed(snapshot)
        Toast.show(self.window.layer_host, 'Sharing updated. New invitations await acceptance.')
