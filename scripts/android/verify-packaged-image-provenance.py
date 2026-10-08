#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Bind packaged raw images/notices/source materials to the actual producer.

This verifies a private candidate's immutable byte correspondence. It never
claims installed behavior or a complete publicly available source offer.
"""
import gzip,hashlib,json,re,sys,xml.etree.ElementTree as ET
from pathlib import Path

def sha(p):
 with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def regular(p):
 if not p.is_file() or p.is_symlink():raise ValueError('Missing regular source evidence: '+p.name)
 return p

def source_manifest(producer):
 values=[producer[k] for k in ('source_manifest_sha256','source63_sha256') if k in producer]
 if len(values)!=1 or not isinstance(values[0],str) or not re.fullmatch('[a-f0-9]{64}',values[0]):
  raise ValueError('Missing or ambiguous exact source manifest identity')
 return values[0]

def verify(root,producer_path,members_path,materials,system_notice,vendor_notice,font):
 producer=json.loads(regular(producer_path).read_text());members=json.loads(regular(members_path).read_text());manifest=json.loads(regular(root/'luma-images.json').read_text())
 if (producer['result']!='CANONICAL-PAIR-RETAINED' or type(producer['actual_container_exit_code']) is not int
     or producer['actual_container_exit_code']!=0 or producer['actual_systemd_terminal_success'] is not True):
  raise ValueError('No genuine terminal matching producer')
 if members['result']!='PASS' or members['read_only_debugfs_no_filesystem_mount'] is not True or members['terminal_pair_receipt_sha256']!=sha(producer_path):
  raise ValueError('No matching actual inside-image verification')
 if members.get('actual_Lineage_TaskStackListener_closeRemovedTask_and_HIDL13_native_java_VINTF_coherent') is not True:
  raise ValueError('Authoritative task-removal Java/native/HIDL13/VINTF pair not proven')
 origin=manifest['origin']
 if origin['kind']!='luma-native-builder' or origin['source_manifest_sha256']!=source_manifest(producer) or origin['compiler_invocation_id']!=producer['compiler_invocation_id']:
  raise ValueError('Packaged image identity differs from its actual producer')
 if set(producer['images'])!={'system.img','vendor.img'} or set(manifest['images'])!={'system.img','vendor.img'}:
  raise ValueError('Incomplete producer pair')
 for name in ('system.img','vendor.img'):
  p=regular(root/name);r=producer['images'][name];m=manifest['images'][name]
  if sha(p)!=r['sha256'] or p.stat().st_size!=r['bytes'] or m['sha256']!=r['sha256'] or m['size']!=r['bytes']:
   raise ValueError('Packaged raw image differs from actual terminal producer')
  a=manifest['archives'][name[:-4]];record=producer['archives'][name]
  if a['sha256']!=record['sha256'] or a['size']!=record['bytes']:
   raise ValueError('Packaged archive differs from actual terminal producer')
 if sha(regular(materials))!=producer['source_material_archive']['sha256'] or materials.stat().st_size!=producer['source_material_archive']['bytes']:
  raise ValueError('Exact corresponding material archive changed')
 for role,notice in (('system',system_notice),('vendor',vendor_notice)):
  regular(notice);digest=sha(notice);found=[r for r in members['members'] if r.get('role')==role and r.get('image_path','').endswith('/etc/NOTICE.xml.gz')]
  if len(found)!=1 or found[0]['sha256']!=digest:raise ValueError('Aggregate NOTICE differs from actual image')
  with gzip.open(notice,'rb') as f:data=f.read(64*1024**2+1)
  if not data or len(data)>64*1024**2:raise ValueError('Unbounded NOTICE XML')
  ET.fromstring(data)
 found=[r for r in members['members'] if r.get('role')=='vendor' and r.get('image_path','').endswith('/licenses/prairie-figtree/OFL.txt')]
 if len(found)!=1 or found[0]['sha256']!=sha(regular(font)):raise ValueError('Font redistribution license differs from image')
 print('Actual terminal pair, exact image membership, aggregate NOTICE and Luma material source correspondence PASS')
if __name__=='__main__':
 if len(sys.argv)!=9:raise SystemExit('usage: ROOT PRODUCER MEMBERS MATERIALS SYSTEM-NOTICE VENDOR-NOTICE FONT ARCH')
 if sys.argv[8]!='x86_64':raise ValueError('No current frame pair qualified for this architecture')
 verify(*(Path(p) for p in sys.argv[1:8]))
