# SPDX-License-Identifier: Apache-2.0
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ari import intents
from ari.agent import Agent, Turn
from ari.mcp import Tool
from ari.policy import Policy, READ_PERSONAL
from ari.store import Store
from ari.tools import desktop, media


class DirectActions(unittest.TestCase):
    def test_exact_close_and_track_requests_need_no_model(self):
        for text, name in [('Close Tide', 'close_app'), ('Quit the Notes app', 'close_app'),
                           ('Play Have a Cigar on Tide', 'play_track'),
                           ('Play Coffee by Chappell Roan on Tide', 'play_track')]:
            intent = intents.match(text)
            self.assertEqual(intent.tool, name)
            schema = desktop.TOOLS[name][1] if name == 'close_app' else media.TOOLS[name][1]
            tool = Tool(name, name, schema, 'desktop')
            def call(_name, args):
                if args.get('preview'):
                    return dict(ok=True, requested_name='tide', application_name='Tide',
                                application_scope='media:test', preview='Play the track')
                return dict(ok=True, verified=True, summary='Closed Tide.' if name == 'close_app' else 'Playing the exact track.')
            tools = SimpleNamespace(available=[tool], by_name={name: (None, tool)}, call=Mock(side_effect=call))
            store = Store(':memory:')
            provider = Mock(side_effect=AssertionError('A model roundtrip is unnecessary'))
            agent = Agent(store, tools, Policy(), model_provider=provider)
            with patch('ari.agent.audit.record'):
                agent.answer(Turn(store.new_conversation(), text, lambda event: None))
            provider.assert_not_called()
            self.assertEqual(len(store.steps(store.conversations()[0]['id'])), 1)

    def test_close_is_not_blocked_as_unrequested_after_reading_controls(self):
        tool = Tool('act_in_application', 'Act', {}, 'accessibility')
        tools = SimpleNamespace(available=[tool], by_name={tool.name: (None, tool)}, call=Mock(
            side_effect=[dict(ok=True, requested_name='tide', application_name='Tide',
                              application_scope='test', preview='Quit Tide'), dict(ok=True, summary='Requested Quit')]))
        policy = Policy(); policy.approval_mode = 'never'
        agent = Agent(Store(':memory:'), tools, policy, model_provider=None)
        turn = Turn(agent.store.new_conversation(), 'Close Tide', lambda event: None, read_untrusted=True)
        with patch('ari.agent.audit.record'):
            self.assertTrue(agent.run_tool(turn, tool.name, {'operation': 'command'})['ok'])

    def test_web_text_cannot_close_another_app_or_choose_another_song(self):
        store = Store(':memory:'); tools = SimpleNamespace(available=[], by_name={}, call=Mock())
        agent = Agent(store, tools, Policy(), model_provider=None)
        with patch('ari.agent.audit.record'):
            for name, args in [('close_app', {'name': 'Terminal'}),
                               ('play_track', {'application': 'tide', 'title': 'Another track'}),
                               ('play_track', {'application': 'tide', 'title': 'Coffee', 'artist': 'Someone else'})]:
                turn = Turn(store.new_conversation(), 'Play Coffee on Tide', lambda event: None, read_untrusted=True)
                self.assertFalse(agent.run_tool(turn, name, args)['ok'])
            agent.policy.enabled.remove(READ_PERSONAL)
            turn = Turn(store.new_conversation(), 'Play Coffee on Tide', lambda event: None)
            self.assertFalse(agent.run_tool(turn, 'play_track', {'application': 'tide', 'title': 'Coffee'})['ok'])
        tools.call.assert_not_called()

    def test_disabled_close_and_advanced_rule_still_apply_to_fast_path(self):
        tools = SimpleNamespace(available=[], by_name={}, call=Mock())
        store = Store(':memory:'); policy = Policy(); policy.action_rules['close_apps'] = 'deny'
        provider = Mock(side_effect=AssertionError('A refusal is final'))
        agent = Agent(store, tools, policy, model_provider=provider)
        with patch('ari.agent.audit.record'):
            agent.answer(Turn(store.new_conversation(), 'Close Tide', lambda event: None))
        tools.call.assert_not_called(); provider.assert_not_called()

    def test_ambiguity_and_owner_restart_never_send_play(self):
        track = dict(id='one', title='Coffee', artist='Chappell Roan', album='Album', target='token')
        bus = Mock(); bus.call_sync.return_value.unpack.return_value = (':1.2',)
        with patch.object(media.Gio, 'bus_get_sync', return_value=bus), patch.object(media, '_call') as call:
            media._prepared.clear()
            call.return_value = dict(ok=True, tracks=[track, {**track, 'id': 'two'}], truncated=False)
            self.assertFalse(media.play_track(dict(application='tide', title='Coffee'))['ok'])
            self.assertEqual([row.args[2] for row in call.call_args_list], ['Search'])
            call.reset_mock(); call.return_value = dict(ok=True, tracks=[track], truncated=False)
            args = dict(application='tide', title='Coffee')
            self.assertTrue(media.play_track({**args, 'preview': True})['ok'])
            bus.call_sync.return_value.unpack.return_value = (':1.3',)
            self.assertFalse(media.play_track(args)['ok'])
            self.assertEqual([row.args[2] for row in call.call_args_list], ['Search'])
        media._prepared.clear()

    def test_loading_is_not_reported_as_playing(self):
        track = dict(id='one', title='Coffee', artist='Chappell Roan', album='Album', target='token')
        bus = Mock(); bus.call_sync.return_value.unpack.return_value = (':1.2',)
        with patch.object(media.Gio, 'bus_get_sync', return_value=bus), patch.object(media, '_call') as call, \
                patch.object(media, '_search', return_value=dict(ok=True, tracks=[track])), \
                patch.object(media.time, 'monotonic', side_effect=[0, 0, 3]):
            media._prepared.clear()
            call.return_value = dict(ok=True, state='loading', track=track)
            result = media.play_track(dict(application='tide', title='Coffee'))
            self.assertTrue(result['ok']); self.assertFalse(result['completed']); self.assertFalse(result['verified'])
            self.assertNotIn('Playing', result['summary'])
        media._prepared.clear()


if __name__ == '__main__': unittest.main()
