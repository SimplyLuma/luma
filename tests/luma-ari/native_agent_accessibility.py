# SPDX-License-Identifier: Apache-2.0
"""A scripted Responses stream drives the real agent, approvals and Weather MCP.

No hosted model or account is used. This verifies transport/tool integration;
only manual account testing can establish actual model decisions.
"""
import io
import json
import os
import time
from types import SimpleNamespace
from unittest.mock import patch

from ari.agent import Agent, Turn
from ari.chatgpt_provider import ChatGPTProvider
from ari.policy import Policy
from ari.store import Store


def exercise(client, directory):
    state = {'stage': 0, 'requests': 0, 'remove':False}

    def opened(_url, *, body, **_kwargs):
        state['requests'] += 1
        stage = state['stage']
        if stage:
            last = body['input'][-1]
            assert last['type'] == 'function_call_output', last
            assert 'Succeeded:' in last['output'], last
            details = last['output'].partition('Details: ')[2]
            data = json.loads(details) if details else None
        if state['remove'] and stage == 1:
            state['application']=data[0]['application']
            command=next(row for row in data[0]['snapshot']['commands'] if row['name']=='weather-remove')
            name,args='act_in_application',{'application':state['application'], 'target':command['target'], 'operation':'command'}
        elif state['remove'] and stage == 2:
            assert not any(row['name'].casefold()=='chicago' for row in data['controls']), data
            assert any(row['name']=='Removed Chicago' for row in data['controls']), data
            name,args=None,None
        elif stage == 0:
            name, args = 'desktop_applications', {'name': 'Weather'}
        elif stage == 1:
            state['application'] = data[0]['application']
            name, args = 'inspect_application', {'application': state['application']}
        elif stage == 2:
            field = next((row for row in data['controls'] if row['editable']), None)
            if field is None:
                state['stage'] -= 1
                time.sleep(.15)
                name, args = 'inspect_application', {'application': state['application']}
            else:
                name, args = 'act_in_application', {'application': state['application'], 'target': field['target'],
                                                   'operation': 'set_text', 'text': 'Chicago'}
        elif stage == 3:
            name, args = 'inspect_application', {'application': state['application']}
        elif stage == 4:
            result = next((row for row in data['controls'] if 'chicago' in row['name'].casefold() and row['actions']), None)
            if result is None:
                state['stage'] -= 1
                time.sleep(.15)
                name, args = 'inspect_application', {'application': state['application']}
            else:
                name, args = 'act_in_application', {'application': state['application'], 'target': result['target'],
                                                   'operation': 'action', 'action': result['actions'][0]}
        elif stage == 5:
            name, args = 'inspect_application', {'application': state['application']}
        else:
            assert any('chicago' in row['name'].casefold() for row in data['controls']), data
            name, args = None, None
        state['stage'] += 1
        item = ({'type': 'function_call', 'status': 'completed', 'namespace': 'ari', 'name': name,
                 'call_id': f"call-{state['requests']}", 'arguments': json.dumps(args)} if name else
                {'type': 'message', 'status': 'completed', 'role': 'assistant',
                 'content': [{'type': 'output_text', 'text': 'Removed Chicago from Weather.' if state['remove'] else 'Added Chicago to Weather.'}]})
        # Individual final output is intentionally absent from the terminal summary.
        events = [{'type': 'response.output_item.done', 'output_index': 0, 'item': item},
                  {'type': 'response.completed', 'response': {'status': 'completed', 'output': []}}]
        return io.BytesIO(b''.join(b'data: ' + json.dumps(event).encode() + b'\n\n' for event in events))

    provider = ChatGPTProvider(SimpleNamespace(access=lambda _profile: 'fixture-only',
        transport=SimpleNamespace(open=opened)), 'fixture', 'fixture', allow_search=False)
    tools = SimpleNamespace(available=client.tools, by_name={tool.name: (client, tool) for tool in client.tools}, call=client.call)
    store = Store(directory / 'agent.db')
    events = []
    policy=Policy();policy.approval_mode=os.environ.get('ARI_ACCESSIBILITY_AUTONOMY','system')
    agent = Agent(store, tools, policy, model_provider=lambda: (provider, 'Fixture', 'fixture', 'unknown', 'large'))

    def emit(event):
        events.append(event)
        if event['type'] == 'approval':
            assert agent.approve(event['approval'], True)

    try:
        with patch('ari.agent._current_settings', return_value=''), patch('ari.agent.audit.record'):
            turn = Turn(store.new_conversation(), 'Add Chicago to the Weather app', emit)
            agent.answer(turn)
        assert events[-1]['type'] == 'done', events[-1]
        assert events[-1]['text'] == 'Added Chicago to Weather.', events[-1]
        approvals=len([event for event in events if event['type'] == 'approval'])
        assert (approvals>=6 if policy.approval_mode=='all' else approvals=={'system':1,'never':0}[policy.approval_mode]), events
        assert len(store.steps(turn.conversation)) == 2, store.steps(turn.conversation)
        from prairie_apps.weather_backend import PlaceStore
        assert any(place.name.casefold() == 'chicago' for place in PlaceStore().list())
        print(f'Scripted Responses → real agent/{policy.approval_mode} mode/{approvals} approvals → actual MCP → populated Weather saved Chicago PASS', flush=True)
        events.clear()
        state.update(stage=0, remove=True)
        with patch('ari.agent._current_settings',return_value=''),patch('ari.agent.audit.record'):
            removal=Turn(turn.conversation,'Remove Chicago from the Weather app',emit)
            agent.answer(removal)
        assert events[-1]['type']=='done' and events[-1]['text']=='Removed Chicago from Weather.',events
        approvals=len([event for event in events if event['type']=='approval'])
        assert approvals=={'all':2,'system':1,'never':0}[policy.approval_mode],events
        assert not any(place.name.casefold()=='chicago' for place in PlaceStore().list())
        assert any(place.name.casefold()=='boston' for place in PlaceStore().list())
        print(f'Scripted Responses → real agent/{policy.approval_mode}/{approvals} approvals → generic native command → exactly Chicago removed, Boston preserved PASS',flush=True)
    finally:
        store.db.close()
