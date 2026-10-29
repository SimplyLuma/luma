# SPDX-License-Identifier: Apache-2.0
import copy
import io
import json
import threading
import tempfile
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ari.chatgpt_provider import ChatGPTProvider


def stream(*events):
    return io.BytesIO(b"".join(b"data: " + json.dumps(event).encode() + b"\n\n" for event in events))


def completed(output):
    return {"type": "response.completed", "response": {"status": "completed", "output": output}}


class Responses(unittest.TestCase):
    def test_hosted_search_replaces_local_web_tools_and_preserves_citations(self):
        self.tools += [{"type":"function", "function":{"name":name,"parameters":{}}} for name in ('web_search','fetch_page','wikipedia')]
        self.responses.append(stream({'type':'response.web_search_call.searching'},
            {'type':'response.output_text.delta', 'delta':'Verified source'}, completed([
                {'type':'web_search_call','status':'completed'},
                {'type':'message','content':[{'type':'output_text','text':'Verified source', 'annotations':[
                    {'type':'url_citation','url':'https://example.org/fedora','title':'Fedora','start_index':9,'end_index':15}]}]}])))
        events = list(self.provider.chat(self.messages, self.tools))
        names = [tool['name'] for tool in self.requests[-1][1]['body']['tools'][0]['tools']]
        self.assertEqual(names, ['now'])
        self.assertEqual(self.requests[-1][1]['body']['tools'][-1], {'type':'web_search'})
        self.assertIn({'type':'working','label':'Searching the web…'}, events)
        self.assertIn({'type':'final_text','text':'Verified [Fedora](https://example.org/fedora)'}, events)
        self.assertIn({'type':'sources','sources':[{'url':'https://example.org/fedora','title':'Fedora'}]}, events)

    def test_reasoning_is_sent_only_when_provided_by_validated_selection(self):
        self.provider.reasoning = 'high'
        self.responses.append(stream(completed([])))
        list(self.provider.chat(self.messages))
        self.assertEqual(self.requests[-1][1]['body']['reasoning'], {'effort':'high'})
        self.provider.reasoning = None
        self.messages[-1]['content'] = 'A different question'
        self.responses.append(stream(completed([])))
        list(self.provider.chat(self.messages))
        self.assertNotIn('reasoning', self.requests[-1][1]['body'])

    def setUp(self):
        self.requests = []
        self.responses = []
        def opened(url, **kwargs):
            self.requests.append((url, copy.deepcopy(kwargs)))
            return self.responses.pop(0)
        self.provider = ChatGPTProvider(SimpleNamespace(access=lambda profile: "fixture-token",
                                        transport=SimpleNamespace(open=opened)), "fixture-profile", "fixture-model")
        self.messages = [{"role": "system", "content": "Ari's rules"}, {"role": "user", "content": "Help"}]
        self.tools = [{"type": "function", "function": {"name": "now", "description": "Read the time",
                                                        "parameters": {"type": "object", "properties": {}}}}]

    def test_subscription_request_uses_supported_fields_and_namespace_tools(self):
        self.responses.append(stream({"type": "response.output_text.delta", "delta": "Hello"}, completed([])))
        self.assertEqual(list(self.provider.chat(self.messages, self.tools))[0], {"type": "text", "text": "Hello"})
        url, request = self.requests[0]
        self.assertEqual(url, "https://api.openai.com/v1/responses")
        body = request["body"]
        self.assertIs(body["store"], False)
        self.assertIs(body["stream"], True)
        self.assertEqual(body["instructions"], "Ari's rules")
        self.assertEqual(body["input"], [{"role": "user", "content": "Help"}])
        self.assertEqual(body["tools"][0]["type"], "namespace")
        self.assertEqual(body["tools"][0]["name"], "ari")
        self.assertFalse(set(body) & {"temperature", "max_output_tokens", "max_tokens", "previous_response_id", "conversation"})

    def test_hosted_search_honors_the_answer_tools_permission(self):
        self.provider.allow_search = False
        self.responses.append(stream(completed([])))
        list(self.provider.chat(self.messages))
        self.assertNotIn('tools', self.requests[0][1]['body'])

    def test_tool_calls_require_terminal_completion_and_valid_arguments(self):
        call = {"type": "function_call", "call_id": "call", "name": "now", "namespace": "ari", "arguments": "{}"}
        for ending in ({"type": "response.failed", "response": {"error": {"code": "subscription_sharing_usage_limit_exceeded"}}},
                       {"type": "response.incomplete"}, None):
            events = [{"type": "response.output_item.done", "item": call}]
            if ending:
                events.append(ending)
            self.responses.append(stream(*events))
            result = list(self.provider.chat(self.messages, self.tools))
            self.assertTrue(any(e["type"] == "error" for e in result))
            self.assertFalse(any(e["type"] == "tool_call" for e in result))
        self.responses.append(stream(completed([call])))
        self.assertEqual(list(self.provider.chat(self.messages, self.tools)),
                         [{"type": "tool_call", "id": "call", "name": "now", "arguments": {}}])

    def test_malformed_or_foreign_tool_calls_fail_before_any_calls_are_released(self):
        good = {"type": "function_call", "call_id": "good", "name": "now", "arguments": "{}"}
        for bad in ({**good, "call_id": "bad", "arguments": "[]"}, {**good, "namespace": "other"},
                    {**good, "arguments": "{broken"}, {**good, "call_id": ""}, dict(good)):
            self.responses.append(stream(completed([good, bad])))
            result = list(self.provider.chat(self.messages, self.tools))
            self.assertEqual(result[-1]["type"], "error")
            self.assertFalse(any(e["type"] == "tool_call" for e in result))

    def test_reasoning_and_namespace_are_preserved_through_tool_continuation(self):
        reasoning = {"type": "reasoning", "id": "r", "summary": [], "encrypted_content": "fixture-encrypted-reasoning"}
        call = {"type": "function_call", "call_id": "call", "name": "now", "namespace": "ari", "arguments": "{}"}
        self.responses.append(stream(completed([reasoning, call])))
        list(self.provider.chat(self.messages, self.tools))
        self.messages += [{"role": "assistant", "content": None, "tool_calls": [{"id": "call", "type": "function",
                            "function": {"name": "now", "arguments": "{}"}}]},
                          {"role": "tool", "tool_call_id": "call", "content": "Test time"}]
        self.responses.append(stream(completed([{"type": "message", "role": "assistant", "phase": "final_answer",
                                                "content": [{"type": "output_text", "text": "Done"}]}])))
        self.assertEqual(list(self.provider.chat(self.messages, self.tools)), [{"type": "text", "text": "Done"}])
        inputs = self.requests[-1][1]["body"]["input"]
        self.assertIn(reasoning, inputs)
        self.assertEqual(sum(i.get("type") == "function_call" for i in inputs), 1)
        self.assertEqual(inputs[-1], {"type": "function_call_output", "call_id": "call", "output": "Test time"})

    def test_cancelled_request_never_starts_inference(self):
        stop = threading.Event()
        stop.set()
        self.assertEqual(list(self.provider.chat(self.messages, stop=stop)), [])
        self.assertEqual(self.requests, [])

    def test_late_limit_error_has_usage_guidance_and_no_remote_error_text(self):
        self.responses.append(stream({"type": "response.output_text.delta", "delta": "Unfinished"},
                                     {"type": "response.failed", "response": {"error": {
                                         "code": "subscription_sharing_usage_limit_exceeded", "message": "fixture-secret"}}}))
        result = list(self.provider.chat(self.messages))
        self.assertEqual(result[-1]["type"], "error")
        self.assertIn("Manage usage", result[-1]["message"])
        self.assertNotIn("fixture-secret", str(result))

    def test_agent_discards_incomplete_text_and_never_runs_a_failed_streams_tool(self):
        from ari.agent import Agent, Turn
        from ari.policy import Policy
        from ari.store import Store
        self.responses.append(stream({"type": "response.output_text.delta", "delta": "Unfinished reply"},
                                     {"type": "response.failed", "response": {"error": {
                                         "code": "subscription_sharing_usage_limit_exceeded"}}}))
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "ari.db")
            tools = SimpleNamespace(available=[], by_name={}, call=Mock())
            events = []
            engine = Agent(store, tools, Policy(), model_provider=lambda: (self.provider, "Test", "through your ChatGPT plan", "unknown", "large"))
            turn = Turn(store.new_conversation(), "Explain clouds", events.append)
            with patch('ari.agent._current_settings', return_value=''):
                engine.answer(turn)
            self.assertIn({"type": "discard_text"}, events)
            self.assertIn("Manage usage", events[-1]["text"])
            self.assertNotIn("Unfinished", store.messages(turn.conversation)[-1]["content"])
            tools.call.assert_not_called()
            store.db.close()

    def test_subscription_tool_call_still_requires_aris_policy_permission(self):
        from ari.agent import Agent, Turn
        from ari.mcp import Tool
        from ari.policy import Policy, PERSONAL_SETTINGS
        from ari.store import Store
        call = {"type": "function_call", "call_id": "c", "name": "set_theme", "namespace": "ari", "arguments": '{"theme":"dark"}'}
        self.responses.extend([stream(completed([call])), stream(completed([
            {"type": "message", "content": [{"type": "output_text", "text": "Permission was refused."}]}]))])
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "ari.db")
            tool = Tool('set_theme', 'Change the theme', {'type': 'object'}, 'test')
            tools = SimpleNamespace(available=[tool], by_name={'set_theme': (None, tool)}, call=Mock())
            permission = Policy()
            permission.enabled.discard(PERSONAL_SETTINGS)
            engine = Agent(store, tools, permission, model_provider=lambda: (self.provider, 'Test', 'through your ChatGPT plan', 'unknown', 'large'))
            with patch('ari.agent._current_settings', return_value=''), patch('ari.agent.audit.record'):
                engine.answer(Turn(store.new_conversation(), 'Discuss the dark theme', lambda event: None))
            tools.call.assert_not_called()
            self.assertEqual(len(self.requests), 2)
            self.assertIn('turned off', self.requests[-1][1]['body']['input'][-1]['output'])
            store.db.close()

    def test_completed_without_reply_or_action_reports_the_real_failure(self):
        self.responses.append(stream(completed([{'type':'reasoning'}])))
        events=list(self.provider.chat(self.messages))
        self.assertEqual(events[0]['type'],'error')
        self.assertIn('without returning a reply or an application action',events[0]['message'])

    def test_refusal_text_is_not_lost_as_an_empty_answer(self):
        self.responses.append(stream(completed([{'type':'message','content':[{'type':'refusal','refusal':'I cannot perform that action.'}]}])))
        self.assertIn({'type':'text','text':'I cannot perform that action.'},list(self.provider.chat(self.messages)))

    def test_done_items_survive_an_empty_terminal_summary_and_continue_the_tool_loop(self):
        reasoning = {'type': 'reasoning', 'id': 'reason', 'encrypted_content': 'private-reasoning'}
        call = {'type': 'function_call', 'status': 'completed', 'call_id': 'call', 'namespace': 'ari',
                'name': 'now', 'arguments': '{}'}
        self.responses.append(stream(
            {'type': 'response.output_item.done', 'output_index': 0, 'item': reasoning},
            {'type': 'response.output_item.done', 'output_index': 1, 'item': call}, completed([])))
        self.assertEqual(list(self.provider.chat(self.messages, self.tools)),
                         [{'type': 'tool_call', 'id': 'call', 'name': 'now', 'arguments': {}}])
        self.messages += [{'role': 'assistant', 'content': None, 'tool_calls': [
            {'id': 'call', 'function': {'name': 'now', 'arguments': '{}'}}]},
            {'role': 'tool', 'tool_call_id': 'call', 'content': 'The current time'}]
        self.responses.append(stream(completed([{'type': 'message', 'content': [
            {'type': 'output_text', 'text': 'The answer'}]}])))
        self.assertEqual(list(self.provider.chat(self.messages, self.tools)), [{'type': 'text', 'text': 'The answer'}])
        inputs = self.requests[-1][1]['body']['input']
        self.assertIn(reasoning, inputs)
        self.assertEqual(sum(item.get('type') == 'function_call' for item in inputs), 1)
        self.assertEqual(inputs[-1]['call_id'], 'call')

    def test_done_message_keeps_citations_when_terminal_summary_is_empty(self):
        message = {'type': 'message', 'status': 'completed', 'content': [
            {'type': 'output_text', 'text': 'Read this', 'annotations': [
                {'type': 'url_citation', 'url': 'https://example.org', 'title': 'Source',
                 'start_index': 5, 'end_index': 9}]}]}
        self.responses.append(stream({'type': 'response.output_item.done', 'output_index': 0, 'item': message}, completed([])))
        result = list(self.provider.chat(self.messages))
        self.assertIn({'type': 'text', 'text': 'Read [Source](https://example.org)'}, result)
        self.assertIn({'type': 'sources', 'sources': [{'title': 'Source', 'url': 'https://example.org'}]}, result)

    def test_terminal_output_does_not_duplicate_individual_done_calls(self):
        call = {'type': 'function_call', 'call_id': 'call', 'name': 'now', 'arguments': '{}'}
        self.responses.append(stream({'type': 'response.output_item.done', 'output_index': 0, 'item': call}, completed([call])))
        self.assertEqual(len([event for event in self.provider.chat(self.messages, self.tools)
                              if event['type'] == 'tool_call']), 1)

    def test_incomplete_or_conflicting_done_sequences_never_release_actions(self):
        call = {'type': 'function_call', 'call_id': 'call', 'name': 'now', 'arguments': '{}'}
        good = {'type': 'response.output_item.done', 'output_index': 0, 'item': call}
        for events in ([{**good, 'output_index': 1}], [{**good, 'output_index': True}],
                       [good, {**good, 'item': {**call, 'arguments': '{"different":true}'}}],
                       [{**good, 'item': {**call, 'status': 'incomplete'}}]):
            self.responses.append(stream(*events, completed([])))
            result = list(self.provider.chat(self.messages, self.tools))
            self.assertEqual(result[-1]['type'], 'error')
            self.assertFalse(any(event['type'] == 'tool_call' for event in result))

    def test_empty_completion_diagnostics_never_log_remote_data(self):
        self.responses.append(stream({'type': 'private-event-name', 'text': 'private-text'}, completed([
            {'type': 'private-item-name', 'arguments': 'private-arguments', 'id': 'private-id'},
            {'type': 'reasoning', 'encrypted_content': 'private-reasoning'}])))
        with self.assertLogs('ari.chatgpt_provider', level='WARNING') as logs:
            self.assertEqual(list(self.provider.chat(self.messages))[-1]['type'], 'error')
        diagnostic = '\n'.join(logs.output)
        self.assertNotIn('private-', diagnostic)
        self.assertIn('terminal_items=2', diagnostic)
        self.assertIn("'other': 1", diagnostic)

    def test_empty_text_parts_are_not_a_successful_reply(self):
        self.responses.append(stream({'type': 'response.output_text.delta', 'delta': ''}, completed([
            {'type': 'message', 'content': [{'type': 'output_text', 'text': ''}]}])))
        self.assertEqual(list(self.provider.chat(self.messages))[-1]['type'], 'error')


if __name__ == "__main__":
    unittest.main()
