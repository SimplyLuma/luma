# SPDX-License-Identifier: GPL-3.0-only
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from page_touch import PageTouch


class TouchTest(unittest.TestCase):
    def setUp(self):
        self.sent=[]
        self.owner=SimpleNamespace(session='session',target='page',tab_id='tab',
            page=SimpleNamespace(grab_focus=lambda:None),metrics={},
            point=lambda x,y:dict(x=x/2,y=y/2),valid_identity=lambda:True)
        self.owner.dispatch=lambda method,params,**kwargs:self.sent.append((method,params,kwargs)) or True
        self.touch=PageTouch.__new__(PageTouch)
        self.touch.owner=self.owner
        self.touch.contacts={};self.touch.ignored=set();self.touch.identity=None
        self.touch.enabled_session=None;self.touch.next_id=0;self.touch.move=None
        self.touch.timer=None;self.touch.inflight=0
        self.timer=patch('page_touch.GLib.timeout_add',return_value=123).start()
        self.remove=patch('page_touch.GLib.source_remove').start()
        self.addCleanup(patch.stopall)

    def test_two_contacts_retain_ids_and_release_only_lifted_finger(self):
        t=self.touch
        t.feed('start',11,100,200);t.feed('start',22,200,200)
        self.assertEqual(self.sent[-1][1]['touchPoints'],[dict(id=1,x=50,y=100),dict(id=2,x=100,y=100)])
        t.feed('move',11,100,100);t.feed('move',22,200,100)
        t.feed('end',11,100,100)
        self.assertEqual(self.sent[-1][1],dict(type='touchMove',touchPoints=[dict(id=2,x=100,y=50)],modifiers=0))
        t.feed('end',22,200,100)
        self.assertEqual(self.sent[-1][1]['type'],'touchEnd')
        self.assertEqual(self.sent[-1][1]['touchPoints'],[])

    def test_slow_motion_is_bounded_and_final_position_precedes_release(self):
        t=self.touch;t.feed('start',11,100,200)
        for y in [180,160]:
            t.feed('move',11,100,y);t.flush()
        self.assertEqual(t.inflight,2)
        count=len(self.sent)
        for y in range(150,30,-1):t.feed('move',11,100,y)
        self.assertEqual(len(self.sent),count)
        t.feed('end',11,100,31)
        self.assertEqual(self.sent[-2][1]['touchPoints'][0]['y'],15.5)
        self.assertEqual(self.sent[-1][1]['type'],'touchEnd')

    def test_switching_pages_does_not_forward_old_contacts_into_new_page(self):
        t=self.touch;t.feed('start',11,100,200)
        count=len(self.sent);self.owner.target='new-page'
        t.feed('move',11,100,100);t.feed('end',11,100,100)
        self.assertEqual(len(self.sent),count)
        self.assertFalse(t.contacts);self.assertFalse(t.ignored)
        t.feed('start',22,50,100)
        self.assertEqual(self.sent[-1][1]['type'],'touchStart')

    def test_cancel_drops_queued_motion_and_blocks_remaining_contact(self):
        t=self.touch;t.feed('start',11,100,200);t.feed('move',11,100,100)
        t.cancel();count=len(self.sent)
        self.assertEqual(self.sent[-1][1]['type'],'touchCancel')
        t.feed('move',11,100,50);t.feed('end',11,100,50)
        self.assertEqual(len(self.sent),count)
        self.assertIsNone(t.move)
