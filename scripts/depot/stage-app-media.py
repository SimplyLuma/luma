#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Stage vendor screenshots from the exact app commit about to be signed.

No URL is downloaded, no source-directory image is trusted, and an existing
media object is never replaced with different bytes. CDN publication is a
separate operation performed only after the complete release is approved.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import struct
import zlib
import xml.etree.ElementTree as ET

def object_bytes(repo, commit, name, limit):
    process = subprocess.Popen(['ostree', 'cat', '--repo='+str(repo), commit, name],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        data = process.stdout.read(limit+1)
        if len(data)>limit:
            process.kill(); raise ValueError('Application media object exceeds its size limit')
        error = process.stderr.read(65536)
        if process.wait()!=0: raise ValueError('Application commit lacks its declared media object: '+error.decode(errors='replace'))
        return data
    finally:
        if process.poll() is None: process.kill(); process.wait()

def directory(path):
    if not path.exists(): path.mkdir(mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode & 0o022:
        raise ValueError('Application media staging directory is unsafe')

def png_structure(data):
    """Bounded structural validation; actual decoded/visual proof is keyless."""
    if not data.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('Screenshot is not a PNG')
    offset, count, width, height, idat = 8, 0, 0, 0, False
    while offset < len(data):
        if count >= 2048 or len(data)-offset < 12:
            raise ValueError('Screenshot PNG structure is incomplete')
        length, kind = struct.unpack('>I4s',data[offset:offset+8])
        if length > 10485760 or offset+length+12 > len(data):
            raise ValueError('Screenshot PNG chunk exceeds its size bound')
        body = data[offset+8:offset+8+length]
        crc = struct.unpack('>I',data[offset+8+length:offset+12+length])[0]
        if zlib.crc32(kind+body)&0xffffffff != crc:
            raise ValueError('Screenshot PNG chunk checksum differs')
        if count == 0:
            if kind != b'IHDR' or length != 13:
                raise ValueError('Screenshot PNG has no image header')
            width,height,depth,color,compression,filter_mode,interlace=struct.unpack('>IIBBBBB',body)
            valid_depths={0:{1,2,4,8,16},2:{8,16},3:{1,2,4,8},4:{8,16},6:{8,16}}
            if (not 1 <= width <= 8192 or not 1 <= height <= 8192
                    or width*height > 16777216 or depth not in valid_depths.get(color,set())
                    or compression != 0 or filter_mode != 0 or interlace not in (0,1)):
                raise ValueError('Screenshot PNG dimensions or encoding are unsupported')
        elif kind == b'IHDR':
            raise ValueError('Screenshot PNG repeats its image header')
        if kind == b'IDAT': idat = idat or bool(length)
        offset += length+12; count += 1
        if kind == b'IEND':
            if length or offset != len(data) or not idat:
                raise ValueError('Screenshot PNG has an invalid end or no image data')
            return width,height
    raise ValueError('Screenshot PNG has no image end')

def stage(repo, commit, app_id, site, base):
    if not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_-]*(\.[a-zA-Z0-9_-]+){2,}', app_id):
        raise ValueError('Invalid application identity')
    if not re.fullmatch(r'[0-9a-f]{64}', commit): raise ValueError('Invalid application commit')
    source = object_bytes(repo, commit, '/files/share/metainfo/'+app_id+'.metainfo.xml', 1048576)
    if b'<!DOCTYPE' in source or b'<!ENTITY' in source: raise ValueError('Unexpected metainfo declaration')
    root = ET.fromstring(source)
    if root.findtext('id')!=app_id: raise ValueError('Metainfo application identity differs')
    images = root.findall('./screenshots/screenshot/image')
    if not images or len(images) > 16:
        raise ValueError('Application release needs at least one bounded real screenshot')
    directory(site); directory(site/'media'); directory(site/'media'/app_id)
    staged = []
    prefix = base.rstrip('/')+'/media/'+app_id+'/'
    for image in images:
        url = image.text or ''
        if not url.startswith(prefix): raise ValueError('Vendor screenshot has no maintained immutable media path')
        digest = url[len(prefix):]
        if not re.fullmatch(r'[0-9a-f]{64}\.png', digest): raise ValueError('Media path must contain only its full SHA256')
        data = object_bytes(repo, commit, '/files/share/luma-app-media/'+digest, 10485760)
        if hashlib.sha256(data).hexdigest()!=digest[:-4] or not data.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError('Screenshot bytes differ from the exact declared image')
        width,height = png_structure(data)
        for key,value in (('width',width),('height',height)):
            if image.get(key) is not None and image.get(key) != str(value):
                raise ValueError('Screenshot dimensions differ from signed metadata')
        target = site/'media'/app_id/digest
        if target.exists() or target.is_symlink():
            info=target.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode & 0o022:
                raise ValueError('Existing immutable media object is unsafe')
            if target.read_bytes()!=data: raise ValueError('An immutable screenshot path already contains different bytes')
        else:
            fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644)
            with os.fdopen(fd,'wb') as stream: stream.write(data); stream.flush(); os.fsync(stream.fileno())
        staged.append({'url':url,'path':str(target),'sha256':digest[:-4],'bytes':len(data),'width':width,'height':height})
    return {'schema':'org.projectluma.app-media-stage/v1','app_id':app_id,
            'source_commit':commit,'result':'PASS','public_sync_executed':False,'images':staged}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,required=True);parser.add_argument('--commit',required=True)
    parser.add_argument('--app-id',required=True);parser.add_argument('--site',type=Path,required=True)
    parser.add_argument('--base-url',default='https://dl.simplyluma.com')
    args=parser.parse_args();print(json.dumps(stage(args.repo,args.commit,args.app_id,args.site,args.base_url),sort_keys=True))
