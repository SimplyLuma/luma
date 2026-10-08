"""Real HTTPS/SSE/WSS + pinned inner TLS, disposable synthetic principals only.

Run with the completed server source on PYTHONPATH. No SSH, real account,
recipient, modem, system credential store, or production endpoint is used.
"""
from datetime import datetime,timedelta,timezone
from pathlib import Path
import json
import socket
import ssl
import tempfile
import threading
import time
import uuid
import httpx
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from connect.app import create_app
from connect.auth import Principal,AuthError
from connect.db import Database
from luma_continuity.account import AccountAPI,AccountConfig
from luma_continuity.bootstrap import create_identity,approve_peer
from luma_continuity.relay import connect_wss
from luma_continuity.relay_binding import RelayBinding
from luma_continuity.relay_runtime import RelayRuntime


def wait(predicate,seconds=10):
    deadline=time.monotonic()+seconds
    while not predicate():
        if time.monotonic()>deadline:raise AssertionError('owned relay fixture timed out')
        time.sleep(.02)


with tempfile.TemporaryDirectory(prefix='luma-owned-relay-') as temporary:
    root=Path(temporary)
    key=ec.generate_private_key(ec.SECP256R1());name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')])
    now=datetime.now(timezone.utc)
    certificate=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1))
        .not_valid_after(now+timedelta(hours=1)).add_extension(x509.BasicConstraints(ca=True,path_length=0),critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]),critical=False)
        .sign(key,hashes.SHA256()))
    cert=root/'localhost.pem';private=root/'localhost.key'
    cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    private.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()));private.chmod(0o600)
    trusted=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT);trusted.load_verify_locations(cafile=str(cert))
    listener=socket.socket();listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    origin=f'https://localhost:{port}';issuer='https://identity.invalid/realms/fixture'
    class Verifier:
        def verify(self,token):
            if token not in {'fixture-left','fixture-right'}:raise AuthError()
            return Principal('fixture-account',issuer,token,frozenset({'openid'}))
    database=Database('sqlite:///'+str(root/'server.sqlite'));database.migrate()
    app=create_app(database,Verifier(),allowed_hosts=('localhost',),relay_url=f'wss://localhost:{port}/v1/relay')
    server=uvicorn.Server(uvicorn.Config(app,ssl_certfile=str(cert),ssl_keyfile=str(private),log_level='error',access_log=False,timeout_graceful_shutdown=2))
    thread=threading.Thread(target=lambda:server.run(sockets=[listener]),daemon=True);thread.start();wait(lambda:server.started)
    runtimes=[];clients=[]
    try:
        clients=[httpx.Client(verify=trusted,trust_env=False,timeout=5) for _ in range(2)]
        left,right=root/'left',root/'right';lp=create_identity(left);rp=create_identity(right)
        def request(index,method,path,body=None):
            response=clients[index].request(method,origin+path,headers={'Authorization':'Bearer '+('fixture-left' if index==0 else 'fixture-right')},json=body)
            if response.status_code not in {200,201}:raise AssertionError((method,path,response.status_code))
            return response.json()
        account=request(0,'GET','/v1/me')['account_id'];devices=[]
        for index,pin in enumerate((lp,rp)):
            devices.append(request(index,'POST','/v1/devices',{'name':'fixture','certificate_sha256':pin})['id'])
        pair=request(0,'POST','/v1/pairings',{'device_id':devices[0],'peer_device_id':devices[1]})['id']
        for index,pin in enumerate((rp,lp)):
            request(index,'POST',f'/v1/pairings/{pair}/approval',{'device_id':devices[index],'peer_certificate_sha256':pin,'approved':True})
        epoch='e'*32
        approve_peer(left,right/'device.pem',rp,epoch,['messages.read','messages.send'],account=account)
        approve_peer(right,left/'device.pem',lp,epoch,['messages.read','messages.send'],account=account)
        effects=[]
        def adapter(_binding,allowed):
            def invoke(capability,payload):
                if not allowed():raise PermissionError('fixture revoked')
                if capability=='messages.send':effects.append(payload['operation']);return {'uid':'synthetic-native','state':'sent'}
                return {'records':[],'truncated':False}
            return invoke,lambda:None
        for index,(directory,peer,role) in enumerate(((left,rp,'requester'),(right,lp,'receiver'))):
            api=AccountAPI(AccountConfig(issuer,origin,'fixture-native'),client=clients[index])
            binding=RelayBinding(account,peer,epoch,devices[index],pair,role)
            runtimes.append(RelayRuntime(directory,[binding],api,bearer=lambda i=index:'fixture-left' if i==0 else 'fixture-right',
                account=lambda:account,adapter_factory=adapter,
                websocket_connector=lambda url,ticket,**kw:connect_wss(url,ticket,tls_context=trusted,**kw)))
        for runtime in runtimes:runtime.resume()
        runtimes[1].enable_sharing(lp)
        wait(lambda:all(runtime.directory_state.cursor for runtime in runtimes))
        operation=uuid.uuid4().hex
        envelope=dict(version=1,epoch=epoch,id=operation,account=account,capability='messages.send',
                      expires=int(time.time())+120,payload={'address':'+12025550123','body':'synthetic fixture only'})
        encoded=json.dumps(envelope)
        first=runtimes[0].exchange(rp,encoded)
        assert first['state']=='complete',first
        assert runtimes[0].exchange(rp,encoded)==first
        assert effects==[operation]
        old=runtimes[0].requesters[rp].session
        runtimes[0].api.close_session(old)
        def closed():
            with runtimes[0].lock:
                return pair not in runtimes[0].directory_state.sessions and runtimes[0].requesters[rp].session is None
        wait(closed)
        assert runtimes[0].exchange(rp,encoded)==first
        assert effects==[operation]
        assert runtimes[0].requesters[rp].session.session_id!=old.session_id
        # Restart the receiver owner from its saved explicit intent. No share
        # command, re-pairing, new operation ID or SSH connection is supplied.
        runtimes[1].close()
        binding=RelayBinding(account,lp,epoch,devices[1],pair,'receiver')
        runtimes[1]=RelayRuntime(right,[binding],AccountAPI(AccountConfig(issuer,origin,'fixture-native'),client=clients[1]),
            bearer=lambda:'fixture-right',account=lambda:account,adapter_factory=adapter,
            websocket_connector=lambda url,ticket,**kw:connect_wss(url,ticket,tls_context=trusted,**kw))
        assert lp in runtimes[1].sharing_peers
        runtimes[1].resume();wait(lambda:runtimes[1].directory_state.cursor is not None)
        wait(closed)
        try:assert runtimes[0].exchange(rp,encoded)==first
        except Exception:
            print('fixture transport states',[(r.last_error,len(r.receivers),len(r.directory_state.sessions)) for r in runtimes])
            raise
        assert effects==[operation]
        old_responses=[runtime.api._event_response for runtime in runtimes]
        server.should_exit=True;thread.join(8);assert not thread.is_alive()
        listener=socket.socket();listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        listener.bind(('127.0.0.1',port))
        app=create_app(database,Verifier(),allowed_hosts=('localhost',),relay_url=f'wss://localhost:{port}/v1/relay')
        server=uvicorn.Server(uvicorn.Config(app,ssl_certfile=str(cert),ssl_keyfile=str(private),log_level='critical',access_log=False,timeout_graceful_shutdown=2))
        thread=threading.Thread(target=lambda:server.run(sockets=[listener]),daemon=True);thread.start();wait(lambda:server.started)
        wait(lambda:all(runtime.api._event_response is not None and runtime.api._event_response is not old_responses[i]
            and runtime.last_error is None for i,runtime in enumerate(runtimes)),seconds=15)
        assert runtimes[0].exchange(rp,encoded)==first
        assert effects==[operation]
        print('PASS real HTTPS/SSE/WSS + pinned inner TLS; automatic receiver rendezvous; session replacement, receiver restart and relay-server restart retain one operation/effect; no SSH/carrier')
    finally:
        for runtime in runtimes:runtime.close()
        for client in clients:client.close()
        server.should_exit=True;thread.join(8);listener.close();database.engine.dispose()
