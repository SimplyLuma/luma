import unittest
from luma_installer.depot_flatpak import validate_remote, SourceUnavailable, ResolvedSource, operations_match, select_installation


class Remote:
    def __init__(self, url='https://dl.flathub.org/repo/', disabled=False, verify=True):
        self.url, self.disabled, self.verify = url, disabled, verify
    def get_name(self): return 'flathub'
    def get_url(self): return self.url
    def get_disabled(self): return self.disabled
    def get_gpg_verify(self): return self.verify


class SourceTests(unittest.TestCase):
    def test_configured_signed_remote(self):
        validate_remote(Remote())
    def test_rejects_changed_address(self):
        for url in ('http://dl.flathub.org/repo/', 'https://dl.flathub.org.evil/repo/',
                    'https://dl.flathub.org/repo/?redirect=evil', 'file:///tmp/repo'):
            with self.subTest(url=url), self.assertRaises(SourceUnavailable):
                validate_remote(Remote(url=url))
    def test_rejects_disabled(self):
        with self.assertRaises(SourceUnavailable): validate_remote(Remote(disabled=True))
    def test_rejects_unsigned(self):
        with self.assertRaises(SourceUnavailable): validate_remote(Remote(verify=False))


class ScopeTests(unittest.TestCase):
    def test_disabled_user_remote_does_not_hide_healthy_system_remote(self):
        from types import SimpleNamespace
        user = SimpleNamespace(list_remotes=lambda _: [Remote(disabled=True)])
        system = SimpleNamespace(list_remotes=lambda _: [Remote()])
        self.assertIs(select_installation((user, system)), system)
        with self.assertRaises(SourceUnavailable): select_installation((user,))
    def test_bad_address_is_not_used_even_with_same_name(self):
        from types import SimpleNamespace
        bad = SimpleNamespace(list_remotes=lambda _: [Remote(url='https://invalid.example/')])
        good = SimpleNamespace(list_remotes=lambda _: [Remote()])
        self.assertIs(select_installation((bad, good)), good)
        with self.assertRaises(SourceUnavailable): select_installation((bad,))


class OperationTests(unittest.TestCase):
    def test_prepared_operations_preserve_identity_and_trust(self):
        from types import SimpleNamespace
        source = ResolvedSource('org.mozilla.firefox', 'x86_64',
                                'app/org.mozilla.firefox/x86_64/stable', 'abc')
        def op(ref=source.ref, commit='abc', remote='flathub'):
            return SimpleNamespace(get_ref=lambda: ref, get_commit=lambda: commit,
                                   get_remote=lambda: remote)
        self.assertTrue(operations_match(source, [op(), op(ref='runtime/test')]))
        self.assertFalse(operations_match(source, []))
        self.assertFalse(operations_match(source, [op(), op()]))
        self.assertFalse(operations_match(source, [op(commit='changed')]))
        self.assertFalse(operations_match(source, [op(remote='other')]))
        self.assertFalse(operations_match(source, [op(), op(ref='runtime/test', remote='other')]))





class LumaRemoteTests(unittest.TestCase):
    """The Luma remote gets exactly Flathub's checks, and nothing else is ever added."""

    def remote(self, url='https://dl.simplyluma.com/repo', disabled=False, verify=True, name='luma'):
        remote = Remote(url, disabled, verify)
        remote.get_name = lambda: name
        return remote

    def test_the_luma_remote_is_validated_by_its_own_address(self):
        from luma_installer.depot_flatpak import validate_remote
        validate_remote(self.remote())
        validate_remote(self.remote(url='https://dl.simplyluma.com/repo/'))
        for url in ('https://dl.flathub.org/repo/', 'https://dl.simplyluma.com.evil/repo',
                    'http://dl.simplyluma.com/repo', 'https://dl.simplyluma.com/repo/../other'):
            with self.subTest(url=url), self.assertRaises(SourceUnavailable):
                validate_remote(self.remote(url=url))
        with self.assertRaises(SourceUnavailable):
            validate_remote(self.remote(verify=False))
        with self.assertRaises(SourceUnavailable):
            validate_remote(Remote(), 'luma')  # Flathub's address under Luma's name

    def test_selection_is_per_remote(self):
        from types import SimpleNamespace
        both = SimpleNamespace(list_remotes=lambda _: [Remote(), self.remote()])
        flathub_only = SimpleNamespace(list_remotes=lambda _: [Remote()])
        self.assertIs(select_installation((flathub_only, both), remote='luma'), both)
        self.assertIs(select_installation((flathub_only, both)), flathub_only)
        with self.assertRaises(SourceUnavailable):
            select_installation((flathub_only,), remote='luma')
        with self.assertRaises(SourceUnavailable):
            select_installation((both,), remote='fedora')

    def test_dependencies_must_come_from_the_apps_own_remote(self):
        from types import SimpleNamespace
        source = ResolvedSource('org.projectluma.Canvas', 'x86_64',
                                'app/org.projectluma.Canvas/x86_64/stable', 'abc', 'luma')
        def op(ref=source.ref, commit='abc', remote='luma'):
            return SimpleNamespace(get_ref=lambda: ref, get_commit=lambda: commit, get_remote=lambda: remote)
        self.assertTrue(operations_match(source, [op(ref='runtime/org.projectluma.Platform/x86_64/44'), op()]))
        # A Luma-remote app may build on Flathub's runtimes, which Luma does not redistribute.
        self.assertTrue(operations_match(source, [op(ref='runtime/org.freedesktop.Platform/x86_64/25.08',
                                                     remote='flathub'),
                                                  op(ref='runtime/org.freedesktop.Platform.GL.default/x86_64/25.08',
                                                     remote='flathub'), op()]))
        # ...but never an application, never the app itself, and never another remote.
        self.assertFalse(operations_match(source, [op(ref='app/org.example.Helper/x86_64/stable',
                                                      remote='flathub'), op()]))
        self.assertFalse(operations_match(source, [op(remote='flathub')]))
        self.assertFalse(operations_match(source, [op(ref='runtime/org.gnome.Platform/x86_64/48',
                                                      remote='fedora'), op()]))

    def test_flathub_apps_take_nothing_from_the_luma_remote(self):
        from types import SimpleNamespace
        source = ResolvedSource('org.mozilla.firefox', 'x86_64',
                                'app/org.mozilla.firefox/x86_64/stable', 'abc', 'flathub')
        def op(ref=source.ref, commit='abc', remote='flathub'):
            return SimpleNamespace(get_ref=lambda: ref, get_commit=lambda: commit, get_remote=lambda: remote)
        self.assertFalse(operations_match(source, [op(ref='runtime/org.projectluma.Platform/x86_64/44',
                                                      remote='luma'), op()]))


class RepoFileTests(unittest.TestCase):
    KEY = 'mQINBGbLumaTestKeyMaterialThatIsLongEnoughToLookLikeOneAAAAAAAAAAAA='

    def write(self, text):
        import tempfile
        from pathlib import Path
        handle = tempfile.NamedTemporaryFile('w', suffix='.flatpakrepo', delete=False)
        handle.write(text)
        handle.close()
        self.addCleanup(lambda: Path(handle.name).unlink())
        return handle.name

    def test_a_good_file(self):
        from luma_installer.depot_flatpak import read_repo_file
        path = self.write(f'[Flatpak Repo]\nTitle=Luma\nUrl=https://dl.simplyluma.com/repo/\n'
                          f'Homepage=https://simplyluma.com/\nGPGKey={self.KEY}\n')
        self.assertIn(b'dl.simplyluma.com', read_repo_file(path))

    def test_a_file_that_would_weaken_or_redirect_the_remote_is_refused(self):
        from luma_installer.depot_flatpak import read_repo_file
        cases = (
            f'[Flatpak Repo]\nUrl=https://evil.example/repo/\nGPGKey={self.KEY}\n',
            '[Flatpak Repo]\nUrl=https://dl.simplyluma.com/repo/\n',
            '[Flatpak Repo]\nUrl=https://dl.simplyluma.com/repo/\nGPGKey=not base64!\n',
            f'[Flatpak Repo]\nUrl=https://dl.simplyluma.com/repo/\nGPGKey={self.KEY}\nNoGPGVerify=true\n',
            f'[Other]\nUrl=https://dl.simplyluma.com/repo/\nGPGKey={self.KEY}\n',
            '\xff not a keyfile',
        )
        for text in cases:
            with self.subTest(text=text[:40]), self.assertRaises(SourceUnavailable):
                read_repo_file(self.write(text))
        with self.assertRaises(SourceUnavailable):
            read_repo_file('/nonexistent/luma.flatpakrepo')

    def test_an_existing_untrustworthy_luma_remote_is_reported_not_replaced(self):
        from types import SimpleNamespace
        from luma_installer.depot_flatpak import ensure_luma_remote
        added = []
        bad = Remote(url='https://mirror.example/repo')
        bad.get_name = lambda: 'luma'
        installation = SimpleNamespace(list_remotes=lambda _: [bad], add_remote=lambda *a: added.append(a))
        with self.assertRaises(SourceUnavailable):
            ensure_luma_remote(installations=(installation,))
        self.assertEqual(added, [])

    def test_the_luma_remote_is_added_beside_flathub(self):
        import sys
        from types import SimpleNamespace
        from unittest import mock
        from luma_installer import depot_flatpak
        flathub = Remote(url='https://dl.flathub.org/repo/')
        flathub.get_name = lambda: 'flathub'
        added = []

        def installation(name, remotes):
            state = {'remotes': list(remotes)}

            def add_remote(remote, *_args):
                added.append(name)
                luma = Remote(url='https://dl.simplyluma.com/repo/')
                luma.get_name = lambda: 'luma'
                state['remotes'].append(luma)
            return SimpleNamespace(list_remotes=lambda _: state['remotes'], add_remote=add_remote,
                                   drop_caches=lambda *_: None)
        user, system = installation('user', []), installation('system', [flathub])
        fake = SimpleNamespace(Remote=SimpleNamespace(new_from_file=lambda *_: SimpleNamespace(
            set_gpg_verify=lambda *_: None, set_disabled=lambda *_: None)))
        gi_repository = SimpleNamespace(Flatpak=fake.Remote and fake, GLib=SimpleNamespace(
            Bytes=SimpleNamespace(new=lambda data: data), Error=Exception))
        with mock.patch.object(depot_flatpak, 'read_repo_file', return_value=b'x'), \
                mock.patch.dict(sys.modules, {'gi.repository': gi_repository}), \
                mock.patch('gi.require_version', lambda *_: None):
            chosen = depot_flatpak.ensure_luma_remote(installations=(user, system))
        self.assertEqual(added, ['system'])
        self.assertIs(chosen, system)




class ReviewedUpdateTests(unittest.TestCase):
    def test_remote_moving_after_review_is_refused_before_transaction(self):
        from luma_installer.depot_flatpak import require_reviewed_update
        source = ResolvedSource('org.projectluma.Notes', 'x86_64',
                                'app/org.projectluma.Notes/x86_64/beta', 'a' * 64, 'luma', 'beta')
        require_reviewed_update(source, 'a' * 64)
        for commit in ('', 'b' * 64, 'a' * 12, 'A' * 64, None):
            with self.subTest(commit=commit), self.assertRaises(SourceUnavailable):
                require_reviewed_update(source, commit)


class UpdateBaselineTests(unittest.TestCase):
    def test_downgraded_replaced_removed_or_unknown_installed_baseline_refuses_update(self):
        from types import SimpleNamespace
        from luma_installer.depot_flatpak import require_update_baseline
        ref = SimpleNamespace(get_commit=lambda: 'a' * 64)
        require_update_baseline(ref, 'a' * 64)
        for current, expected in ((None, 'a' * 64), (ref, ''), (ref, 'b' * 64), (ref, None)):
            with self.subTest(expected=expected), self.assertRaises(SourceUnavailable):
                require_update_baseline(current, expected)


if __name__ == "__main__":
    unittest.main()
