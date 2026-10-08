"""Daemon-owned account state over real server facts and libsecret tokens.

No credentials, personal files or sync bytes are stored in this model. A sync
handler must exist before enabling; disabling never deletes local content.
"""
from copy import deepcopy
from dataclasses import dataclass
import json
import math
import ssl
import time
import threading
from urllib.parse import quote, urlsplit


class AccountError(Exception):
    def __init__(self, code, status=503, retry_after=None):
        self.code,self.status,self.retry_after=code,status,retry_after
        super().__init__(code)


def authorization_current(state, *, now=None, monotonic=None):
    """Local account lease: UTC token bound plus rollback-safe monotonic bound."""
    utc=state.get('valid_until');deadline=state.get('lease_deadline_monotonic')
    if any(type(value) not in (int,float) or not math.isfinite(value) for value in (utc,deadline)):return False
    return (state.get('account_status')=='signed_in' and state.get('service_status')=='ready'
            and state.get('stale') is False and utc>(time.time() if now is None else now)
            and deadline>(time.monotonic() if monotonic is None else monotonic))


@dataclass(frozen=True)
class AccountConfig:
    issuer: str
    api_origin: str
    client_id: str
    scope: str = 'openid profile email offline_access'

    def __post_init__(self):
        for value in (self.issuer, self.api_origin):
            url = urlsplit(value)
            if (url.scheme != 'https' or not url.hostname or url.username or url.password
                    or url.query or url.fragment or value.endswith('/')):
                raise ValueError('exact HTTPS account endpoint required')
        if urlsplit(self.api_origin).path or not self.client_id or len(self.client_id) > 128:
            raise ValueError('invalid native account configuration')


class SecretTokens:
    """Real Secret Service only. Never fall back to disk or process arguments."""
    def __init__(self, config):
        import gi
        gi.require_version('Secret', '1')
        from gi.repository import Secret
        self.secret = Secret
        self.schema = Secret.Schema.new('org.projectluma.Connect.Token', Secret.SchemaFlags.NONE,
            {'issuer': Secret.SchemaAttributeType.STRING, 'client': Secret.SchemaAttributeType.STRING})
        self.attributes = {'issuer': config.issuer, 'client': config.client_id}

    def _secret_call(self,operation,*args,timeout=5):
        # libsecret's synchronous API accepts a GCancellable. Bound each call
        # even if its D-Bus owner never replies; a timer needs no main loop.
        from gi.repository import Gio
        cancellable=Gio.Cancellable()
        timer=threading.Timer(timeout,cancellable.cancel);timer.daemon=True;timer.start()
        try:return operation(*args,cancellable)
        except Exception:
            if cancellable.is_cancelled():raise AccountError('keyring_unavailable') from None
            raise
        finally:timer.cancel()

    def get(self):
        # A locked collection hides records; absence is not evidence of logout.
        collection = self._collection()
        if collection is not None and self._locked(collection):
            raise AccountError('keyring_locked')
        raw = self._secret_call(self.secret.password_lookup_sync,self.schema,self.attributes)
        if raw is None:
            if collection is not None and self._locked(collection):raise AccountError('keyring_locked')
            return None
        if len(raw) > 65536: raise AccountError('invalid_keyring_record')
        result = json.loads(raw)
        if not isinstance(result, dict) or not isinstance(result.get('access_token'), str):
            raise AccountError('invalid_keyring_record')
        return result

    def _collection(self):
        service = self._secret_call(self.secret.Service.get_sync,self.secret.ServiceFlags.OPEN_SESSION)
        collection = self._secret_call(self.secret.Collection.for_alias_sync,service,'default',self.secret.CollectionFlags.NONE)
        self.collection_path = collection.get_object_path() if collection else None
        return collection

    def unlock(self):
        collection = self._collection()
        if collection is not None and self._locked(collection):
            service = collection.get_service()
            self._secret_call(self.secret.Service.unlock_sync,service,[collection],timeout=120)
            if self._locked(collection): raise AccountError('keyring_locked')

    def _locked(self, collection):
        # Read the owner, not a proxy cache awaiting its main-context dispatch.
        from gi.repository import Gio, GLib
        return collection.call_sync('org.freedesktop.DBus.Properties.Get',
            GLib.Variant('(ss)',('org.freedesktop.Secret.Collection','Locked')),
            Gio.DBusCallFlags.NONE,1500,None).unpack()[0]

    def set(self, token):
        raw = json.dumps(token, allow_nan=False)
        if len(raw) > 65536: raise AccountError('invalid_token')
        if not self._secret_call(self.secret.password_store_sync,self.schema,self.attributes,
                self.secret.COLLECTION_DEFAULT,'Luma Connect account',raw):
            raise AccountError('keyring_unavailable')

    def clear(self):
        self._secret_call(self.secret.password_clear_sync,self.schema,self.attributes)


class AccountAPI:
    def __init__(self, config, client=None):
        import httpx
        self.config = config
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT); tls.load_default_certs()
        tls.minimum_version = ssl.TLSVersion.TLSv1_2
        self.client = client or httpx.Client(verify=tls, trust_env=False, timeout=5, follow_redirects=False)

    def request(self, method, path, token, body=None):
        import httpx
        if not path.startswith('/v1/') or '?' in path or '#' in path:
            raise AccountError('invalid_api_route')
        if not isinstance(token, str) or not token or '\r' in token or '\n' in token:
            raise AccountError('invalid_token', 401)
        try:
            kwargs = {'headers': {'Authorization': 'Bearer ' + token, 'Accept': 'application/json', 'Accept-Encoding':'identity'}}
            if body is not None: kwargs['json'] = body
            elif method == 'POST': kwargs['content'] = b''
            deadline=time.monotonic()+10
            with self.client.stream(method, self.config.api_origin + path, **kwargs) as response:
                if response.status_code == 401: raise AccountError('signed_out', 401)
                if response.status_code == 429:
                    delay=response.headers.get('Retry-After','60')
                    delay=min(172800,max(1,int(delay))) if delay.isdecimal() else 60
                    code='rate_limited'
                    if (response.headers.get('Content-Type','').split(';')[0]=='application/json'
                            and response.headers.get('Content-Encoding','identity').lower()=='identity'):
                        raw=bytearray()
                        try:
                            for chunk in ((response.content,) if response.is_stream_consumed else response.iter_raw(chunk_size=1025)):
                                raw.extend(chunk)
                                if len(raw)>1024 or time.monotonic()>=deadline:break
                            if len(raw)<=1024:
                                from .transport import _unique
                                error=json.loads(raw,object_pairs_hook=_unique)
                                if isinstance(error,dict) and set(error)=={'error'} and error['error']=='call_attempt_limit':
                                    code='call_attempt_limit'
                        except (ValueError,httpx.HTTPError):pass
                    raise AccountError(code,429,delay)
                if not 200 <= response.status_code < 300: raise AccountError('service_unavailable', response.status_code)
                if response.headers.get('Content-Encoding','identity').lower()!='identity':raise AccountError('invalid_server_response')
                content = bytearray()
                for chunk in ((response.content,) if response.is_stream_consumed else response.iter_raw()):
                    if time.monotonic()>=deadline:raise AccountError('service_unavailable')
                    content.extend(chunk)
                    if len(content) > 1024*1024: raise AccountError('invalid_server_response')
                value = json.loads(content)
                if not isinstance(value, dict): raise AccountError('invalid_server_response')
                return value
        except (httpx.HTTPError, ValueError):
            raise AccountError('service_unavailable') from None

    def close(self): self.client.close()


class AccountModel:
    def __init__(self, api=None, tokens=None, *, handlers=None, pause_account=lambda: None, now=time.time, monotonic=time.monotonic):
        self.api, self.tokens = api, tokens
        self.handlers = dict(handlers or {})
        self.pause_account = pause_account
        self.now = now
        self.monotonic = monotonic
        self.state = {'version':1, 'generation':0, 'account_status':'signed_out',
            'service_status':'unconfigured' if api is None else 'ready', 'stale':False,
            'observed_at':None, 'profile':None, 'identity':None, 'sync':None,
            'usage':None, 'sessions':None, 'security':None, 'login_available':False}

    def snapshot(self): return deepcopy(self.state)

    def authority_token(self):
        """Read a verified credential, then recheck authority after store I/O."""
        token = self.tokens.get() if self.tokens else None
        if not token or token.get('_pending_verification'):
            raise AccountError('credential_unavailable')
        if not authorization_current(self.state, now=self.now(), monotonic=self.monotonic()):
            raise AccountError('account_unavailable')
        binding=token.get('_identity')
        if binding is not None and (not isinstance(binding,dict) or
                any((self.state.get('identity') or {}).get(k)!=v for k,v in binding.items())):
            raise AccountError('identity_mismatch',401)
        expiry=token.get('expires_at')
        if expiry is not None and (type(expiry) not in (int,float) or
                not math.isfinite(expiry) or expiry<=self.now()):
            raise AccountError('credential_unavailable')
        if not isinstance(token.get('access_token'),str) or not token['access_token']:
            raise AccountError('credential_unavailable')
        return token['access_token']

    def _changed(self): self.state['generation'] += 1

    def refresh(self):
        if self.api is None or self.tokens is None: return self.snapshot()
        stage='credential_read'
        self.state['account_failure']=None
        try:
            token = self.tokens.get()
            if token is None:
                if self.state.get('identity'):
                    self.pause_account()
                    self.state.update(account_status='reauth_required',credential_status='missing',
                                      service_status='unavailable',stale=True)
                    self._changed()
                else:self._signed_out()
                return self.snapshot()
            if self.state.get('account_status') == 'locked':
                # A successful credential read clears the old keyring lock,
                # not the need for fresh server authorization. If the network
                # is still down, lifecycle recovery must remain armed.
                self.state.update(account_status='unavailable', credential_status='ready',
                                  service_status='unavailable', stale=True)
            renewed=False
            if isinstance(token.get('expires_at'),(int,float)) and token['expires_at'] <= self.now()+30:
                # Routine rotation is not revocation. The verified local lease
                # already ends no later than this credential's expiry, and the
                # replacement grants nothing until verified; any failure below
                # still marks the account stale. Revoking early here tore down
                # every live call channel once per access-token lifetime.
                from .login import BrowserLogin
                stage='refresh_token'
                token = BrowserLogin(self.api.config,self.api,self.tokens).renew(token);renewed=True
            if token.get('_pending_verification'):
                self.pause_account();self.state.update(stale=True,service_status='unavailable')
                from .login import BrowserLogin
                stage='verify_pending'
                token=BrowserLogin(self.api.config,self.api,self.tokens).verify_pending(token)
            stage='account_identity'
            try:identity = self.api.request('GET', '/v1/me', token['access_token'])
            except AccountError as error:
                if error.status!=401 or renewed or token.get('_pending_verification') or not token.get('refresh_token'):raise
                self.pause_account();self.state.update(stale=True,service_status='unavailable')
                from .login import BrowserLogin
                stage='refresh_token'
                token=BrowserLogin(self.api.config,self.api,self.tokens).renew(token)
                identity=self.api.request('GET','/v1/me',token['access_token'])
            if (not all(isinstance(identity.get(k), str) and identity[k] for k in ('issuer','subject','account_id','session_id'))
                    or identity['issuer'] != self.api.config.issuer):
                raise AccountError('identity_mismatch',401)
            binding=token.get('_identity')
            if binding is not None and (not isinstance(binding,dict) or any(identity.get(k)!=v for k,v in binding.items())):
                raise AccountError('identity_mismatch',401)
            # A pending replacement remains inert until the same account/session
            # is verified. Keep it encrypted if any following request fails.
            # Collect atomically so partial outages never look like zero usage or
            # an empty account. Last good snapshot remains explicitly stale.
            stage='account_snapshot'
            values = {key:self.api.request('GET', '/v1/account/' + route, token['access_token'])
                for key,route in (('profile','profile'),('sync','sync'),('usage','usage'),('sessions','sessions'),('security','security'))}
            sync = values['sync']
            if not isinstance(sync.get('items'), list): raise AccountError('invalid_sync_state')
            for row in sync['items']:
                if row.get('id') not in self.handlers:
                    row['available'] = False; row['enabled'] = False
                    row['reason'] = 'native_handler_unavailable'
                if row.get('id') == 'clipboard': row['visible'] = False
            self.state.update(values)
            expiry=token.get('expires_at')
            # Preserve the existing 15-second remote authorization freshness.
            # A longer lease needs a reviewed server revocation event contract.
            valid_until=min(expiry,self.now()+15) if isinstance(expiry,(int,float)) and math.isfinite(expiry) else self.now()+15
            self.state['persistent_login']='offline_access' in str(token.get('scope','')).split()
            self.state.update(identity=identity, account_status='signed_in', service_status='ready',
                              stale=False, observed_at=int(self.now()), credential_status='ready',valid_until=valid_until,
                              lease_deadline_monotonic=self.monotonic()+min(15,max(0,valid_until-self.now())))
        except AccountError as error:
            code=error.code if error.code in {'sign_in_required','identity_mismatch','keyring_locked','signed_out','service_unavailable','rate_limited','identity_unavailable'} else 'account_unavailable'
            self.state['account_failure']={'stage':stage,'code':code}
            if error.code == 'keyring_locked':
                self.pause_account()
                self.state.update(account_status='locked', credential_status='locked',
                                  service_status='unavailable', stale=True)
            elif error.code in {'sign_in_required','identity_mismatch'}:
                self.pause_account()
                self.state.update(account_status='reauth_required',credential_status='reauth_required',
                                  service_status='unavailable',stale=True)
            elif error.status == 401:
                self.pause_account()
                self.state.update(service_status='unavailable',credential_status='rejected',stale=True)
                if not self.state.get('identity'):self.state['account_status']='unavailable'
            else:
                self.pause_account()
                self.state.update(service_status='unavailable', stale=True)
        except Exception:
            self.state['account_failure']={'stage':stage,'code':'account_unavailable'}
            self.pause_account()
            self.state.update(service_status='unavailable', stale=True)
        self._changed()
        return self.snapshot()

    def _signed_out(self):
        self.state.update(account_status='signed_out', stale=False, observed_at=None,
            credential_status='missing',
            service_status='unconfigured' if self.api is None else 'ready',
            profile=None, identity=None, sync=None, usage=None, sessions=None, security=None)
        self._changed()

    def set_sync(self, category, enabled):
        if type(enabled) is not bool: raise AccountError('invalid_setting', 400)
        handler = self.handlers.get(category)
        if handler is None: raise AccountError('native_handler_unavailable')
        if self.state['account_status'] != 'signed_in' or self.state['stale']:
            raise AccountError('account_unavailable')
        # The reviewed handler must pause/cancel work without deleting local data.
        # There is deliberately no filesystem/delete callback in this contract.
        handler.set_enabled(enabled)
        self._changed()
        return self.refresh()

    def management_url(self):
        if self.state['account_status']!='signed_in' or self.state['stale'] or not self.api:
            raise AccountError('account_unavailable')
        password=(self.state.get('security') or {}).get('password_change') or {}
        expected=self.api.config.issuer+'/account/'
        if password.get('mode')!='identity_provider_browser' or password.get('url')!=expected:
            raise AccountError('account_management_unavailable')
        return expected

    def sign_out(self, target):
        if self.state['account_status'] != 'signed_in' or self.state['stale']:
            raise AccountError('account_unavailable')
        bearer = self.authority_token()
        if target in {'current','others'}:
            method, route = 'POST', '/v1/account/signout/' + target
        else:
            if target == self.state['identity']['session_id']: raise AccountError('use_current_signout',409)
            items = (self.state.get('sessions') or {}).get('items', [])
            if not any(row.get('session_id') == target and row.get('can_revoke_here') for row in items):
                raise AccountError('session_unavailable',404)
            method, route = 'DELETE', '/v1/account/sessions/' + quote(target, safe='')
        result = self.api.request(method, route, bearer)
        if (result.get('local_data_action') != 'none' or not isinstance(result.get('revoked_session_ids'),list)):
            raise AccountError('invalid_revocation_receipt')
        if target == 'current':
            self.pause_account(); self.tokens.clear(); self._signed_out()
        else: self.refresh()
        return result
