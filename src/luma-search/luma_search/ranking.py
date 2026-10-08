# SPDX-License-Identifier: Apache-2.0
"""Luma Search ranking model shared by every result kind.

This is the Python twin of GNOME Shell's js/ui/lumaSearchRanking.js (Beam).
tests/search/ranking-vectors.json is run against both so they stay in step.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
import unicodedata
from typing import Iterable, Mapping

TIER_EXACT = 1000
TIER_PREFIX = 800
TIER_WORD = 600
TIER_SUBSTRING = 350
TIER_FUZZY = 150

COMPLETION_BONUS = 100
APP_NAME_BONUS = 150
APP_EXACT_BONUS = 300
SETTING_BONUS = 60
XDG_BOOST = 200
USAGE_BOOST_CAP = 180
JUNK_PENALTY_CAP = 400
JUNK_PATH_PENALTY = 450
DEPTH_STEP = 30
DEPTH_CAP = 180
OUTSIDE_HOME_PENALTY = 120
DAY = 86400

XDG_FOLDER_KEYS = ("DESKTOP", "DOCUMENTS", "DOWNLOAD", "MUSIC", "PICTURES", "VIDEOS")

JUNK_DIRECTORIES = frozenset({
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".cache", "cache",
    "build", "builddir", "_build", "dist", "target", "obj", ".venv", "venv",
    "site-packages", ".tox", ".mypy_cache", ".pytest_cache", ".gradle",
    ".flatpak-builder", "buildroot", "rpmbuild", "cmakefiles", "tmp", "temp",
    ".tmp", "vendor", ".npm", ".cargo", ".rustup", "deps",
})

JUNK_WORDS = {
    "backup": 220, "backups": 220, "bak": 220, "bck": 220, "bkp": 220,
    "copy": 220, "old": 200, "orig": 180, "tmp": 220, "temp": 200,
    "autosave": 220, "conflicted": 200, "stale": 160, "archived": 120,
}
JUNK_EXTENSIONS = frozenset({"bak", "old", "orig", "tmp", "swp", "swo", "part", "crdownload", "rej"})

_NON_WORD = re.compile(r"[^\w]+|_+")
_DATE = re.compile(
    r"(?:^|[^\d])(?:19|20)\d{2}[-_.]?(?:0[1-9]|1[0-2])[-_.]?(?:[0-2]\d|3[01])"
    r"(?:[t_-]?\d{4,6})?(?:[^\d]|$)"
)
_LONG_NUMBER = re.compile(r"\d{5,}")
_VERSION = re.compile(r"(?:^|[^\d.])v?\d+\.\d+(?:\.\d+){0,2}(?:[^\d]|$)")
_HEX = re.compile(r"^[0-9a-f]+$")


def js_round(value: float) -> int:
    """Math.round semantics (halves round up), so both twins agree."""
    return math.floor(value + 0.5)


def fold_text(value: object) -> str:
    text = unicodedata.normalize("NFKD", "" if value is None else str(value))
    text = "".join(char for char in text if not ("̀" <= char <= "ͯ"))
    return text.lower()


def split_terms(query: str) -> tuple[str, ...]:
    return tuple(term for term in fold_text(query).strip().split() if term)


def _compact(text: str) -> str:
    return "".join(char for char in text if char.isalnum())


def _words(text: str) -> list[str]:
    return [part for part in _NON_WORD.split(text) if part]


def name_words(name: object) -> list[str]:
    spaced = "" if name is None else str(name)
    spaced = re.sub(r"([a-zà-ɏ])([A-Z])", r"\1 \2", spaced)
    spaced = re.sub(r"([A-Za-z])(\d)", r"\1 \2", spaced)
    spaced = re.sub(r"(\d)([A-Za-z])", r"\1 \2", spaced)
    return _words(fold_text(spaced))


def split_extension(name: object) -> tuple[str, str]:
    text = "" if name is None else str(name)
    dot = text.rfind(".")
    if dot <= 0 or dot == len(text) - 1 or len(text) - dot > 12:
        return text, ""
    return text[:dot], text[dot + 1:]


def edit_distance(a: str, b: str, limit: int) -> int:
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    before: list[int] | None = None
    previous = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        current = [i]
        best = i
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            value = min(current[j - 1] + 1, previous[j] + 1, previous[j - 1] + cost)
            if before is not None and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                value = min(value, before[j - 2] + 1)
            current.append(value)
            best = min(best, value)
        if best > limit:
            return limit + 1
        before = previous
        previous = current
    return previous[len(b)]


def _typo_limit(term: str) -> int:
    if len(term) >= 8:
        return 2
    if len(term) >= 5:
        return 1
    return 0


def _term_tier(term: str, folded: str, words: list[str]) -> int:
    if folded == term:
        return TIER_EXACT
    if folded.startswith(term):
        return TIER_PREFIX
    if any(word.startswith(term) for word in words):
        return TIER_WORD
    if len(term) >= 3 and term in folded:
        return TIER_SUBSTRING
    limit = _typo_limit(term)
    if limit > 0:
        for candidate in (folded, *words):
            head = candidate[: len(term)]
            if edit_distance(term, candidate, limit) <= limit or (
                len(candidate) > len(term) and edit_distance(term, head, limit) <= limit
            ):
                return TIER_FUZZY
    return -1


@dataclass(frozen=True, slots=True)
class Match:
    tier: int
    score: int


def match_name(name: object, terms: tuple[str, ...] | list[str], *, ignore_extension: bool = False) -> Match | None:
    if not terms or not name:
        return None
    stem = split_extension(name)[0] if ignore_extension else str(name)
    folded_name = fold_text(name)
    folded_stem = fold_text(stem)
    words = name_words(name)
    phrase = " ".join(terms)

    # "wifi" is "Wi-Fi" and "nightlight" is "Night Light".
    compact_name = _compact(folded_name)
    compact_phrase = _compact(phrase)

    if folded_stem == phrase or folded_name == phrase or (
        len(compact_phrase) >= 3 and compact_name == compact_phrase
    ):
        tier = TIER_EXACT
    elif (len(terms) > 1 and folded_name.startswith(phrase)) or (
        len(compact_phrase) >= 3 and compact_name.startswith(compact_phrase)
        and not folded_name.startswith(phrase)
    ):
        tier = TIER_PREFIX
    else:
        tier = TIER_EXACT
        for term in terms:
            t = _term_tier(term, term if folded_stem == term else folded_name, words)
            if t < 0:
                return None
            tier = min(tier, t)
        if len(terms) > 1 and tier == TIER_EXACT:
            tier = TIER_PREFIX

    score = tier
    if tier >= TIER_WORD:
        matched = min(len(re.sub(r"\s+", "", phrase)), len(folded_stem) or 1)
        score += js_round(COMPLETION_BONUS * matched / max(len(folded_stem), 1))
    return Match(tier, score)


def _terms_touch(word: str, terms: Iterable[str]) -> bool:
    return any(
        word.startswith(term) or term.startswith(word) or (len(term) >= 3 and term in word)
        for term in terms
    )


def junk_penalty(name: object, terms: tuple[str, ...] | list[str] = ()) -> tuple[int, list[str]]:
    text = "" if name is None else str(name)
    stem, extension = split_extension(text)
    reasons: list[str] = []
    penalty = 0

    def add(reason: str, value: int) -> None:
        nonlocal penalty
        reasons.append(reason)
        penalty += value

    if text.endswith("~") or re.fullmatch(r"#.*#", text):
        add("editor-backup", 260)
    if extension and fold_text(extension) in JUNK_EXTENSIONS and not _terms_touch(fold_text(extension), terms):
        add("backup-extension", 260)
    if re.search(r"\(\d{1,3}\)\s*$", stem) or re.search(r"[\s_-]\(?copy(\s*\d+)?\)?$", stem, re.IGNORECASE):
        add("duplicate", 200)

    for word in name_words(stem):
        weight = JUNK_WORDS.get(word)
        if weight and not _terms_touch(word, terms):
            add(f"word:{word}", weight)
            break

    folded = fold_text(stem)
    tokens = _words(folded)
    typed_digits = any(re.search(r"\d", term) for term in terms)
    if not typed_digits:
        if _DATE.search(folded):
            add("date", 120)
        elif _LONG_NUMBER.search(folded):
            add("long-number", 160)
        if _VERSION.search(folded):
            add("version", 80)
    if any(
        7 <= len(token) <= 40 and _HEX.match(token) and re.search(r"\d", token)
        and re.search(r"[a-f]", token) and not _terms_touch(token, terms)
        for token in tokens
    ):
        add("hash", 160)
    return min(penalty, JUNK_PENALTY_CAP), reasons


def _parts(path: object) -> list[str]:
    return [part for part in str(path or "").split("/") if part]


def location_adjustment(path: object, home: object) -> tuple[int, list[str], bool]:
    parts = _parts(path)
    home_parts = _parts(home)
    in_home = bool(home_parts) and parts[: len(home_parts)] == home_parts and len(parts) > len(home_parts)
    relative = parts[len(home_parts):] if in_home else parts
    adjustment = 0
    reasons: list[str] = []
    if in_home:
        depth = len(relative)
        depth_penalty = min(DEPTH_CAP, DEPTH_STEP * max(0, depth - 1))
        if depth_penalty:
            adjustment -= depth_penalty
            reasons.append(f"depth:{depth}")
    else:
        adjustment -= OUTSIDE_HOME_PENALTY
        reasons.append("outside-home")
    if any(part.startswith(".") or fold_text(part) in JUNK_DIRECTORIES for part in relative):
        adjustment -= JUNK_PATH_PENALTY
        reasons.append("generated-path")
    return adjustment, reasons, any(part.startswith(".") for part in relative)


def _decay(age: float, half_life_days: float) -> float:
    if age < 0:
        return 1.0
    return 0.5 ** (age / (half_life_days * DAY))


def usage_boost(candidate: Mapping[str, object], now: float) -> int:
    visited = float(candidate.get("visited") or 0)
    visits = float(candidate.get("visits") or 0)
    picked = float(candidate.get("picked") or 0)
    picks = float(candidate.get("picks") or 0)
    modified = float(candidate.get("modified") or 0)
    boost = 0.0
    if visited:
        boost += 120 * _decay(now - visited, 10)
    if visits:
        boost += min(60, 15 * math.log2(1 + visits))
    if picks:
        boost += min(120, 45 * picks) * max(0.25, _decay(now - picked, 30))
    if modified:
        boost += 40 * _decay(now - modified, 5)
    return min(USAGE_BOOST_CAP, js_round(boost))


@dataclass(frozen=True, slots=True)
class FileScore:
    score: int
    tier: int
    evidence: tuple[str, ...]


def score_file(candidate: Mapping[str, object], terms, *, home: str, now: float,
               xdg_folders: frozenset[str] | set[str] = frozenset()) -> FileScore | None:
    match = match_name(candidate.get("name"), terms, ignore_extension=True)
    if match is None:
        return None
    adjustment, location_reasons, _hidden = location_adjustment(candidate.get("path"), home)
    penalty, junk_reasons = junk_penalty(candidate.get("name"), terms)
    xdg = XDG_BOOST if candidate.get("path") in xdg_folders else 0
    usage = usage_boost(candidate, now)
    evidence = [f"tier:{match.tier}", *location_reasons, *junk_reasons]
    if xdg:
        evidence.append("xdg")
    if usage:
        evidence.append(f"usage:{usage}")
    return FileScore(match.score + adjustment - penalty + xdg + usage, match.tier, tuple(evidence))


def rank_files(candidates: Iterable[Mapping[str, object]], terms, *, home: str, now: float,
               xdg_folders: frozenset[str] | set[str] = frozenset(), limit: int = 20) -> list[dict]:
    by_path: dict[str, dict] = {}
    for candidate in candidates:
        path = candidate.get("path")
        if not path or not candidate.get("name"):
            continue
        existing = by_path.get(path)
        if existing is None:
            by_path[path] = dict(candidate)
        else:
            existing.update({k: v for k, v in candidate.items() if v not in (None, 0, "")})
    ranked = []
    for candidate in by_path.values():
        if location_adjustment(candidate["path"], home)[2]:
            continue
        scored = score_file(candidate, terms, home=home, now=now, xdg_folders=xdg_folders)
        if scored is not None:
            ranked.append({**candidate, "score": scored.score, "tier": scored.tier,
                           "evidence": scored.evidence})
    ranked.sort(key=lambda item: (-item["score"], len(item["path"]), item["path"]))
    return ranked[:limit]


def score_app(name: str, terms, *, generic_name: str = "", keywords: Iterable[str] = (),
              matched_elsewhere: bool = False) -> int | None:
    candidates = []
    by_name = match_name(name, terms)
    if by_name:
        bonus = APP_EXACT_BONUS if by_name.tier == TIER_EXACT else (
            APP_NAME_BONUS if by_name.tier >= TIER_WORD else 0)
        candidates.append(by_name.score + bonus)
    generic = match_name(generic_name, terms)
    if generic:
        candidates.append(js_round(generic.score * 0.75))
    for keyword in keywords:
        k = match_name(keyword, terms)
        if k:
            candidates.append(js_round(k.score * 0.7))
    if not candidates:
        candidates.append(250 if matched_elsewhere else -1)
    best = max(candidates)
    return None if best < 0 else best


def score_setting_page(page: Mapping[str, object], terms) -> int | None:
    scores = []
    title = match_name(page.get("title"), terms)
    if title:
        scores.append(title.score + SETTING_BONUS)
    for keyword in page.get("keywords") or ():
        k = match_name(keyword, terms)
        if k and k.tier >= TIER_WORD:
            scores.append(js_round(k.score * 0.8))
    path = match_name(page.get("path"), terms)
    if path and path.tier >= TIER_WORD:
        scores.append(js_round(path.score * 0.5))
    return max(scores) if scores else None


def rank_setting_pages(pages, terms, limit: int = 5) -> list[dict]:
    ranked = []
    for index, page in enumerate(pages):
        score = score_setting_page(page, terms)
        if score is not None:
            ranked.append({"page": page, "index": index, "score": score})
    ranked.sort(key=lambda item: (-item["score"], item["index"]))
    return ranked[:limit]


CONTENT_KINDS = frozenset({"mail", "message", "contact", "calendar"})


def score_provider_result(kind: str, name: str, terms, index: int = 0, provider_id: str = "") -> int:
    identity = provider_id.lower()
    if "calculator" in identity:
        return 1400 - index
    match = match_name(name, terms)
    order = min(index, 20) * 8
    if kind == "setting":
        return (match.score + SETTING_BONUS if match else 520) - order
    if "software" in identity:
        return (match.score - 250 if match else 150) - order
    # Mail, messages, people and events match on what they contain; they
    # follow apps, settings and files that match by name.
    if kind in CONTENT_KINDS:
        return (min(match.score, TIER_WORD) - 350 if match else 150) - order
    return (match.score if match else 300) - order


def fts_query(terms, *, shorten: bool = False) -> str:
    tokens = []
    for term in terms:
        for token in _words(term):
            cut = token[: max(3, len(token) // 2)] if shorten and len(token) >= 5 else token
            tokens.append(f"{cut}*")
    return " ".join(tokens)
