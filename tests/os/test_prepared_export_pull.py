#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Exercise the collection-aware import boundary with actual OSTree objects."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('prepared_export', ROOT/'scripts/os/lib/prepared_export.py')
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)

class ImportedTreeBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.source = self.root/'source'; self.target = self.root/'target'
        for repo in (self.source,self.target):
            subprocess.run(['ostree','init','--repo='+str(repo),'--mode=archive','--collection-id=org.projectluma.OS'],check=True)
        tree=self.root/'tree';tree.mkdir();(tree/'payload').write_text('Actual native import boundary fixture\n')
        self.raw=subprocess.check_output(['ostree','commit','--repo='+str(self.source),'--orphan','--no-bindings','--tree=dir='+str(tree),'--subject=raw importer tree'],text=True).strip()
    def commit(self, bound):
        args=['ostree','commit','--repo='+str(self.source),'--orphan','--tree=ref='+self.raw,'--subject=candidate']
        args += ['--bind-ref=luma/1/aarch64/nightly'] if bound else ['--no-bindings']
        return subprocess.check_output(args,text=True).strip()
    def test_importer_unbound_tree_then_strict_bound_candidate(self):
        candidate=self.commit(True)
        module.pull_prepared_objects(self.target,self.source,self.raw,candidate)
        for commit in (self.raw,candidate):
            subprocess.run(['ostree','show','--repo='+str(self.target),commit],check=True,stdout=subprocess.DEVNULL)
    def test_unbound_publishable_candidate_is_rejected(self):
        candidate=self.commit(False)
        with self.assertRaises(subprocess.CalledProcessError):
            module.pull_prepared_objects(self.target,self.source,self.raw,candidate)
if __name__=='__main__':unittest.main()
