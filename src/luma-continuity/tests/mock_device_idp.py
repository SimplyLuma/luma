"""A mock RFC 8628 identity provider for the Connect device sign-in tests.

An httpx transport, so the daemon's Authlib clients reach it unchanged. Shaped
like Keycloak's realm endpoints: discovery, device authorization, token
(device_code and refresh_token grants, with refresh-token rotation) and JWKS.
User codes come from RFC 8628's twenty consonants. Synthetic keys and tokens
only; nothing leaves the process.
"""
import secrets
import time
from urllib.parse import parse_qs

CLEAR = 'BCDFGHJKLMNPQRSTVWXZ'
DEVICE_GRANT = 'urn:ietf:params:oauth:grant-type:device_code'


class MockDeviceIdP:
    def __init__(self, issuer, device_client, *, subject='fixture-subject', session='fixture-session'):
        import httpx
        from joserfc.jwk import RSAKey
        self.issuer, self.device_client = issuer, device_client
        self.subject, self.session = subject, session
        self.key = RSAKey.generate_key(2048, parameters={'kid': 'mock'})
        self.grants = {}      # device_code -> {'user_code', 'approved', 'client'}
        self.refresh = {}     # live refresh token -> {'client', 'scope'}
        self.access = {}      # access token -> {'sub', 'sid', 'client'}
        self.requests = []    # (path, form)
        self.down = False
        self.audience = None  # misissue ID tokens to this audience
        self.transport = httpx.MockTransport(self.handle)

    # -- the person, on their phone -------------------------------------------------
    def approve(self, user_code):
        for grant in self.grants.values():
            if grant['user_code'] == user_code:
                grant['approved'] = True
                return
        raise KeyError(user_code)

    def revoke_all(self):
        self.refresh.clear()

    # -- endpoints -------------------------------------------------------------------
    def handle(self, request):
        import httpx
        if self.down: raise httpx.ConnectError('identity provider unreachable', request=request)
        path = request.url.path
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()} if request.method == 'POST' else {}
        self.requests.append((path, form))
        base = self.issuer + '/protocol/openid-connect'
        if str(request.url) == self.issuer + '/.well-known/openid-configuration':
            return httpx.Response(200, json={
                'issuer': self.issuer, 'authorization_endpoint': base + '/auth', 'token_endpoint': base + '/token',
                'jwks_uri': base + '/certs', 'device_authorization_endpoint': base + '/auth/device',
                'code_challenge_methods_supported': ['S256'],
                'grant_types_supported': ['authorization_code', 'refresh_token', DEVICE_GRANT]})
        if str(request.url) == base + '/certs':
            return httpx.Response(200, json={'keys': [self.key.as_dict(private=False)]})
        if str(request.url) == base + '/auth/device':
            if form.get('client_id') != self.device_client:
                return httpx.Response(401, json={'error': 'unauthorized_client'})
            code = ''.join(secrets.choice(CLEAR) for _ in range(8))
            device = secrets.token_urlsafe(24)
            self.grants[device] = {'user_code': f'{code[:4]}-{code[4:]}', 'approved': False,
                                   'client': form['client_id'], 'scope': form.get('scope', 'openid')}
            return httpx.Response(200, json={
                'device_code': device, 'user_code': self.grants[device]['user_code'],
                'verification_uri': 'https://id.example.test/device',
                'verification_uri_complete': 'https://id.example.test/device?user_code=' + code,
                'expires_in': 600, 'interval': 5})
        if str(request.url) == base + '/token':
            return self._token(form)
        return httpx.Response(404, json={'error': 'not_found'})

    def _token(self, form):
        import httpx
        kind = form.get('grant_type')
        if kind == DEVICE_GRANT:
            grant = self.grants.get(form.get('device_code', ''))
            if grant is None or grant['client'] != form.get('client_id'):
                return httpx.Response(400, json={'error': 'expired_token'})
            if not grant['approved']:
                return httpx.Response(400, json={'error': 'authorization_pending'})
            del self.grants[form['device_code']]
            return httpx.Response(200, json=self._issue(grant['client'], grant['scope']))
        if kind == 'refresh_token':
            held = self.refresh.pop(form.get('refresh_token', ''), None)
            if held is None or held['client'] != form.get('client_id'):
                if held is not None: self.refresh[form['refresh_token']] = held
                return httpx.Response(400, json={'error': 'invalid_grant',
                                                 'error_description': 'Invalid refresh token'})
            return httpx.Response(200, json=self._issue(held['client'], held['scope']))
        return httpx.Response(400, json={'error': 'unsupported_grant_type'})

    def _issue(self, client, scope):
        from joserfc import jwt
        now = int(time.time())
        access, refresh = 'mock-access-' + secrets.token_urlsafe(16), 'mock-refresh-' + secrets.token_urlsafe(16)
        self.access[access] = {'sub': self.subject, 'sid': self.session, 'client': client}
        self.refresh[refresh] = {'client': client, 'scope': scope}
        audience = self.audience or client
        claims = {'iss': self.issuer, 'sub': self.subject, 'aud': audience, 'azp': audience, 'sid': self.session,
                  'iat': now, 'exp': now + 300, 'preferred_username': 'lumatest'}
        return {'access_token': access, 'refresh_token': refresh, 'token_type': 'Bearer', 'expires_in': 300,
                'refresh_expires_in': 0, 'scope': scope,
                'id_token': jwt.encode({'alg': 'RS256', 'kid': 'mock'}, claims, self.key)}


def setup_device_sign_in(idp, client_id, scope='openid profile email offline_access'):
    """The requests luma-setup's ConnectFlow makes (RFC 8628 3.1-3.5), the
    person approving the code on another device between two polls. Returns the
    token response as the setup hands it to the root helper for sealing."""
    import httpx
    with httpx.Client(transport=idp.transport) as client:
        meta = client.get(idp.issuer + '/.well-known/openid-configuration').json()
        grant = client.post(meta['device_authorization_endpoint'], data={'client_id': client_id, 'scope': scope}).json()
        poll = {'grant_type': DEVICE_GRANT, 'device_code': grant['device_code'], 'client_id': client_id}
        pending = client.post(meta['token_endpoint'], data=poll)
        assert pending.json() == {'error': 'authorization_pending'}, pending.text
        idp.approve(grant['user_code'])
        answer = client.post(meta['token_endpoint'], data=poll)
        assert answer.status_code == 200, answer.text
        return grant['user_code'], answer.text
