import base64
import hashlib
import importlib.util
import json
import threading
import time
import unittest
from urllib.parse import parse_qs,urlsplit,urlencode
from luma_continuity.account import AccountConfig,AccountError

AVAILABLE=importlib.util.find_spec('authlib') is not None

@unittest.skipUnless(AVAILABLE,'Authlib/joserfc protocol-test environment required')
class LoginTests(unittest.TestCase):
    def test_browser_callback_pkce_and_validated_identity(self):
        import httpx
        from joserfc import jwt
        from joserfc.jwk import RSAKey
        from luma_continuity.login import BrowserLogin
        config=AccountConfig('https://identity.example.test/realms/luma','https://api.example.test','native-test')
        key=RSAKey.generate_key(2048,parameters={'kid':'fixture'})
        requests=[];saved=[];identity={'issuer':config.issuer,'subject':'fixture','account_id':'fixture','session_id':'session-fixture'}
        auth={}
        def server(request):
            requests.append(request.url.path)
            if request.url.path.endswith('/.well-known/openid-configuration'):
                return httpx.Response(200,json={'issuer':config.issuer,'authorization_endpoint':config.issuer+'/auth',
                    'token_endpoint':config.issuer+'/token','jwks_uri':config.issuer+'/certs',
                    'code_challenge_methods_supported':['S256'],'authorization_response_iss_parameter_supported':True})
            if request.url.path.endswith('/certs'): return httpx.Response(200,json={'keys':[key.as_dict(private=False)]})
            if request.url.path.endswith('/token'):
                fields=parse_qs(request.content.decode())
                self.assertEqual(fields['grant_type'],['authorization_code'])
                self.assertEqual(fields['redirect_uri'],auth['redirect_uri'])
                verifier=fields['code_verifier'][0]
                challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
                self.assertEqual(challenge,auth['code_challenge'][0])
                claims={'iss':config.issuer,'sub':'fixture','aud':config.client_id,'sid':'session-fixture',
                    'iat':int(time.time()),'exp':int(time.time())+60,'nonce':auth['nonce'][0]}
                return httpx.Response(200,json={'access_token':'synthetic-access','token_type':'Bearer','expires_in':60,
                    'id_token':jwt.encode({'alg':'RS256','kid':'fixture'},claims,key)})
            raise AssertionError(request.url.path)
        class API:
            def request(self,*_): return identity
        class Tokens:
            def set(self,token): saved.append(token)
        flow=BrowserLogin(config,API(),Tokens(),http_transport=httpx.MockTransport(server))
        url=flow.prepare('fixture@example.test');auth.update(parse_qs(urlsplit(url).query))
        self.assertEqual(auth['code_challenge_method'],['S256'])
        self.assertEqual(auth['scope'], ['openid profile email offline_access'])
        self.assertNotIn('code_verifier',auth)
        callback=urlsplit(auth['redirect_uri'][0])
        self.assertEqual((callback.scheme,callback.hostname,callback.path),('http','127.0.0.1','/callback'))
        self.assertEqual(callback.port,flow.server.server_port)
        self.assertGreater(callback.port,0)
        results=[];failures=[]
        def finish():
            try: results.append(flow.finish())
            except Exception as error: failures.append(error)
        worker=threading.Thread(target=finish);worker.start()
        with httpx.Client(trust_env=False) as client:
            wrong_path=client.get(flow.redirect_uri+'/unregistered?'+urlencode({'code':'fixture','state':auth['state'][0],'iss':config.issuer}))
            self.assertEqual(wrong_path.status_code,400);self.assertEqual(saved,[])
            bad=client.get(flow.redirect_uri+'?'+urlencode({'code':'fixture','state':'wrong','iss':config.issuer}))
            self.assertEqual(bad.status_code,400);self.assertEqual(saved,[])
            good=client.get(flow.redirect_uri+'?'+urlencode({'code':'fixture','state':auth['state'][0],'iss':config.issuer}))
            self.assertEqual(good.status_code,200)
        worker.join(5);self.assertFalse(worker.is_alive());self.assertEqual(failures,[])
        self.assertEqual(results,[identity]);self.assertEqual(len(saved),1)
        self.assertIsNone(flow.server);self.assertIsNone(flow.verifier)

    def test_wrong_nonce_audience_issuer_and_expiry_rejected(self):
        from joserfc import jwt
        from joserfc.jwk import RSAKey
        from luma_continuity.login import validate_id_token
        config=AccountConfig('https://identity.example.test/realms/luma','https://api.example.test','native-test')
        key=RSAKey.generate_key(2048,parameters={'kid':'fixture'})
        keys={'keys':[key.as_dict(private=False)]}
        claims={'iss':config.issuer,'sub':'fixture','aud':config.client_id,'iat':1000,'exp':1100,'nonce':'expected'}
        for field,bad in [('nonce','wrong'),('aud','other'),('iss','https://evil.example.test'),('exp',900)]:
            encoded=jwt.encode({'alg':'RS256','kid':'fixture'},{**claims,field:bad},key)
            with self.assertRaises(AccountError): validate_id_token(encoded,keys,config,'expected','synthetic',now=1000)

    def test_refresh_rotates_keyring_token_and_rejects_identity_change(self):
        import httpx
        from luma_continuity.login import BrowserLogin
        config=AccountConfig('https://identity.example.test/realms/luma','https://api.example.test','native-test')
        identity={'issuer':config.issuer,'subject':'fixture','session_id':'here'};saved=[]
        def server(request):
            if request.url.path.endswith('openid-configuration'):
                return httpx.Response(200,json={'issuer':config.issuer,'token_endpoint':config.issuer+'/token'})
            fields=parse_qs(request.content.decode())
            self.assertEqual(fields['grant_type'],['refresh_token'])
            self.assertEqual(fields['refresh_token'],['old-refresh'])
            return httpx.Response(200,json={'access_token':'new-access','refresh_token':'new-refresh','token_type':'Bearer','expires_in':120})
        class API:
            def request(self,*_):return identity
        class Tokens:
            def set(self,value):saved.append(value)
        prior={'access_token':'old-access','refresh_token':'old-refresh','_identity':dict(identity),'_nonce':'nonce'}
        flow=BrowserLogin(config,API(),Tokens(),http_transport=httpx.MockTransport(server))
        self.assertEqual(flow.renew(prior)['refresh_token'],'new-refresh');self.assertEqual(len(saved),2)
        identity['subject']='other-account'
        with self.assertRaises(AccountError):flow.renew(prior)
        self.assertEqual(len(saved),3)
        self.assertTrue(saved[-1]['_pending_verification'])

    def test_refresh_nonce_optional_but_present_value_must_match(self):
        from joserfc import jwt
        from joserfc.jwk import RSAKey
        from luma_continuity.login import validate_id_token
        config=AccountConfig('https://identity.example.test/realms/luma','https://api.example.test','native-test')
        key=RSAKey.generate_key(2048,parameters={'kid':'fixture'})
        keys={'keys':[key.as_dict(private=False)]}
        claims={'iss':config.issuer,'sub':'fixture','aud':config.client_id,'iat':1000,'exp':1100}
        def token(values):return jwt.encode({'alg':'RS256','kid':'fixture'},values,key)
        with self.assertRaises(AccountError):validate_id_token(token(claims),keys,config,'original','synthetic',now=1000)
        self.assertEqual(validate_id_token(token(claims),keys,config,'original','synthetic',now=1000,refreshed=True)['sub'],'fixture')
        with self.assertRaises(AccountError):
            validate_id_token(token({**claims,'nonce':'wrong'}),keys,config,'original','synthetic',now=1000,refreshed=True)
        with self.assertRaises(AccountError):
            validate_id_token(token({**claims,'aud':'wrong'}),keys,config,'original','synthetic',now=1000,refreshed=True)

    def test_incomplete_callback_headers_cannot_hold_login_past_deadline(self):
        import httpx,socket
        from luma_continuity.login import BrowserLogin
        config=AccountConfig('https://identity.example.test/realm','https://api.example.test','fixture')
        def server(request):
            return httpx.Response(200,json={'issuer':config.issuer,'authorization_endpoint':config.issuer+'/auth',
                'token_endpoint':config.issuer+'/token','jwks_uri':config.issuer+'/keys','code_challenge_methods_supported':['S256']})
        flow=BrowserLogin(config,None,None,http_transport=httpx.MockTransport(server));flow.prepare()
        failures=[]
        def finish():
            try:flow.finish()
            except AccountError as error:failures.append(error.code)
        with socket.create_connection(('127.0.0.1',flow.server.server_port)) as stalled:
            stalled.sendall(b'GET /unfinished HTTP/1.1\r\nHost:')
            flow.deadline=time.monotonic()+.1
            worker=threading.Thread(target=finish);worker.start();worker.join(2)
            self.assertFalse(worker.is_alive())
        self.assertEqual(failures,['login_expired']);self.assertIsNone(flow.server)

    def test_valid_cancellation_is_consumed_without_token_exchange(self):
        import httpx
        from luma_continuity.login import BrowserLogin
        config=AccountConfig('https://identity.example.test/realm','https://api.example.test','fixture')
        def server(request):
            self.assertTrue(request.url.path.endswith('openid-configuration'))
            return httpx.Response(200,json={'issuer':config.issuer,'authorization_endpoint':config.issuer+'/auth',
                'token_endpoint':config.issuer+'/token','jwks_uri':config.issuer+'/keys','code_challenge_methods_supported':['S256']})
        flow=BrowserLogin(config,None,None,http_transport=httpx.MockTransport(server));flow.prepare()
        failures=[]
        def finish():
            try:flow.finish()
            except AccountError as error:failures.append(error.code)
        redirect,state=flow.redirect_uri,flow.state
        worker=threading.Thread(target=finish);worker.start()
        with httpx.Client(trust_env=False) as client:
            # Supplied mismatched issuer must fail even if discovery does not
            # advertise the response-issuer extension.
            bad=client.get(redirect+'?'+urlencode({'error':'access_denied','state':state,'iss':'https://wrong.example.test'}))
            self.assertEqual(bad.status_code,400);self.assertEqual(failures,[])
            good=client.get(redirect+'?'+urlencode({'error':'access_denied','state':state,'iss':config.issuer}))
            self.assertEqual(good.status_code,200)
        worker.join(2);self.assertFalse(worker.is_alive())
        self.assertEqual(failures,['login_cancelled']);self.assertIsNone(flow.server)

class RotationRecoveryTests(unittest.TestCase):
    def test_outage_after_rotation_keeps_new_refresh_for_verification(self):
        import httpx
        from luma_continuity.login import BrowserLogin
        config=AccountConfig('https://identity.example.test/realms/luma','https://api.example.test','native-test')
        identity={'issuer':config.issuer,'subject':'fixture','account_id':'account','session_id':'here'}
        saved=[];rotations=[];offline=[True]
        def server(request):
            if request.url.path.endswith('openid-configuration'):
                return httpx.Response(200,json={'issuer':config.issuer,'token_endpoint':config.issuer+'/token'})
            rotations.append(True)
            return httpx.Response(200,json={'access_token':'new-access','refresh_token':'new-refresh','token_type':'Bearer','expires_in':120})
        class API:
            def request(self,*_):
                if offline[0]:raise AccountError('offline')
                return identity
        class Tokens:
            def set(self,value):saved.append(dict(value))
        prior={'access_token':'old-access','refresh_token':'old-refresh','_identity':identity,'_nonce':'nonce'}
        flow=BrowserLogin(config,API(),Tokens(),http_transport=httpx.MockTransport(server))
        with self.assertRaises(AccountError):flow.renew(prior)
        self.assertEqual(saved[-1]['refresh_token'],'new-refresh')
        self.assertTrue(saved[-1]['_pending_verification'])
        offline[0]=False
        recovered=BrowserLogin(config,API(),Tokens(),http_transport=httpx.MockTransport(server)).verify_pending(saved[-1])
        self.assertNotIn('_pending_verification',recovered)
        self.assertEqual(len(rotations),1)
