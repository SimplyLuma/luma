# SPDX-License-Identifier: Apache-2.0
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
from ari import chatgpt
from ari.policy import Policy, OPEN_APPS, PERSONAL_SETTINGS
from ari.store import Store
from ari.tools import answers
from ari_ui import rich_text, live_data


class Parity(unittest.TestCase):
    def test_markdown_escapes_markup_and_rejects_active_urls(self):
        markup = rich_text.inline('<span foreground="red">unsafe</span> **bold** `x < y` [site](https://example.org) [bad](javascript:alert)')
        self.assertIn('&lt;span', markup)
        self.assertNotIn('<span foreground=', markup)
        self.assertIn('<b>bold</b>', markup)
        self.assertIn('<tt>x &lt; y</tt>', markup)
        self.assertIn('<a href="https://example.org">site</a>', markup)
        self.assertNotIn('href="javascript:', markup)
        self.assertFalse(rich_text.safe_url('file:///etc/passwd'))
        self.assertFalse(rich_text.safe_url('https://name:password@example.org'))

    def test_code_and_list_copy_stays_plain(self):
        value = '* **One**\n2. Two\n\n```python\nprint("<tag>")\n```'
        result = list(rich_text.blocks(value))
        self.assertIn('• <b>One</b>', result[0][0])
        self.assertEqual(result[1], ('<tt>print(&quot;&lt;tag&gt;&quot;)</tt>', True))
        self.assertNotIn('<b>', rich_text.plain_text(value))
        self.assertIn('print("<tag>")', rich_text.plain_text(value))

    def test_no_wikipedia_fallback_for_web_search(self):
        with patch.object(answers, '_config', return_value={}), patch.object(answers, '_get') as get:
            self.assertFalse(answers.web_search({'query':'Fedora next release'})['ok'])
        get.assert_not_called()

    def test_open_apps_has_separate_permission_without_approval(self):
        policy = Policy()
        policy.approval_mode = 'all'
        self.assertEqual(policy.tier('open_app'), OPEN_APPS)
        self.assertFalse(policy.needs_approval('open_app'))
        policy.enabled.discard(PERSONAL_SETTINGS)
        self.assertTrue(policy.check('open_app', calls_this_turn=0, after_untrusted=False).allowed)
        policy.enabled.discard(OPEN_APPS)
        self.assertFalse(policy.check('open_app', calls_this_turn=0, after_untrusted=False).allowed)

    def test_cancel_discards_partial_without_removing_an_installed_model(self):
        from ari import models
        with tempfile.TemporaryDirectory() as directory:
            model = models.catalogue()[0]
            target = model.path(Path(directory))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'installed')
            verified = target.with_suffix('.verified')
            verified.write_text('published-checksum')
            partial = target.with_suffix(target.suffix + '.part')
            partial.write_bytes(b'incomplete')
            models.discard_partial(model, Path(directory))
            self.assertFalse(partial.exists())
            self.assertEqual(target.read_bytes(), b'installed')
            self.assertEqual(verified.read_text(), 'published-checksum')

    def test_model_capabilities_are_dynamic(self):
        row = chatgpt._model_row({'slug':'future-model', 'display_name':'Future',
            'supported_reasoning_levels':[{'effort':'low'}, {'effort':'ultra'}, {'effort':'<bad>'}],
            'default_reasoning_level':'ultra'})
        self.assertEqual(row['supported_reasoning_levels'], [{'effort':'low'}, {'effort':'ultra'}])
        self.assertEqual(row['default_reasoning_level'], 'ultra')
        self.assertNotIn('supported_reasoning_levels', chatgpt._model_row({'slug':'basic','display_name':'Basic'}))

    def test_receipts_use_apps_category_and_no_tool_identifier(self):
        node, category = live_data.action_node('open_app', 'Opened Weather')
        self.assertEqual(category, 'Apps')
        self.assertEqual(node['k'], 'app')
        self.assertNotIn('open_app', str(node))

    def test_title_ends_at_a_word_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory)/'ari.db')
            identity = store.new_conversation()
            store.rename_if_new(identity, 'When does the next version of Fedora finally release this year?')
            title = store.conversations()[0]['title']
            self.assertTrue(title.endswith('…'))
            self.assertIn(title[:-1], 'When does the next version of Fedora finally release this year?')
            self.assertNotIn(' rel…', title)
            store.db.close()

    def test_open_app_is_one_stored_receipt_and_no_duplicate_reply(self):
        from ari.agent import Agent, Turn
        from ari.mcp import Tool
        from unittest.mock import Mock
        tool = Tool('open_app', 'Open an app', {'type':'object'}, 'desktop')
        tools = SimpleNamespace(available=[tool], by_name={'open_app':(None,tool)},
                                call=Mock(return_value={'ok':True,'summary':'Opened Weather'}))
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory)/'ari.db')
            events = []
            policy = Policy()
            policy.approval_mode = 'all'
            agent = Agent(store, tools, policy, model_provider=None)
            conversation = store.new_conversation()
            with patch('ari.agent.audit.record'):
                agent.answer(Turn(conversation, 'open my Weather app', events.append))
            tools.call.assert_called_once_with('open_app', {'name':'Weather'})
            self.assertFalse(any(event['type']=='approval' for event in events))
            self.assertEqual(sum(event['type']=='step' for event in events), 1)
            self.assertEqual(events[-1]['text'], '')
            self.assertEqual(len(store.steps(conversation)), 1)
            record = {'messages':store.messages(conversation),'steps':store.steps(conversation)}
            self.assertEqual(len(live_data.stored_messages(record)), 2)
            store.db.close()

    def test_web_results_cannot_change_the_app_the_person_requested(self):
        from ari.agent import Agent, Turn
        from ari.mcp import Tool
        from unittest.mock import Mock
        tool = Tool('open_app', 'Open an app', {'type':'object'}, 'desktop')
        tools = SimpleNamespace(available=[tool], call=Mock(return_value={'ok':True, 'summary':'Opened Weather'}))
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory)/'ari.db')
            agent = Agent(store, tools, Policy(), model_provider=None)
            turn = Turn(store.new_conversation(), 'Search the forecast and then open Weather', lambda event:None, read_untrusted=True)
            with patch('ari.agent.audit.record'):
                self.assertFalse(agent.run_tool(turn, 'open_app', {'name':'Terminal'})['ok'])
                self.assertTrue(agent.run_tool(turn, 'open_app', {'name':'Weather'})['ok'])
            tools.call.assert_called_once_with('open_app', {'name':'Weather'})
            store.db.close()

    def test_task_overrides_select_a_provider_without_changing_the_default(self):
        from ari.service import Daemon
        from unittest.mock import Mock
        daemon = Daemon.__new__(Daemon)
        daemon.config = {'brain':'local', 'task_models':{'code':'chatgpt:fixture', 'writing':'local-writing'}}
        daemon._model = Mock(return_value='provider')
        self.assertEqual(daemon._model_for('Debug this Python function'), 'provider')
        daemon._model.assert_called_with('chatgpt:fixture')
        daemon._model_for('Write an email')
        daemon._model.assert_called_with('local-writing')
        daemon._model_for('What is two plus two?')
        daemon._model.assert_called_with(None)
        self.assertEqual(daemon.config['brain'], 'local')

    def test_task_model_does_not_interrupt_another_local_answer(self):
        import threading
        from unittest.mock import Mock
        from ari import models
        from ari.service import Daemon
        daemon = Daemon.__new__(Daemon)
        daemon.catalogue = models.catalogue()
        daemon.config = {}
        daemon._runtime_selection_lock = threading.Lock()
        daemon.requests = {'first':object(), 'second':object()}
        first, second = daemon.catalogue[:2]
        daemon.runtime = Mock(model=first, base_url='http://127.0.0.1:4321/v1', key='fixture')
        daemon.runtime.running.return_value = True
        daemon.hardware = Mock()
        with patch.object(models, 'installed', return_value=daemon.catalogue):
            with self.assertRaisesRegex(LookupError, 'one local model at a time'):
                daemon._model(second.id)
            daemon.runtime.ensure.assert_not_called()
            daemon.requests.pop('first')
            daemon._model(second.id)
            daemon.runtime.ensure.assert_called_once_with(second)

    def test_approved_change_carries_its_approval_identity_to_the_receipt(self):
        from ari.agent import Agent, Turn
        from ari.mcp import Tool
        from unittest.mock import Mock
        tool = Tool('set_theme', 'Change theme', {'type':'object'}, 'desktop')
        tools = SimpleNamespace(available=[tool], by_name={'set_theme':(None,tool)},
                                call=Mock(return_value={'ok':True,'summary':'Changed theme','undo':{'tool':'set_theme','arguments':{'theme':'dark'}}}))
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory)/'ari.db')
            events = []
            policy = Policy()
            policy.approval_mode = 'all'
            agent = Agent(store, tools, policy, model_provider=None)
            def receive(event):
                events.append(event)
                if event['type']=='approval':
                    agent.approve(event['approval'], True)
            with patch('ari.agent.audit.record'):
                agent.answer(Turn(store.new_conversation(), 'use dark mode', receive))
            approval = next(event['approval'] for event in events if event['type']=='approval')
            self.assertEqual(next(event['approval'] for event in events if event['type']=='step'), approval)
            store.db.close()
