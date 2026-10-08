"""External-browser OIDC Authorization Code + S256 PKCE, Authlib/joserfc.

Credentials are persisted only after ID-token and API identity agreement.
The loopback listener serves only a one-use callback, not a remote API.
"""
import base64
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import secrets
import ssl
import socket
import threading
import time
from urllib.parse import parse_qs, urlsplit
from .account import AccountError


def validate_id_token(encoded, keys, config, nonce, access_token, *, now=None, refreshed=False):
    from joserfc import jwt
    from joserfc.jwk import KeySet
    from joserfc.jwt import JWTClaimsRegistry
    if (not isinstance(encoded, str) or len(encoded) > 32768
            or not isinstance(keys, dict) or not isinstance(keys.get('keys'),list)
            or not 1 <= len(keys['keys']) <= 16 or any('d' in key for key in keys['keys'])):
        raise AccountError('invalid_identity_token')
    try:
        token = jwt.decode(encoded, KeySet.import_key_set(keys), algorithms=['RS256'])
        claims = token.claims
        JWTClaimsRegistry(now=now, leeway=30,
            iss={'essential':True,'value':config.issuer}, aud={'essential':True,'value':config.client_id},
            sub={'essential':True}, exp={'essential':True}, iat={'essential':True},
            # OIDC Core12.2: a refreshed ID token may omit nonce. If present,
            # it must retain the original value. Initial login still requires it.
            nonce={'essential':not refreshed,'value':nonce}).validate(claims)
        audience = claims['aud']
        if (isinstance(audience,list) and len(audience)>1 or 'azp' in claims) and claims.get('azp') != config.client_id:
            raise ValueError('authorized party')
        if 'at_hash' in claims:
            expected = base64.urlsafe_b64encode(hashlib.sha256(access_token.encode('ascii')).digest()[:16]).rstrip(b'=').decode()
            if not isinstance(claims['at_hash'],str) or not hmac.compare_digest(claims['at_hash'],expected):
                raise ValueError('access token binding')
        return claims
    except Exception:
        raise AccountError('invalid_identity_token') from None


class BrowserLogin:
    def __init__(self, config, api, tokens, *, http_transport=None):
        self.config, self.api, self.tokens = config, api, tokens
        self.http_transport = http_transport  # isolated protocol tests only
        self.server = None
        self.client = None
        self.response = None
        self.callback_error = None
        self.deadline = 0
        self.verifier = None
        self.nonce = None
        self.state = None

    def _endpoint(self, value):
        source, target = urlsplit(self.config.issuer), urlsplit(value)
        if (target.scheme != 'https' or target.netloc != source.netloc or target.username
                or target.password or target.query or target.fragment):
            raise AccountError('untrusted_identity_endpoint')
        return value

    def _json(self, url):
        deadline=time.monotonic()+10
        with self.client.stream('GET', url, withhold_token=True,headers={'Accept-Encoding':'identity'}) as response:
            if response.status_code != 200: raise AccountError('identity_unavailable')
            if response.headers.get('Content-Encoding','identity').lower()!='identity':raise AccountError('invalid_identity_metadata')
            data = bytearray()
            for chunk in ((response.content,) if response.is_stream_consumed else response.iter_raw()):
                if time.monotonic()>=deadline:raise AccountError('identity_unavailable')
                data.extend(chunk)
                if len(data)>262144: raise AccountError('invalid_identity_metadata')
            result=json.loads(data)
            if not isinstance(result,dict): raise AccountError('invalid_identity_metadata')
            return result

    def prepare(self, email=''):
        from authlib.integrations.httpx_client import OAuth2Client
        if self.server is not None: raise AccountError('login_already_pending')
        if not isinstance(email,str) or len(email)>254 or '\x00' in email: raise AccountError('invalid_email',400)
        owner=self
        class Callback(BaseHTTPRequestHandler):
            def log_message(self,*_): pass  # authorization codes must not reach logs
            def handle(self):
                try:super().handle()
                except (ConnectionError,TimeoutError):pass  # local peer closed or deadline expired
            def do_GET(self):
                parsed=urlsplit(self.path)
                query=parse_qs(parsed.query,keep_blank_values=True)
                valid=(self.headers.get('Host') == f'127.0.0.1:{owner.server.server_port}'
                    and parsed.path == owner.path and not parsed.fragment
                    and len(self.path)<=8192 and time.monotonic()<owner.deadline
                    and owner.response is None and len(query.get('state',[]))==1
                    and hmac.compare_digest(query['state'][0],owner.state)
                    and ((len(query.get('code',[]))==1 and bool(query['code'][0]) and 'error' not in query)
                         or (len(query.get('error',[]))==1 and bool(query['error'][0]) and 'code' not in query))
                    and not any(len(values)!=1 for values in query.values())
                    and ('iss' not in query or query['iss'] == [owner.config.issuer])
                    and (not owner.metadata.get('authorization_response_iss_parameter_supported')
                         or query.get('iss') == [owner.config.issuer]))
                if valid:
                    owner.response=owner.redirect_uri+'?'+parsed.query
                    if 'error' in query:
                        owner.callback_error='login_cancelled' if query['error']==['access_denied'] else 'login_failed'
                self.send_response(200 if valid else 400)
                self.send_header('Content-Type','text/plain; charset=utf-8')
                self.send_header('Cache-Control','no-store')
                self.send_header('Referrer-Policy','no-referrer')
                self.send_header('Content-Security-Policy',"default-src 'none'")
                self.end_headers()
                body=b'Sign-in response rejected.'
                if valid:body=b'Sign-in was cancelled. Return to Luma Connect.' if owner.callback_error=='login_cancelled' else b'Return to Luma Connect.'
                self.wfile.write(body)
        class CallbackServer(HTTPServer):
            def get_request(self):
                connection,address=super().get_request()
                # Bound a peer that connects but never finishes HTTP headers.
                # HTTPServer.timeout alone only bounds waiting for accept.
                connection.settimeout(0.5)
                def expire():
                    try:connection.shutdown(socket.SHUT_RDWR)
                    except OSError:pass
                # Also bound a slow trickle that repeatedly resets socket I/O
                # timeouts. The callback is tiny and local; one second suffices.
                self.read_timer=threading.Timer(min(1.0,max(.01,owner.deadline-time.monotonic())),expire)
                self.read_timer.daemon=True;self.read_timer.start()
                return connection,address
            def close_request(self,request):
                self.read_timer.cancel()
                super().close_request(request)
        self.server=CallbackServer(('127.0.0.1',0),Callback)
        self.server.timeout=0.5
        # Exact registered path; the OS chooses the loopback port. State,
        # nonce and PKCE bind the single-use response to this login attempt.
        self.path='/callback'
        self.redirect_uri=f'http://127.0.0.1:{self.server.server_port}'+self.path
        tls=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT);tls.load_default_certs();tls.minimum_version=ssl.TLSVersion.TLSv1_2
        self.client=OAuth2Client(self.config.client_id,scope=self.config.scope,redirect_uri=self.redirect_uri,
            code_challenge_method='S256',token_endpoint_auth_method='none',verify=tls,
            timeout=5,trust_env=False,follow_redirects=False,transport=self.http_transport)
        try:
            self.metadata=self._json(self.config.issuer+'/.well-known/openid-configuration')
            if self.metadata.get('issuer') != self.config.issuer: raise AccountError('issuer_mismatch')
            if 'S256' not in self.metadata.get('code_challenge_methods_supported',[]): raise AccountError('pkce_unavailable')
            self.authorization_endpoint=self._endpoint(self.metadata['authorization_endpoint'])
            self.token_endpoint=self._endpoint(self.metadata['token_endpoint'])
            self.jwks_uri=self._endpoint(self.metadata['jwks_uri'])
            self.verifier=secrets.token_urlsafe(64);self.nonce=secrets.token_urlsafe(32)
            url,self.state=self.client.create_authorization_url(self.authorization_endpoint,
                code_verifier=self.verifier,nonce=self.nonce,login_hint=email)
            self.deadline=time.monotonic()+180
            return url
        except BaseException:
            self.close();raise

    def finish(self,expected_identity=None):
        try:
            while self.response is None and time.monotonic()<self.deadline:
                self.server.handle_request()
            if self.response is None: raise AccountError('login_expired')
            if self.callback_error: raise AccountError(self.callback_error,400)
            token=self.client.fetch_token(self.token_endpoint,authorization_response=self.response,
                code_verifier=self.verifier,state=self.state)
            if (not isinstance(token.get('access_token'),str)
                    or str(token.get('token_type','')).lower()!='bearer'):
                raise AccountError('invalid_access_token')
            claims=validate_id_token(token.get('id_token'),self._json(self.jwks_uri),self.config,
                self.nonce,token['access_token'])
            identity=self.api.request('GET','/v1/me',token['access_token'])
            if (identity.get('issuer')!=claims['iss'] or identity.get('subject')!=claims['sub']
                    or 'sid' in claims and identity.get('session_id')!=claims['sid']):
                raise AccountError('identity_mismatch')
            if expected_identity is not None:
                # An explicit persistent-session upgrade may change SID, never
                # issuer, user or account. Keep the prior encrypted record if
                # the browser signs into another account or consent is denied.
                if (set(expected_identity)!={'issuer','subject','account_id'}
                    or any(not isinstance(v,str) or not v or identity.get(k)!=v for k,v in expected_identity.items())):
                    raise AccountError('identity_mismatch',401)
                if 'offline_access' not in str(token.get('scope','')).split():
                    raise AccountError('persistent_login_unavailable')
            token['_identity'] = {key:identity[key] for key in ('issuer','subject','session_id')}
            token['_nonce'] = self.nonce
            self.tokens.set(dict(token))
            return identity
        finally: self.close()

    def renew(self, previous):
        """Rotate an expiring token, bound to the previously approved identity."""
        from authlib.integrations.httpx_client import OAuth2Client
        from authlib.integrations.base_client.errors import OAuthError
        if not previous.get('refresh_token') or not isinstance(previous.get('_identity'),dict):
            raise AccountError('sign_in_required',401)
        tls=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT);tls.load_default_certs()
        self.client=OAuth2Client(self.config.client_id,token_endpoint_auth_method='none',
            verify=tls,timeout=5,trust_env=False,follow_redirects=False,transport=self.http_transport)
        try:
            metadata=self._json(self.config.issuer+'/.well-known/openid-configuration')
            if metadata.get('issuer')!=self.config.issuer:raise AccountError('issuer_mismatch')
            token=self.client.refresh_token(self._endpoint(metadata['token_endpoint']),
                refresh_token=previous['refresh_token'])
            if not token.get('access_token') or str(token.get('token_type','')).lower()!='bearer':
                raise AccountError('invalid_access_token')
            # Refresh-token rotation is already committed by the issuer.
            # Persist the replacement encrypted BEFORE subsequent network
            # validation, or a lost /v1/me response strands the old token.
            # Pending credentials grant no authority until identity validates.
            token['_identity']=previous['_identity'];token['_nonce']=previous.get('_nonce')
            token.setdefault('refresh_token',previous['refresh_token'])
            if 'scope' in previous:token.setdefault('scope',previous['scope'])
            token['_pending_verification']=True
            self.tokens.set(dict(token))
            return self._verify_replacement(token,metadata)
        except OAuthError as error:
            if error.error=='invalid_grant':raise AccountError('sign_in_required',401) from None
            raise AccountError('identity_unavailable') from None
        finally:self.close()

    def _verify_replacement(self,token,metadata):
        identity=self.api.request('GET','/v1/me',token['access_token'])
        if any(identity.get(key)!=value for key,value in token['_identity'].items()):
            raise AccountError('identity_mismatch',401)
        if token.get('id_token'):
            claims=validate_id_token(token['id_token'],self._json(self._endpoint(metadata['jwks_uri'])),
                self.config,token.get('_nonce'),token['access_token'],refreshed=True)
            if claims['sub']!=identity.get('subject') or claims.get('sid',identity.get('session_id'))!=identity.get('session_id'):
                raise AccountError('identity_mismatch',401)
        token=dict(token);token.pop('_pending_verification',None)
        self.tokens.set(token)
        return token

    def verify_pending(self,token):
        """Resume verification of a saved rotation without rotating it again."""
        from authlib.integrations.httpx_client import OAuth2Client
        if not token.get('_pending_verification') or not isinstance(token.get('_identity'),dict):
            raise AccountError('invalid_pending_credential')
        tls=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT);tls.load_default_certs()
        self.client=OAuth2Client(self.config.client_id,token_endpoint_auth_method='none',
            verify=tls,timeout=5,trust_env=False,follow_redirects=False,transport=self.http_transport)
        try:
            metadata=self._json(self.config.issuer+'/.well-known/openid-configuration')
            if metadata.get('issuer')!=self.config.issuer:raise AccountError('issuer_mismatch')
            return self._verify_replacement(token,metadata)
        finally:self.close()

    def close(self):
        if self.server: self.server.server_close();self.server=None
        if self.client: self.client.close();self.client=None
        self.verifier=self.nonce=self.state=self.response=self.callback_error=None
