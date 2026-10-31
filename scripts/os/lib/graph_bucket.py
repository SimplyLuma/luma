#!/usr/bin/python3 -B
# SPDX-License-Identifier: Apache-2.0
"""One explicit graph root -> architecture/device bucket for all producers.

The input root never includes a bucket suffix. A canonical directory returned
here must not be passed back to a signer's --graph-dir root argument.
"""
from functools import lru_cache
import argparse
import importlib.util
import json
from pathlib import Path
import re


@lru_cache(maxsize=None)
def sibling(name):
    spec=importlib.util.spec_from_file_location('graph_bucket_'+name,Path(__file__).with_name(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


class GraphBucket:
    def __init__(self, architecture='x86_64', hardware_target='', major='1'):
        self.architecture=sibling('desktop_paths').architecture(architecture)
        if not isinstance(hardware_target,str):raise ValueError('Explicit hardware target must be a string')
        if not re.fullmatch(r'[1-9][0-9]*',major):raise ValueError('Invalid graph OS major')
        self.hardware_target=hardware_target;self.major=major;self.slug=''
        if hardware_target:
            row=sibling('devices').device(hardware_target)
            if row['arch']!=architecture:raise ValueError('Graph hardware target and architecture disagree')
            self.slug=row['slug']

    @property
    def parts(self):
        return (self.architecture,self.slug) if self.slug else (self.architecture,)

    def directory(self,root):
        return Path(root).joinpath(*self.parts)

    def directories(self,root):
        result=[self.directory(root)]
        if self.architecture=='x86_64' and not self.hardware_target:
            result.append(Path(root))
        return result

    def public_prefix(self):
        return 'os/graph/'+'/'.join(self.parts)

    def public_prefixes(self):
        result=[self.public_prefix()]
        if self.architecture=='x86_64' and not self.hardware_target:result.append('os/graph')
        return result

    def refresh_root(self,workspace,channel):
        return Path(workspace)/self.channel(channel)

    def previous_refresh_directory(self,workspace,channel):
        if self.hardware_target:return self.directory(self.refresh_root(workspace,channel))
        return sibling('desktop_paths').architecture_path(workspace,self.architecture)/self.channel(channel)

    def releases(self,repo):
        if self.hardware_target:
            return Path(repo)/'luma'/'targets'/self.architecture/self.slug/'releases'
        return sibling('desktop_paths').releases_path(repo,self.architecture)

    def release_manifest(self,repo,version):
        if not isinstance(version,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.+_-]*',version):
            raise ValueError('Invalid published release version')
        return self.releases(repo)/version/'manifest.json'

    def ref(self,channel):
        self.channel(channel)
        return '/'.join(('luma',self.major,*self.parts,channel))

    @staticmethod
    def channel(channel):
        if channel not in ('stable','beta','nightly'):raise ValueError('Unsupported graph channel')
        return channel

    def validate_graph(self,graph,channel):
        self.channel(channel)
        if (graph.get('schema_version')!=(2 if self.hardware_target else 1) or
                graph.get('channel')!=channel or graph.get('arch')!=self.architecture or
                graph.get('hardware_target','')!=self.hardware_target):
            raise ValueError('Graph channel, architecture or hardware target differs')

    def validate_manifest(self,manifest):
        if (manifest.get('architecture','x86_64')!=self.architecture or
                manifest.get('hardware_target','')!=self.hardware_target):
            raise ValueError('Publication architecture/hardware target differs from graph bucket')
        ref=manifest.get('ref')
        if ref is not None and ref!=self.ref(manifest.get('channel')):
            raise ValueError('Publication ref differs from graph bucket')
        if self.architecture!='x86_64' and not ref:
            raise ValueError('Explicit ARM/device publication ref required')

    def comment(self,graph,key_id):
        self.validate_graph(graph,graph.get('channel'))
        value=('luma-update-graph channel='+graph['channel']+' arch='+self.architecture+
               ' generated_at='+graph['generated_at']+' key='+key_id)
        if self.hardware_target:value+=' hardware_target='+self.hardware_target
        return value

    def unit_name(self,channel):
        self.channel(channel)
        suffix='' if self.architecture=='x86_64' and not self.hardware_target else '-'.join(self.parts)+'-'
        return 'luma-approved-os-graph-refresh-'+suffix+channel


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--arch',default='x86_64')
    parser.add_argument('--hardware-target',default='')
    parser.add_argument('--comment',type=Path)
    parser.add_argument('--key-id')
    parser.add_argument('root',type=Path,nargs='?')
    args=parser.parse_args()
    bucket=GraphBucket(args.arch,args.hardware_target)
    if args.comment:
        if args.root is not None or not args.key_id:parser.error('Comment needs a public key ID and no graph root')
        print(bucket.comment(json.loads(args.comment.read_text()),args.key_id))
    else:
        if args.root is None:parser.error('Graph root required')
        for path in bucket.directories(args.root):print(path)


if __name__=='__main__':main()
