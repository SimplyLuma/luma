#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Prepare refresh admission inputs from genuine already qualified publication.

Usage: CONTROL CONTROL_SHA HOST_CONFIG SIGNED_GRAPH_DIR OUTPUT CHANNEL
Seal OUTPUT with seal-inputs, then install/activate it through the normal
publication sequence. This does not sign or upload anything.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

def load_module(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def main():
    if os.getuid() or len(sys.argv)!=7:
        raise ValueError('Root-only usage: CONTROL CONTROL_SHA HOST_CONFIG SIGNED_GRAPH_DIR OUTPUT CHANNEL')
    control,digest,config,graphs,target,channel=sys.argv[1:]
    if channel not in ('beta','nightly') or not re.fullmatch('[a-f0-9]{64}',digest):
        raise ValueError('Unsupported channel or unsealed control')
    control,config,graphs,target=map(Path,(control,config,graphs,target))
    runner=load_module(control/'scripts/os/refresh-approved-graphs.py','refresh_control')
    for path in (control,config,graphs,target.parent):runner.protected(path)
    seal=load_module(control/'scripts/depot/seal-signing-control.py','input_seal')
    seal.verify(control,digest)
    checks=load_module(control/'scripts/os/lib/approved_graph.py','admission_checks')
    cfg=json.loads(config.read_text())
    selected=cfg['channels'][channel]
    repo=Path(selected['repository_path']);runner.protected(repo)
    graph_path=graphs/(channel+'.json');runner.protected(graph_path)
    signature=graphs/(channel+'.json.minisig');runner.protected(signature)
    graph=json.loads(graph_path.read_text())
    if graph['channel']!=channel or graph['arch']!='x86_64':raise ValueError('Wrong publication graph')
    records={}
    for release in graph['releases']:
        path=repo/'luma/releases'/release['version']/'manifest.json';runner.protected(path)
        manifest=json.loads(path.read_text())
        if manifest['channel']!=channel:raise ValueError('Wrong genuine publication channel')
        record={'manifest_sha256':runner.digest(path)};evidence={}
        if manifest.get('bootstrap') is None:
            for name,filename in (('gate','gate-result.json'),('delivery','PUBLIC-DELIVERY.json')):
                original=path.parent/filename;runner.protected(original)
                record[name+'_sha256']=runner.digest(original)
                evidence[name]=json.loads(original.read_text())
            if record['gate_sha256']!=manifest['gate']['sha256']:
                raise ValueError('Gate evidence differs from the signed publication manifest')
        checks.admitted_release(release,manifest,public_repository_url=selected['public_repository_url'],**evidence)
        records[release['commit']]=record
    if target.exists():raise ValueError('Refusing to alter an existing approval snapshot')
    target.mkdir(mode=0o700);(target/'graphs').mkdir(mode=0o700)
    for path in (graph_path,signature):
        (target/'graphs'/path.name).write_bytes(path.read_bytes())
    selected={**selected,'graph':'graphs/'+channel+'.json','releases':records}
    cfg={**cfg,'channels':{channel:selected}}
    (target/'approved.json').write_text(json.dumps(cfg,indent=2,sort_keys=True)+'\n')
    print('Prepared '+channel+' genuine publication inputs; seal and validate signature before activation')

if __name__=='__main__':main()
