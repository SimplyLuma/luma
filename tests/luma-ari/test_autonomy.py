# SPDX-License-Identifier: Apache-2.0
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch

from ari.agent import Agent,Turn,_requested_application
from ari.mcp import Tool
from ari.policy import Policy,ACT_ON_BEHALF,validate_permission
from ari.store import Store


class Autonomy(unittest.TestCase):
    def test_three_modes_and_explicit_overrides(self):
        policy=Policy()
        for mode,count in (('all',True),('system',False),('never',False)):
            policy.approval_mode=mode
            self.assertEqual(policy.needs_approval('open_app'),count)
        policy.action_rules={'open_apps':'ask'}
        self.assertTrue(policy.needs_approval('open_app'))
        policy.action_rules={'enter_text@weather':'allow','activate_controls':'ask'}
        self.assertFalse(policy.needs_approval('act_in_application',{'operation':'set_text'},'weather'))
        self.assertTrue(policy.needs_approval('act_in_application',{'operation':'action'},'weather'))
        for invalid in ('invented','enter_text@../../other','open_apps@weather'):
            with self.assertRaises(ValueError): validate_permission(invalid,'allow')

    def test_task_scope_remembering_and_revocation_are_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'ari.db')
            tool=Tool('act_in_application','Control an app',{'properties':{'preview':{'type':'boolean'}}},'accessibility')
            def operation(_name,args):
                return ({'ok':True,'preview':'Enter text','requested_name':'weather','application_name':'Weather',
                         'application_scope':args['application']} if args.get('preview') else {'ok':True,'summary':'Changed Weather'})
            tools=SimpleNamespace(available=[tool],by_name={tool.name:(None,tool)},call=Mock(side_effect=operation))
            agent=Agent(store,tools,Policy(),model_provider=None)
            saved=Mock();agent.on_permission=saved
            events=[]
            def emit(event):
                events.append(event)
                if event['type']=='approval': agent.approve(event['approval'],True)
            turn=Turn(store.new_conversation(),'Add Chicago to Weather',emit)
            args={'application':'weather-process-1','target':'field','operation':'set_text','text':'Chicago'}
            with patch('ari.agent.audit.record'):
                self.assertTrue(agent.run_tool(turn,tool.name,args)['ok'])
                self.assertTrue(agent.run_tool(turn,tool.name,{**args,'operation':'action'})['ok'])
                self.assertEqual(sum(e['type']=='approval' for e in events),1)
                saved.assert_not_called()
                agent.policy.action_rules={'activate_controls@weather':'ask'}
                agent.run_tool(turn,tool.name,{**args,'operation':'action'})
                self.assertEqual(sum(e['type']=='approval' for e in events),2)
                agent.policy.approval_mode='never'
                agent.policy.action_rules={'enter_text@weather':'deny'}
                before=tools.call.call_count
                self.assertTrue(agent.run_tool(turn,tool.name,args)['refused'])
                self.assertEqual(tools.call.call_count,before+1) # preview only
                agent.policy.enabled.discard(ACT_ON_BEHALF)
                before=tools.call.call_count
                self.assertFalse(agent.run_tool(turn,tool.name,args)['ok'])
                self.assertEqual(tools.call.call_count,before)
            store.db.close()

    def test_remember_is_an_explicit_choice_and_cannot_reuse_an_old_approval(self):
        agent=Agent(None,None,Policy(),model_provider=None)
        saved=Mock();agent.on_permission=saved
        def emit(event):
            if event['type']=='approval':
                self.assertTrue(agent.approve(event['approval'],True,remember=True))
        turn=Turn('test','Add Chicago to Weather',emit)
        accepted,identity=agent._ask_approval(turn,'act_in_application','Add city',permission='enter_text@weather')
        self.assertTrue(accepted)
        saved.assert_called_once_with('enter_text@weather','allow')
        self.assertFalse(agent.approve(identity,True,remember=True))

    def test_common_task_names_choose_only_the_known_application(self):
        self.assertTrue(_requested_application('Add a timer for 10 minutes','clock'))
        self.assertTrue(_requested_application('Reply to this email','charlie'))
        self.assertFalse(_requested_application('Add a timer for 10 minutes','browser'))
