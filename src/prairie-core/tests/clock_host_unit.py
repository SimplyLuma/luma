# SPDX-License-Identifier: Apache-2.0
import json
import unittest
from prairie_apps.clock_host_protocol import validate_plan

class PlanTests(unittest.TestCase):
    def plan(self):
        return {'alarms':[dict(uid='a'*32,label='Wake',hour=7,minute=30,days=[0,2],enabled=True,sound='Chime',snooze_minutes=9,ring_seconds=60)],'timers':[dict(uid='b'*32,label='Tea',fires_at=1005,total_seconds=5)]}
    def test_real_alarm_and_timer_survive(self):
        plan=self.plan();self.assertEqual(validate_plan(json.dumps(plan),now=1000),plan)
    def test_no_commands_paths_overflow_or_invalid_schedule(self):
        variants=[]
        for key,value in [('hour',24),('hour',True),('minute',-1),('days',[0,0]),('days',[7]),('days',['1']),('enabled','true'),('sound','/tmp/arbitrary'),('snooze_minutes',0),('ring_seconds',999999),('uid','../../other'),('label','x'*257)]:
            p=self.plan();p['alarms'][0][key]=value;variants.append(p)
        for p in variants:
            with self.subTest(p=p),self.assertRaises((ValueError,TypeError)):validate_plan(json.dumps(p),now=1000)
        p=self.plan();p['path']='/tmp/secret'
        with self.assertRaises(ValueError):validate_plan(json.dumps(p),now=1000)
        p=self.plan();p['timers'][0]['uid']='a'*32
        with self.assertRaises(ValueError):validate_plan(json.dumps(p),now=1000)
        with self.assertRaises(ValueError):validate_plan(' '*131073)

if __name__=='__main__':unittest.main()
