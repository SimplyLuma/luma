#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Tasks comments display real milliseconds/ISO times without rewriting stored data."""
from datetime import date,datetime,timezone
import os,time,unittest
from types import SimpleNamespace
from unittest.mock import patch
from prairie_apps.tasks_data import TasksData,comment_time
from prairie_apps.tasks_backend import Comment,Task,TaskList

class TasksCommentTimeTests(unittest.TestCase):
    def setUp(self):
        self.old=os.environ.get("TZ");os.environ["TZ"]="UTC";time.tzset()
        self.now=datetime(2026,10,8,12,tzinfo=timezone.utc)
        self.ms=int(self.now.timestamp()*1000)
    def tearDown(self):
        if self.old is None:os.environ.pop("TZ",None)
        else:os.environ["TZ"]=self.old
        time.tzset()
    def test_hub_integer_milliseconds(self):
        self.assertEqual(comment_time(self.ms,now=self.now),"Today · 12:00 PM")
    def test_hub_string_milliseconds(self):
        self.assertEqual(comment_time(str(self.ms),now=self.now),"Today · 12:00 PM")
    def test_existing_local_iso_z(self):
        self.assertEqual(comment_time("2026-10-08T12:00:00Z",now=self.now),"Today · 12:00 PM")
    def test_iso_offset_is_converted_to_local_time(self):
        self.assertEqual(comment_time("2026-10-08T14:00:00+02:00",now=self.now),"Today · 12:00 PM")
    def test_local_naive_iso_remains_local(self):
        self.assertEqual(comment_time("2026-10-08T09:15:00",now=self.now),"Today · 9:15 AM")
    def test_actual_local_timezone_changes_clock(self):
        os.environ["TZ"]="America/New_York";time.tzset()
        self.assertEqual(comment_time(self.ms,now=self.now),"Today · 8:00 AM")
    def test_local_day_boundary_uses_yesterday(self):
        os.environ["TZ"]="America/New_York";time.tzset()
        self.assertEqual(comment_time("2026-10-08T01:00:00Z",now=self.now),"Yesterday · 9:00 PM")
    def test_invalid_values_show_no_internal_timestamp(self):
        for value in (None,True,False,1.5,float("nan"),"","bad","9999999999999999999999","2026-99-99T00:00:00"):
            with self.subTest(value=value):self.assertEqual(comment_time(value,now=self.now),"")
    def test_original_comment_value_is_not_rewritten(self):
        original=Comment("Hello","",str(self.ms));comment_time(original.created,now=self.now)
        self.assertEqual(original.created,str(self.ms))
    def test_both_live_and_local_projection_are_formatted_without_store_mutation(self):
        original=Comment("Live","",str(self.ms));task=Task("task-1","book","A task",comments=(original,))
        local={"book|task-1|":{"comments":[{"author":"","text":"Local","created":"2026-10-08T12:00:00Z"}]}}
        data=TasksData.__new__(TasksData);data.fixture=False;data.today=date(2026,10,8)
        data.repository=SimpleNamespace(load=lambda:([TaskList("book","List")],[task]),me="me",registry=None,errors=(),comments_are_local=lambda _:True)
        data.local=SimpleNamespace(read=lambda:{"tasks":local})
        view=data.load()
        self.assertEqual([row[3]for row in view["tasks"][0]["act"]],["Today · 12:00 PM","Today · 12:00 PM"])
        self.assertEqual(original.created,str(self.ms));self.assertEqual(local["book|task-1|"]["comments"][0]["created"],"2026-10-08T12:00:00Z")

if __name__=="__main__":unittest.main()
