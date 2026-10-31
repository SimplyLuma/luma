# SPDX-License-Identifier: Apache-2.0
"""Bounded resumable multipart transport, never publication or qualification.

The caller retains release locks, root credential custody and full public body
readback. client_factory creates an independent normal R2 client for each part.
No request URL, credential, upload ID or ETag is printed.
"""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from pathlib import Path
import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
import time
import xml.etree.ElementTree as ET

PART_SIZE = 32 * 1024**2


def file_identity(path):
    s = Path(path).lstat()
    if not stat.S_ISREG(s.st_mode): raise ValueError('source must be a regular file')
    return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns]


def sha256(path):
    with Path(path).open('rb') as stream: return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, document):
    fd, temporary = tempfile.mkstemp(prefix=path.name+'.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(document, stream, sort_keys=True); stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def multipart_upload(r2, key, path, part_size, content_type, *, client_factory,
                     expected_sha256, state_dir, workers=4, attempts=5,
                     progress=print, pause=time.sleep):
    """Upload with at most four chunks outstanding; resume only strong v2 state.

    Old size/second-mtime-only checkpoints are deliberately left intact for their
    original uploader. They cannot silently become source-SHA authenticated state.
    The public body SHA readback remains mandatory at the caller after completion.
    """
    if not 1 <= workers <= 4 or not 1 <= part_size <= PART_SIZE or not 1 <= attempts <= 10:
        raise ValueError('multipart worker/chunk/retry budget is invalid')
    if not re.fullmatch('[0-9a-f]{64}', expected_sha256): raise ValueError('source SHA256 is required')
    if not key or any(c in key for c in '\r\n\0'): raise ValueError('invalid object key')
    path = Path(path); source = file_identity(path); size = source[2]
    if size == 0: raise ValueError('multipart source is empty')
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    st = state_dir.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o077:
        raise ValueError('checkpoint directory custody differs')
    digest = hashlib.sha256(f'{r2.bucket}/{key}'.encode()).hexdigest()[:32]
    checkpoint = state_dir/(digest+'.json')
    lock = os.open(state_dir/(digest+'.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if sha256(path) != expected_sha256 or file_identity(path) != source:
            raise ValueError('source changed or differs from authenticated SHA256')
        total = (size+part_size-1)//part_size
        done = {}
        if checkpoint.exists():
            st = checkpoint.lstat()
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o077:
                raise ValueError('checkpoint file custody differs')
            state = json.loads(checkpoint.read_text())
            if state.get('version') != 2:
                raise ValueError('legacy checkpoint preserved: resume it with its original uploader')
            if (state.get('key'), state.get('size'), state.get('part_size'), state.get('source_sha256')) != (key,size,part_size,expected_sha256):
                raise ValueError('checkpoint belongs to another source or part layout')
            for number, etag in state['parts']:
                if type(number) is not int or not 1<=number<=total or number in done or not isinstance(etag,str) or not etag or len(etag)>256 or any(c in etag for c in '\r\n'):
                    raise ValueError('invalid accepted part checkpoint')
                done[number] = etag
            upload_id = state['upload_id']
            if not isinstance(upload_id,str) or not upload_id: raise ValueError('invalid upload checkpoint')
        else:
            _,_,body = r2.call('POST',key,query={'uploads':''},extra_headers={
                'content-type':content_type,'content-disposition':f'attachment; filename="{path.name}"'})
            upload_id = next(e.text for e in ET.fromstring(body).iter() if e.tag.split('}')[-1]=='UploadId')
            if not upload_id: raise ValueError('server returned no upload ID')
        def checkpoint_now():
            save(checkpoint,dict(version=2,key=key,size=size,mtime=int(path.stat().st_mtime),
                                source_identity=source,source_sha256=expected_sha256,part_size=part_size,
                                upload_id=upload_id,parts=sorted(done.items())))
        checkpoint_now()
        def upload(number):
            client = client_factory()
            if client.bucket != r2.bucket: raise ValueError('part client uses a different bucket')
            if file_identity(path) != source: raise ValueError('source changed before part read')
            with path.open('rb') as stream:
                stream.seek((number-1)*part_size); chunk=stream.read(min(part_size,size-(number-1)*part_size))
            if len(chunk)!=min(part_size,size-(number-1)*part_size) or file_identity(path)!=source:
                raise ValueError('source changed during part read')
            for attempt in range(1,attempts+1):
                try:
                    _,headers,_=client.call('PUT',key,query={'partNumber':str(number),'uploadId':upload_id},body=chunk)
                    etag=headers.get('ETag') or headers.get('etag')
                    if not isinstance(etag,str) or not etag or len(etag)>256 or any(c in etag for c in '\r\n'):
                        raise ValueError('part response has no valid ETag')
                    return number,etag
                except (Exception,SystemExit):
                    if attempt==attempts: raise RuntimeError(f'part {number} failed after {attempts} attempts') from None
                    pause(min(2**attempt,30))
        missing=iter(n for n in range(1,total+1) if n not in done)
        failures=[]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            pending={}
            def refill():
                while len(pending)<workers and not failures:
                    number=next(missing,None)
                    if number is None:break
                    pending[pool.submit(upload,number)]=number
            refill()
            while pending:
                ready,_=wait(pending,return_when=FIRST_COMPLETED)
                for future in ready:
                    pending.pop(future)
                    try:
                        number,etag=future.result();done[number]=etag;checkpoint_now()
                        sent=sum(min(part_size,size-(n-1)*part_size) for n in done)
                        progress(f'accepted {len(done)}/{total} parts; {sent}/{size} bytes',flush=True)
                    except BaseException as error:failures.append(error)
                refill()
        if failures:raise RuntimeError('multipart stopped; accepted parts are durably checkpointed') from None
        if set(done)!=set(range(1,total+1)) or file_identity(path)!=source:
            raise ValueError('source changed or complete accepted part set is missing')
        complete=ET.Element('CompleteMultipartUpload')
        for number in sorted(done):
            part=ET.SubElement(complete,'Part');ET.SubElement(part,'PartNumber').text=str(number);ET.SubElement(part,'ETag').text=done[number]
        _,_,response=r2.call('POST',key,query={'uploadId':upload_id},body=ET.tostring(complete),extra_headers={'content-type':'application/xml'})
        if ET.fromstring(response).tag.split('}')[-1]!='CompleteMultipartUploadResult':
            raise ValueError('multipart completion was not accepted; checkpoint preserved')
        if file_identity(path)!=source:raise ValueError('source changed during completion')
        checkpoint.unlink()
        directory=os.open(state_dir,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(directory)
        finally:os.close(directory)
        return size
    finally:
        os.close(lock)
