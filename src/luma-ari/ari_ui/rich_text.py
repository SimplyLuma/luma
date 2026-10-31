# SPDX-License-Identifier: Apache-2.0
"""Small, escaped Markdown subset for replies. No remote HTML is interpreted."""
import html
import re
from urllib.parse import urlsplit

_TOKEN = re.compile(r'`([^`\n]+)`|\[([^\]\n]{1,500})\]\(([^\s()]{1,4096})\)|\*\*(.+?)\*\*|__(.+?)__|\*([^*\n]+)\*|_([^_\n]+)_')


def safe_url(url):
    if not isinstance(url, str) or len(url) > 4096 or any(ord(c) < 33 for c in url):
        return False
    try:
        parsed = urlsplit(url)
        return parsed.scheme in ('http', 'https') and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        return False


def inline(value, *, plain=False, depth=0):
    result, end = [], 0
    for match in _TOKEN.finditer(value):
        result.append(html.escape(value[end:match.start()]) if not plain else value[end:match.start()])
        code, label, url, bold, bold_under, italic, italic_under = match.groups()
        content = code or label or bold or bold_under or italic or italic_under
        rendered = (inline(content, plain=plain, depth=depth + 1) if code is None and depth < 2
                    else content if plain else html.escape(content))
        if plain:
            result.append(rendered + (' (' + url + ')' if label and safe_url(url) else ''))
        elif code is not None:
            result.append('<tt>' + rendered + '</tt>')
        elif label is not None:
            result.append('<a href="' + html.escape(url, quote=True) + '">' + rendered + '</a>'
                          if safe_url(url) else html.escape(match.group()))
        else:
            tag = 'b' if bold is not None or bold_under is not None else 'i'
            result.append('<' + tag + '>' + rendered + '</' + tag + '>')
        end = match.end()
    result.append(value[end:] if plain else html.escape(value[end:]))
    return ''.join(result)


def blocks(value, *, plain=False):
    """Yield (markup, code block). Preserve newlines and visible list markers."""
    current, code, fenced = [], [], False
    def paragraph():
        text = '\n'.join(current)
        current.clear()
        return inline(text, plain=plain)
    for line in str(value).splitlines():
        if line.lstrip().startswith('```'):
            if fenced:
                text = '\n'.join(code)
                yield (text if plain else '<tt>' + html.escape(text) + '</tt>', True)
                code, fenced = [], False
            else:
                if current:
                    yield paragraph(), False
                fenced = True
        elif fenced:
            code.append(line)
        elif not line.strip():
            if current:
                yield paragraph(), False
        else:
            line = re.sub(r'^\s*[-*+]\s+', '• ', line)
            line = re.sub(r'^#{1,6}\s+', '', line)
            current.append(line)
    if code:
        text = '\n'.join(code)
        yield (text if plain else '<tt>' + html.escape(text) + '</tt>', True)
    if current:
        yield paragraph(), False


def plain_text(value):
    return '\n\n'.join(text for text, _code in blocks(value, plain=True))
