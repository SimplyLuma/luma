# SPDX-License-Identifier: Apache-2.0
"""Action boundary checks; actual Weather control is a separate native test."""
import unittest
from enum import Enum
from types import SimpleNamespace
from unittest.mock import patch
from ari.tools import accessibility as a


class Role(Enum):
    APPLICATION='application';FRAME='frame';WINDOW='window';DIALOG='dialog';PASSWORD_TEXT='password';ENTRY='entry'
    @property
    def value_nick(self):return self.value

class Node:
    def __init__(self,name,role=Role.ENTRY,children=(),text=''):
        self.name,self.role,self.children,self.text=name,role,list(children),text
        self.enabled=True
    def get_name(self):return self.name
    def get_role(self):return self.role
    def get_child_count(self):return len(self.children)
    def get_child_at_index(self,index):return self.children[index]
    def get_process_id(self):return 1
    def clear_cache(self):pass
    def get_state_set(self):return SimpleNamespace(contains=lambda state: state!='defunct' and (state!='sensitive' or self.enabled))
    def get_action_iface(self):return None
    def get_editable_text_iface(self):return self if self.role==Role.ENTRY else None
    def get_text_iface(self):
        if self.role==Role.PASSWORD_TEXT:raise AssertionError('Read a protected field')
        return self if self.role==Role.ENTRY else None
    def get_character_count(self):return len(self.text)
    def get_text(self,start,end):return self.text[start:end]
    def set_text_contents(self,value):self.text=value;return True


class Accessibility(unittest.TestCase):
    def setUp(self):
        a._apps.clear();a._targets.clear()
        self.entry=Node('City search',text='Existing text')
        self.secret=Node('Secret',Role.PASSWORD_TEXT,text='NEVER READ')
        self.frame=Node('Weather',Role.FRAME,[self.entry,self.secret])
        self.app=Node('python3',Role.APPLICATION,[self.frame])
        self.desktop=Node('Desktop',Role.FRAME,[self.app])
        api=SimpleNamespace(Text=SimpleNamespace(get_text=lambda node,start,end:node.get_text(start,end)),Role=Role,StateType=SimpleNamespace(DEFUNCT='defunct',SHOWING='showing',ENABLED='enabled',SENSITIVE='sensitive',EDITABLE='editable'),get_desktop=lambda index:self.desktop)
        self.api=patch.object(a,'api',return_value=api);self.api.start()
        self.start=patch.object(a,'_start',return_value='original');self.start.start()
        self.application=a.desktop_applications({'name':'Weather'})['data'][0]['application']
    def tearDown(self):self.start.stop();self.api.stop();a._apps.clear();a._targets.clear()
    def operation(self):
        controls=a.inspect_application({'application':self.application})['data']['controls']
        self.assertFalse(any(row['name']=='Secret' for row in controls))
        entry=next(row for row in controls if row['editable'])
        return {'application':self.application,'target':entry['target'],'operation':'set_text','text':'Chicago'}
    def test_preview_preserves_text_and_actual_action_invalidates_the_target(self):
        args=self.operation()
        self.assertEqual(a.act_in_application({**args,'preview':True})['requested_name'],'weather')
        self.assertEqual(self.entry.text,'Existing text')
        self.assertTrue(a.act_in_application(args)['ok'])
        self.assertEqual(self.entry.text,'Chicago')
        with self.assertRaises(ValueError):a.act_in_application(args)
    def test_text_changed_while_approval_open_is_preserved(self):
        args=self.operation()
        a.act_in_application({**args,'preview':True})
        self.entry.text='User edited while approval was open'
        with self.assertRaises(ValueError):a.act_in_application(args)
        self.assertEqual(self.entry.text,'User edited while approval was open')
    def test_layout_substitution_or_pid_reuse_is_refused(self):
        args=self.operation()
        self.frame.children[0]=Node('City search',text='Another widget')
        with self.assertRaises(ValueError):a.act_in_application(args)
        with patch.object(a,'_start',return_value='reused'):
            with self.assertRaises(ValueError):a.inspect_application({'application':self.application})
