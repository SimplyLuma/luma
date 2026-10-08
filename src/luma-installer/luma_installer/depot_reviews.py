# SPDX-License-Identifier: Apache-2.0
"""Ratings and reviews from the Hub (ADR-028, section 10).

Reading needs no account. Writing uses this device's Luma Connect enrolment:
the bearer token Connect keeps in ``$XDG_DATA_HOME/luma/connect/device.json``,
read with the same private-file checks Connect applies to it. The token is sent
only to the Hub that issued it, and only over HTTPS; a device enrolled with a
different Hub (a development one, say) can read reviews here but not post them.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit, urlencode
from urllib.request import Request, urlopen

HUB_URL = os.environ.get('LUMA_DEPOT_HUB_URL', 'https://hub.simplyluma.com').rstrip('/')
TIMEOUT = 15
USER_AGENT = 'Luma-Depot/4'
MAX_RESPONSE = 1024 * 1024
TITLE_MAX = 100
BODY_MAX = 4000
MINIMUM_RATINGS = 3
_SLUG = re.compile(r'[a-z][a-z0-9-]{0,63}\Z')
_REVIEW_ID = re.compile(r'[A-Za-z0-9_-]{1,64}\Z')
_CURSOR = re.compile(r'[A-Za-z0-9_.:=-]{1,256}\Z')
_TOKEN = re.compile(r'[A-Za-z0-9_-]{20,512}\Z')


class ReviewsError(RuntimeError):
    """Something a person can be told in one sentence."""

    def __init__(self, message: str, *, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


class NotEnrolled(ReviewsError):
    pass


@dataclass(frozen=True)
class Reply:
    body: str
    author: str
    created_at: str


@dataclass(frozen=True)
class Review:
    id: str
    rating: int
    title: str
    body: str
    author: str
    version: str
    installed_on_luma: bool
    created_at: str
    updated_at: str
    own: bool = False
    reply: Reply | None = None


@dataclass(frozen=True)
class ReviewPage:
    average: float
    count: int
    reviews: tuple[Review, ...]
    next_cursor: str = ''
    own_review: Review | None = None

    @property
    def enough_ratings(self) -> bool:
        return self.count >= MINIMUM_RATINGS


def _origin(url: str) -> tuple[str, str, int]:
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == 'https' else 80)
    return parts.scheme, (parts.hostname or '').lower(), port


def device_token(hub: str = HUB_URL, environment=None) -> str:
    """This device's Connect token for ``hub``, or '' when it may not be used."""
    env = os.environ if environment is None else environment
    base = env.get('XDG_DATA_HOME') or os.path.join(env.get('HOME', str(Path.home())), '.local/share')
    path = Path(base) / 'luma/connect/device.json'
    try:
        if (path.parent.stat().st_mode & 0o077) or (path.stat().st_mode & 0o077):
            # Connect refuses a token anyone else could read; so does Depot.
            return ''
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return ''
    if not isinstance(data, dict):
        return ''
    token, issuer = str(data.get('token', '')), str(data.get('hub', '')).rstrip('/')
    if not _TOKEN.fullmatch(token) or not issuer:
        return ''
    if _origin(hub)[0] != 'https' or _origin(issuer) != _origin(hub):
        return ''
    return token


def is_enrolled(hub: str = HUB_URL, environment=None) -> bool:
    return bool(device_token(hub, environment))


def _text(value, maximum) -> str:
    return value[:maximum] if isinstance(value, str) else ''


def _review(value) -> Review | None:
    if not isinstance(value, dict):
        return None
    rating = value.get('rating')
    identifier = value.get('id')
    if type(rating) is not int or not 1 <= rating <= 5 or not isinstance(identifier, (str, int)):
        return None
    author = value.get('author')
    reply = value.get('reply')
    parsed_reply = None
    if isinstance(reply, dict) and isinstance(reply.get('body'), str):
        replier = reply.get('developer') or reply.get('author') or {}
        parsed_reply = Reply(_text(reply['body'], BODY_MAX * 2),
                             _text(replier.get('name') if isinstance(replier, dict) else replier, 128),
                             _text(reply.get('created_at'), 40))
    return Review(
        id=str(identifier)[:64], rating=rating,
        title=_text(value.get('title'), TITLE_MAX * 2), body=_text(value.get('body'), BODY_MAX * 2),
        author=_text(author.get('name') if isinstance(author, dict) else author, 128) or 'A Luma user',
        version=_text(value.get('version'), 64),
        installed_on_luma=value.get('installed_on_luma') is True,
        created_at=_text(value.get('created_at'), 40), updated_at=_text(value.get('updated_at'), 40),
        own=value.get('own') is True, reply=parsed_reply)


def parse_page(value) -> ReviewPage:
    if not isinstance(value, dict):
        raise ReviewsError('Reviews could not be read.')
    summary = value.get('rating') if isinstance(value.get('rating'), dict) else {}
    average = summary.get('average', 0)
    count = summary.get('count', 0)
    if type(average) not in (int, float) or not 0 <= average <= 5:
        average = 0.0
    if type(count) is not int or count < 0:
        count = 0
    rows = value.get('reviews') if isinstance(value.get('reviews'), list) else []
    reviews = tuple(review for review in (_review(row) for row in rows[:100]) if review)
    own = _review(value.get('own_review')) or next((review for review in reviews if review.own), None)
    cursor = value.get('next_cursor')
    return ReviewPage(float(average), count, reviews,
                      cursor if isinstance(cursor, str) and _CURSOR.fullmatch(cursor) else '',
                      own)


class ReviewsClient:
    def __init__(self, hub: str = HUB_URL, *, environment=None, opener=urlopen, timeout=TIMEOUT) -> None:
        self.hub = hub.rstrip('/')
        self.environment = environment
        self.opener = opener
        self.timeout = timeout

    def _request(self, method: str, path: str, payload=None, *, authenticated=False, optional_token=False):
        headers = {'Accept': 'application/json', 'User-Agent': USER_AGENT}
        token = device_token(self.hub, self.environment) if (authenticated or optional_token) else ''
        if authenticated and not token:
            raise NotEnrolled('Sign in to Luma on this computer to write a review.')
        if token:
            headers['Authorization'] = f'Bearer {token}'
        data = None
        if payload is not None:
            data = json.dumps(payload).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        request = Request(self.hub + path, data=data, method=method, headers=headers)
        try:
            with self.opener(request, timeout=self.timeout) as response:
                content = response.read(MAX_RESPONSE + 1)
        except HTTPError as error:
            raise ReviewsError(self._explain(error.code, error), status=error.code) from None
        except (URLError, OSError, ValueError):
            raise ReviewsError('Reviews could not be reached. Check the network and try again.') from None
        if len(content) > MAX_RESPONSE:
            raise ReviewsError('Reviews could not be read.')
        if not content.strip():
            return {}
        try:
            return json.loads(content.decode('utf-8'))
        except (UnicodeError, ValueError):
            raise ReviewsError('Reviews could not be read.') from None

    @staticmethod
    def _explain(status: int, error) -> str:
        message = ''
        try:
            decoded = json.loads(error.read(4096).decode('utf-8'))
            message = decoded.get('error', '') if isinstance(decoded, dict) else ''
        except Exception:
            message = ''
        if status == 401:
            return 'Luma no longer recognises this computer. Sign in to Luma again to review.'
        if status == 403:
            return message or 'This account cannot review yet. Verify your email address in Hub.'
        if status == 404:
            return 'Reviews are not available for this app.'
        if status == 429:
            return 'You have written a lot of reviews recently. Try again later.'
        if isinstance(message, str) and message and len(message) < 200:
            return message
        return 'Reviews are not available right now. Try again later.'

    def reviews(self, slug: str, cursor: str = '') -> ReviewPage:
        if not _SLUG.fullmatch(slug or ''):
            raise ReviewsError('This app has no reviews page.')
        query = '?' + urlencode({'cursor': cursor}) if cursor and _CURSOR.fullmatch(cursor) else ''
        return parse_page(self._request('GET', f'/api/depot/apps/{quote(slug)}/reviews{query}',
                                        optional_token=True))

    def save(self, slug: str, *, rating: int, title: str, body: str, version: str,
             installed_on_luma: bool) -> Review | None:
        if not _SLUG.fullmatch(slug or ''):
            raise ReviewsError('This app has no reviews page.')
        if type(rating) is not int or not 1 <= rating <= 5:
            raise ReviewsError('Choose from one to five stars.')
        title, body = title.strip(), body.strip()
        if len(title) > TITLE_MAX or len(body) > BODY_MAX:
            raise ReviewsError('That review is longer than Luma accepts.')
        answer = self._request('PUT', f'/api/depot/apps/{quote(slug)}/review', {
            'rating': rating, 'title': title, 'body': body,
            'version': version[:64], 'installed_on_luma': bool(installed_on_luma),
        }, authenticated=True)
        return _review(answer.get('review') if isinstance(answer, dict) else None)

    def delete(self, slug: str) -> None:
        if not _SLUG.fullmatch(slug or ''):
            raise ReviewsError('This app has no reviews page.')
        self._request('DELETE', f'/api/depot/apps/{quote(slug)}/review', authenticated=True)

    def report(self, review_id: str, reason: str = '') -> None:
        if not _REVIEW_ID.fullmatch(review_id or ''):
            raise ReviewsError('That review cannot be reported.')
        self._request('POST', f'/api/depot/reviews/{quote(review_id)}/report',
                      {'reason': reason.strip()[:500]}, optional_token=True)
