# SPDX-License-Identifier: Apache-2.0
"""Typed app operations, authorization and once-per-turn write boundaries."""
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from ari.agent import Agent, Turn
from ari.mcp import Tool
from ari.policy import Policy, ACT_ON_BEHALF
from ari.store import Store
from ari import router
from ari.tools import productivity as p


class Productivity(unittest.TestCase):
    def tearDown(self):
        p._targets.clear()

    def test_wall_times_offsets_and_dst_are_not_title_parsing(self):
        zone=ZoneInfo('America/Chicago')
        value=p._moment('2026-10-30T14:00:00',zone)
        self.assertEqual(value.hour,14)
        self.assertEqual(value.utcoffset().total_seconds(),-5*3600)
        self.assertEqual(p._moment('2026-10-30T19:00:00+00:00',zone),value)
        for value in ('2026-10-30','tomorrow at 2','2026-03-08T02:30:00','2026-11-01T01:30:00'):
            with self.assertRaises(ValueError):p._moment(value,zone)
        self.assertEqual(p._moment('2026-11-01T01:30:00-06:00',zone).fold,1)

    def test_unknown_fields_and_operations_never_reach_a_backend(self):
        with patch.object(p,'backend') as backend:
            for args in ({'application':'notes','operation':'create','fields':{'command':'rm anything'}},
                         {'application':'contacts','operation':'remove'},
                         {'application':'calendar','operation':'create','fields':{'title':42}},
                         {'application':'tasks','operation':'create','fields':{'completed':'yes'}},
                         {'application':'notes','operation':'update','fields':{'body':'overwrite'}}):
                with self.assertRaises(ValueError):p.change_application(args)
            backend.assert_not_called()

    def test_targets_bind_application_revision_and_expiry(self):
        key=p._target('notes',{'id':'one','body':'original'})
        row=p._existing('notes',key)
        with self.assertRaises(ValueError):p._existing('contacts',key)
        with self.assertRaises(ValueError):p._unchanged(row,{'id':'one','body':'edited by user'})
        row['expires']=time.monotonic()-1
        with self.assertRaises(ValueError):p._existing('notes',key)

    def test_query_budget_keeps_complete_records_and_discards_unreturned_targets(self):
        import json
        records=[{'body':'x'*1000,'target':p._target('notes',{'id':i})} for i in range(30)]
        chosen,truncated=p._bounded(records,6200)
        self.assertTrue(truncated)
        self.assertLess(len(json.dumps(chosen)),6500)
        self.assertEqual(len(p._targets),len(chosen))
        self.assertTrue(all(row['body']=='x'*1000 for row in chosen))

    def test_sources_need_a_default_or_an_unambiguous_writable_choice(self):
        one=SimpleNamespace(uid='one',writable=True,is_default=False)
        two=SimpleNamespace(uid='two',writable=True,is_default=False)
        self.assertIs(p._source([one]),one)
        with self.assertRaises(ValueError):p._source([one,two])
        two.is_default=True
        self.assertIs(p._source([one,two]),two)
        with self.assertRaises(ValueError):p._source([one,two],'invented')
        two.writable=False
        with self.assertRaises(ValueError):p._source([one,two],'two')

    def test_direct_operations_are_routed_before_visual_fallback(self):
        tools=[Tool(name,name,{},'productivity') for name in ('open_app','inspect_application','act_in_application','query_application','change_application')]
        for text in ('Create a note','Add a calendar event tomorrow','Add a contact','Remove Chicago from Weather'):
            self.assertEqual([tool.name for tool in router.select(text,tools,2)],['query_application','change_application'])

    def test_modes_deduplicate_verified_writes_and_preserve_revocation(self):
        for mode,expected in (('system',1),('all',1),('never',0)):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                store=Store(Path(directory)/'ari.db')
                tool=Tool('change_application','Direct operation',{'properties':{'preview':{'type':'boolean'}}},'productivity')
                writes=[]
                def call(_name,args):
                    if args.get('preview'):
                        return {'ok':True,'preview':'Create a note','requested_name':'notes','application_name':'Notes','application_scope':'backend:notes'}
                    writes.append(args)
                    return {'ok':True,'verified':True,'summary':'Created a note.','data':{'record':{'id':'actual'}}}
                tools=SimpleNamespace(available=[tool],by_name={tool.name:(None,tool)},call=Mock(side_effect=call))
                policy=Policy();policy.approval_mode=mode
                agent=Agent(store,tools,policy,model_provider=None)
                events=[]
                def emit(event):
                    events.append(event)
                    if event['type']=='approval':agent.approve(event['approval'],True)
                turn=Turn(store.new_conversation(),'Create a note with my shopping list',emit)
                args={'application':'notes','operation':'create','fields':{'title':'Shopping list','body':'Bread'}}
                with patch('ari.agent.audit.record'):
                    self.assertTrue(agent.run_tool(turn,tool.name,args)['ok'])
                    self.assertTrue(agent.run_tool(turn,tool.name,args)['unchanged'])
                    self.assertEqual(len(writes),1)
                    self.assertEqual(sum(event['type']=='approval' for event in events),expected)
                    policy.enabled.discard(ACT_ON_BEHALF)
                    self.assertFalse(agent.run_tool(turn,tool.name,args)['ok'])
                    self.assertEqual(len(writes),1)
                store.db.close()


if __name__=='__main__':unittest.main()
