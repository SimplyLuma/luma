# SPDX-License-Identifier: GPL-3.0-only
import unittest
from types import SimpleNamespace
from native_address import NativeAddress


class AddressIconTest(unittest.TestCase):
    def adapter(self):
        adapter = NativeAddress.__new__(NativeAddress)
        shown, requested = [], []
        entry = SimpleNamespace(
            set_icon_from_icon_name=lambda _, icon: shown.append(icon),
            set_icon_from_paintable=lambda _, icon: shown.append(icon),
            set_icon_tooltip_text=lambda *_: None)
        adapter.host = SimpleNamespace(address=entry, state={'activeTabId':'tab','activeConnection':{'kind':'secure'}},
            address_focused=lambda: True, favicons=SimpleNamespace(request=lambda url, cb: requested.append((url,cb))))
        adapter.owns_focus = lambda: False
        adapter.generation = 1
        adapter.search_destination = adapter.search_texture = None
        return adapter, shown, requested

    def test_classifier_search_uses_destination_origin_and_cached_favicon(self):
        a, shown, requests = self.adapter()
        a.update_search_icon(1,'tab',[{'type':'tab'}, {'type':'search','url':'https://duckduckgo.com/?q=private+query'}])
        self.assertEqual(requests[0][0], 'https://duckduckgo.com/')
        self.assertEqual(shown[-1], 'system-search-symbolic')
        texture = object(); requests[0][1](texture)
        self.assertIs(shown[-1],texture)
        a.refresh_icon(); self.assertIs(shown[-1],texture)

    def test_url_with_alternate_search_does_not_show_engine(self):
        a, shown, requests = self.adapter()
        a.update_search_icon(1,'tab',[{'type':'url','url':'https://example.com'}, {'type':'search','url':'https://google.com/search?q=example.com'}])
        a.refresh_icon()
        self.assertEqual(shown[-1],'channel-secure-symbolic')
        self.assertFalse(requests)

    def test_late_favicon_cannot_replace_new_input_or_other_tab(self):
        for change in ('edit','tab','blur'):
            a, shown, requests = self.adapter()
            a.update_search_icon(1,'tab',[{'type':'search','url':'https://google.com/search?q=test'}])
            if change=='edit': a.generation+=1
            elif change=='tab': a.host.state['activeTabId']='other'
            else: a.host.address_focused=lambda:False
            requests[0][1](object())
            self.assertIsNone(a.search_texture)

    def test_typing_retains_favicon_until_classifier_changes_to_url(self):
        a, shown, requests = self.adapter()
        a.restoring_focus, a.timer = False, None
        a.host.services = None
        a.host.address.get_text = lambda: 'cats and dogs'
        a.update_search_icon(1,'tab',[{'type':'search','url':'https://google.com/search?q=cats'}])
        texture = object(); requests[0][1](texture)
        shown.clear()
        for _ in range(3):
            a.changed()
            a.update_search_icon(a.generation,'tab',[{'type':'search','url':'https://google.com/search?q=cats+and+dogs'}])
        self.assertTrue(shown)
        self.assertTrue(all(icon is texture for icon in shown))
        self.assertEqual(len(requests),1)
        a.update_search_icon(a.generation,'tab',[{'type':'url','url':'https://example.com'}])
        self.assertEqual(shown[-1],'channel-secure-symbolic')

    def test_empty_input_resets_search_icon_immediately(self):
        a, shown, requests = self.adapter()
        a.restoring_focus, a.timer = False, None
        a.host.services = None
        a.host.address.get_text = lambda: ''
        a.update_search_icon(1,'tab',[{'type':'search','url':'https://google.com/search?q=cats'}])
        a.changed()
        self.assertIsNone(a.search_destination)
        self.assertEqual(shown[-1],'channel-secure-symbolic')

    def test_blur_restores_connection_icon(self):
        a, shown, requests = self.adapter()
        a.update_search_icon(1,'tab',[{'type':'search','url':'https://google.com/search?q=test'}])
        a.host.address_focused=lambda:False
        a.refresh_icon()
        self.assertEqual(shown[-1],'channel-secure-symbolic')

if __name__ == '__main__': unittest.main()
