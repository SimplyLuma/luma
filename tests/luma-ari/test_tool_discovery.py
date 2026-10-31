# SPDX-License-Identifier: Apache-2.0
"""A narrowed initial catalogue must not become a permanent capability wall."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from ari.agent import Agent,Turn,_tool_text
from ari.mcp import Tool
from ari.policy import Policy,ANSWER
from ari.store import Store


class ToolDiscovery(unittest.TestCase):
    def test_discovery_loads_real_schemas_without_executing_or_authorizing_actions(self):
        action=Tool('control_process','End a process',{'type':'object','properties':{}},'processes')
        available=[Tool('now','Time',{},'system'),action]
        tools=SimpleNamespace(available=available,by_name={t.name:(None,t) for t in available},call=Mock())
        seen=[]
        class Provider:
            model='fixture';hosted_search=False
            def chat(self,messages,schemas,**kwargs):
                names={schema['function']['name'] for schema in schemas}
                seen.append(names)
                if len(seen)==1:
                    assert 'discover_tools' in names and 'control_process' not in names
                    yield {'type':'tool_call','id':'discover','name':'discover_tools','arguments':{'query':'process pid'}}
                else:
                    assert 'control_process' in names
                    yield {'type':'text','text':'The process controls are available.'}
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'ari.db')
            policy=Policy();policy.enabled.discard(policy.tier('control_process'))
            policy.approval_mode='all'
            agent=Agent(store,tools,policy,model_provider=lambda:(Provider(),'Fixture','test','2026','small'))
            events=[]
            turn=Turn(store.new_conversation(),'What capabilities are available?',events.append)
            with patch('ari.agent.audit.record'),patch('ari.agent._current_settings',return_value=''):
                agent.answer(turn)
                self.assertEqual(len(seen),2)
                tools.call.assert_not_called()
                self.assertFalse(turn.steps)
                self.assertFalse(any(event['type']=='approval' for event in events))
                denied=agent.run_tool(turn,'control_process',{})
                self.assertTrue(denied['refused'])
                policy.enabled.discard(ANSWER)
                self.assertTrue(agent.run_tool(turn,'discover_tools',{'query':'process'})['refused'])
            store.db.close()

    def test_saved_record_verification_does_not_imply_window_verification(self):
        text=_tool_text({'ok':True,'summary':'Created a note.','data':{'verification':{
            'scope':'saved-record','display_verified':False}}})
        self.assertIn('visible window was not verified or refreshed',text)


if __name__=='__main__':unittest.main()
