# SPDX-License-Identifier: Apache-2.0
"""Leaf's EPUB parser and URI renderer, scoped to a read-only Viewer session."""
from pathlib import Path
from urllib.parse import quote, urlparse, unquote


def open_book(path: Path):
    # Inspect the ZIP directory before Leaf reads metadata. No extraction,
    # external stylesheets, filesystem access, or book JavaScript is needed.
    from luma_leaf.epub import Epub
    return Epub(str(path), max_members=10_000, max_archive_size=256 * 1024 * 1024,
                max_resource_size=16 * 1024 * 1024)


def create_preview(book, on_section, on_ready, on_error):
    import gi
    gi.require_version('Gtk', '4.0')
    gi.require_version('WebKit', '6.0')
    from gi.repository import Gtk, WebKit
    from luma_leaf.scheme import LeafScheme
    from luma_appkit import BarAction, apply_type
    from luma_appkit.action_center import make_control

    class BookPreview(Gtk.Box):
        def __init__(self):
            super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                             hexpand=True, vexpand=True)
            self.book = book
            self.closed = False
            self.section = 0
            context = WebKit.WebContext.new()
            self.scheme = LeafScheme(lambda key: book.path if key == 'preview' else None)
            self.scheme._open['preview'] = book
            self.scheme.register(context)
            settings = WebKit.Settings.new()
            settings.set_enable_javascript(False)
            settings.set_enable_html5_database(False)
            settings.set_enable_html5_local_storage(False)
            settings.set_allow_file_access_from_file_urls(False)
            settings.set_allow_universal_access_from_file_urls(False)
            settings.set_enable_webgl(False)
            settings.set_enable_media(False)
            self.web = WebKit.WebView(web_context=context, settings=settings,
                                     network_session=WebKit.NetworkSession.new_ephemeral(),
                                     hexpand=True, vexpand=True)
            self.web.set_name('vw-epub-pages')
            self.web.connect('decide-policy', self._policy)
            self.web.connect('load-changed', self._loaded)
            self.web.connect('load-failed', self._failed)
            self.web.connect('web-process-terminated', self._terminated)
            navigation = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
            self.previous = make_control(BarAction('chevron-left', tooltip='Previous section', on_activate=lambda: self.show_section(self.section - 1)))
            self.next = make_control(BarAction('chevron-right', tooltip='Next section', on_activate=lambda: self.show_section(self.section + 1)))
            self.label = apply_type(Gtk.Label(), 'body')
            navigation.append(self.previous)
            navigation.append(self.label)
            navigation.append(self.next)
            self.append(self.web)
            self.append(navigation)

        def show_section(self, section):
            if self.closed:
                return
            self.section = max(0, min(section, len(book.sections) - 1))
            self.previous.set_sensitive(self.section > 0)
            self.next.set_sensitive(self.section < len(book.sections) - 1)
            self.label.set_label(f'Section {self.section + 1} of {len(book.sections)}')
            self.web.load_uri('leaf://reader/book/preview/' + quote(book.sections[self.section].href, safe='/'))
            on_section(self.section)

        def _policy(self, web, decision, kind):
            if kind in (WebKit.PolicyDecisionType.NAVIGATION_ACTION, WebKit.PolicyDecisionType.NEW_WINDOW_ACTION):
                uri = urlparse(decision.get_navigation_action().get_request().get_uri())
                prefix = '/book/preview/'
                allowed = (kind != WebKit.PolicyDecisionType.NEW_WINDOW_ACTION and uri.scheme == 'leaf'
                           and uri.netloc == 'reader' and uri.path.startswith(prefix)
                           and book.has(unquote(uri.path[len(prefix):])))
                if not allowed:
                    decision.ignore()
                    return True
                name = unquote(uri.path[len(prefix):])
                section = next((s.index for s in book.sections if s.href == name), None)
                if section is not None:
                    self.section = section
                    self.previous.set_sensitive(section > 0)
                    self.next.set_sensitive(section < len(book.sections) - 1)
                    self.label.set_label(f'Section {section + 1} of {len(book.sections)}')
                    on_section(section)
            return False

        def _loaded(self, web, event):
            if not self.closed and event == WebKit.LoadEvent.FINISHED:
                on_ready()

        def _failed(self, web, event, uri, error):
            if not self.closed:
                on_error(error.message)
            return True

        def _terminated(self, web, reason):
            if not self.closed:
                on_error('The book renderer stopped. Reopen the file to try again.')

        def clear(self):
            self.closed = True
            self.web.stop_loading()
            self.scheme.forget('preview')
            book.zip.close()

    return BookPreview()
