# SPDX-License-Identifier: Apache-2.0
import copy
import importlib.util
from pathlib import Path
import unittest
ROOT=Path(__file__).parents[2]
spec=importlib.util.spec_from_file_location('approved_graph',ROOT/'scripts/os/lib/approved_graph.py')
graph=importlib.util.module_from_spec(spec);spec.loader.exec_module(graph)

class ApprovedRefresh(unittest.TestCase):
    def setUp(self):
        self.release={'commit':'a'*64,'version':'1.0.0-beta.1.1','released_at':'2026-10-09T12:00:00Z','paused':False}
        self.manifest={'commit':'a'*64,'version':'1.0.0-beta.1.1','published_utc':self.release['released_at'],
                       'source_dirty':False,'gate':{'result':'pass'},'parent':'b'*64}
        self.gate={'commit':'a'*64,'result':'pass','fresh':'pass','no_account':'pass','update':'pass','rollback':'pass'}
        self.delivery={'schema':'org.projectluma.os-public-delivery/v1','commit':'a'*64,
                       'complete_closure_readback':True,'commit_signature_verified':True,'summary_signature_verified':True}

    def test_qualified_delivered_release_is_admitted(self):
        graph.admitted_release(self.release,self.manifest,self.delivery,self.gate)

    def test_signature_timestamp_refresh_keeps_exact_policy(self):
        before={'channel':'beta','generated_at':'old','releases':[self.release]}
        graph.same_policy(before,{**before,'generated_at':'new'})

    def test_newer_release_or_pause_policy_cannot_be_overwritten(self):
        before={'channel':'beta','generated_at':'old','releases':[self.release]}
        for changes in ({'commit':'c'*64},{'paused':True}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                graph.same_policy(before,{**before,'releases':[{**self.release,**changes}]})

    def test_dirty_or_failed_manifest_is_refused(self):
        for changes in ({'source_dirty':True},{'gate':{'result':'fail'}}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                graph.admitted_release(self.release,{**self.manifest,**changes},self.delivery,self.gate)

    def test_incomplete_or_different_delivery_is_refused(self):
        for delivery in (None,{**self.delivery,'complete_closure_readback':False},{**self.delivery,'commit':'b'*64}):
            with self.subTest(delivery=delivery),self.assertRaises(ValueError):
                graph.admitted_release(self.release,self.manifest,delivery,self.gate)

    def test_wrong_commit_or_failed_required_gate_is_refused(self):
        for gate in (None,{**self.gate,'commit':'b'*64},{**self.gate,'rollback':'fail'},{**self.gate,'no_account':'fail'}):
            with self.subTest(gate=gate),self.assertRaises(ValueError):
                graph.admitted_release(self.release,self.manifest,self.delivery,gate)

    def test_upgrade_alias_does_not_replace_required_update_gate(self):
        alias={key:value for key,value in self.gate.items() if key!='update'}
        alias['upgrade']='pass'
        with self.assertRaises(ValueError):
            graph.admitted_release(self.release,self.manifest,self.delivery,alias)

    def test_initial_channel_can_have_not_applicable_update_gates(self):
        graph.admitted_release(self.release,{**self.manifest,'parent':None},self.delivery,
                               {**self.gate,'update':'not-applicable','rollback':'not-applicable'})

    def test_nightly_staging_label_is_not_proof_of_public_delivery(self):
        public='https://dl.simplyluma.com/os/repo'
        with self.assertRaises(ValueError):
            graph.admitted_release(self.release,self.manifest,self.delivery,self.gate,public)
        graph.admitted_release(self.release,self.manifest,{**self.delivery,'repository_url':public},self.gate,public)
        with self.assertRaises(ValueError):
            graph.admitted_release(self.release,self.manifest,
                {**self.delivery,'repository_url':'https://staff.invalid/private'},self.gate,public)

    def test_retained_initial_installation_baseline_stays_paused(self):
        release={**self.release,'commit':graph.BASELINE,'version':'1.0.0-beta.1','paused':True}
        manifest={**self.manifest,'commit':graph.BASELINE,'version':'1.0.0-beta.1','bootstrap':{'paused':True},
                  'source_dirty':True,'gate':{'result':'fail'}}
        graph.admitted_release(release,manifest)
        with self.assertRaises(ValueError):graph.admitted_release({**release,'paused':False},manifest)

if __name__=='__main__':unittest.main()
