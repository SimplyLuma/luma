#!/usr/bin/python3
"""Owned loopback page/DOM observation fixture; never native pixel proof."""
import argparse
import hashlib
import http.server
import json
import os
from pathlib import Path
import secrets
import time

def validate(document,token,url,phase):
    if not isinstance(document,dict) or set(document)!=set(('token','phase','pointers','keys','last_event','href','width','height')):
        raise ValueError('unexpected page-state schema')
    if document['token']!=token or document['href']!=url or type(document['phase']) is not int or document['phase']!=phase:
        raise ValueError('wrong unique page or phase')
    expected=((0,0,'load'),(1,0,'trusted-pointer'),(1,1,'trusted-key'))
    if phase not in range(3) or (document['pointers'],document['keys'],document['last_event'])!=expected[phase]:
        raise ValueError('wrong trusted input progression')
    for name in ('pointers','keys','width','height'):
        if type(document[name]) is not int:raise ValueError('page state requires integer counters/geometry')
    if not 0<=document['width']<=8192 or not 0<=document['height']<=8192:
        raise ValueError('unbounded page geometry')
    return document

def validate_viewport(document,token,url,phase):
    if not isinstance(document,dict) or document.get('last_event')!='viewport':raise ValueError('not a viewport observation')
    copy=dict(document)
    copy['last_event']=('load','trusted-pointer','trusted-key')[phase] if phase in range(3) else None
    validate(copy,token,url,phase)
    return document

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path)
    parser.add_argument('--html-sha256',required=True)
    args=parser.parse_args()
    out=args.output;assert out.is_dir() and out.resolve()==out and out.stat().st_uid==os.getuid()
    html=Path(__file__).with_name('page.html').read_bytes()
    assert hashlib.sha256(html).hexdigest()==args.html_sha256 and len(html)<=16384
    token=secrets.token_hex(16);page='/native-shm-'+token+'?token='+token;endpoint='/native-event-'+token
    def record(name,document):
        fd=os.open(out/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as stream:
            json.dump(document,stream,sort_keys=True);stream.write('\n');stream.flush();os.fsync(stream.fileno())
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self,*_args):pass
        def do_GET(self):
            if self.path!=page:self.send_error(404);return
            if not (out/'page-requested.json').exists():
                record('page-requested.json',dict(url=url,token=token,scope='HTTP request only; not DOM/input/native pixel acceptance'))
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(html)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(html)
        def do_POST(self):
            self.connection.settimeout(5)
            try:
                if self.path!=endpoint or self.headers.get('Origin')!=origin or self.headers.get('Content-Type')!='application/json':
                    raise ValueError('unexpected page reporter')
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=4096 or self.headers.get('Transfer-Encoding'):raise ValueError('unbounded report')
                payload=self.rfile.read(size)
                if len(payload)!=size:raise ValueError('truncated report')
                document=json.loads(payload)
                if isinstance(document,dict) and document.get('last_event')=='viewport':
                    if self.server.viewports>=256:raise ValueError('viewport observation bound reached')
                    document=validate_viewport(document,token,url,self.server.phase-1)
                    observed=dict(document,server_monotonic=time.monotonic(),user_agent=self.headers.get('User-Agent'),scope='Same trusted phase; viewport observation does not advance input proof')
                    record('page-viewport-'+str(self.server.viewports)+'.json',observed)
                    temporary='page-viewport-latest.tmp-'+str(self.server.viewports)
                    record(temporary,observed);os.replace(out/temporary,out/'page-viewport-latest.json')
                    self.server.viewports+=1
                else:
                    document=validate(document,token,url,self.server.phase)
                    record('page-phase-'+str(self.server.phase)+'.json',dict(document,server_monotonic=time.monotonic(),user_agent=self.headers.get('User-Agent'),scope='DOM state only; native target/frame/pixels and compositor event admission remain mandatory'))
                    self.server.phase+=1
                self.send_response(204);self.send_header('Content-Length','0');self.end_headers()
            except (ValueError,TypeError,KeyError,OSError):self.send_error(400)
    server=http.server.HTTPServer(('127.0.0.1',0),Handler);server.phase=0;server.viewports=0
    origin='http://127.0.0.1:'+str(server.server_port);url=origin+page
    record('page-server.json',dict(pid=os.getpid(),url=url,token=token,html_sha256=args.html_sha256,scope='DISPOSABLE_LOOPBACK_FIXTURE_NOT_BROWSER_QUALIFICATION'))
    server.serve_forever()

if __name__=='__main__':main()
