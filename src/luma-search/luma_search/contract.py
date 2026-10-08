# SPDX-License-Identifier: Apache-2.0
"""Display-independent Luma Search result and ranking contract."""

from __future__ import annotations

from dataclasses import dataclass, replace
import locale
from typing import Iterable

from . import ranking


KIND_ORDER = (
    "app",
    "setting",
    "file",
    "mail",
    "contact",
    "calendar",
    "message",
    "photo",
    "music",
    "place",
)
KIND_PRIORITY = {kind: index for index, kind in enumerate(KIND_ORDER)}
SENSITIVE_SNIPPET_KINDS = frozenset({"mail", "message"})


def _fold(value: str) -> str:
    return ranking.fold_text(value)


def normalize_terms(query: str) -> tuple[str, ...]:
    """Return stable, accent-insensitive query terms."""
    return ranking.split_terms(query)


@dataclass(frozen=True, slots=True)
class SearchResult:
    provider_id: str
    result_id: str
    kind: str
    title: str
    application_id: str
    subtitle: str = ""
    snippet: str = ""
    icon_name: str = "edit-find-symbolic"
    provider_name: str = ""
    score: int = 0
    score_evidence: str = "provider"
    default_action: str = "activate"
    secondary_actions: tuple[str, ...] = ()
    privacy: str = "public"
    stale: bool = False
    unavailable: bool = False
    remote: bool = False
    requires_unlock: bool = False
    requires_network: bool = False
    activation_token: str = ""
    generation: int = 0
    provider_priority: int = 1000

    def __post_init__(self) -> None:
        if self.kind not in KIND_PRIORITY:
            raise ValueError(f"unsupported search result kind: {self.kind}")
        if not self.provider_id or not self.result_id or not self.application_id:
            raise ValueError("provider_id, result_id, and application_id are required")
        if not self.title.strip():
            raise ValueError("title is required")

    def public(self, *, allow_sensitive_snippets: bool = False) -> "SearchResult":
        if self.kind in SENSITIVE_SNIPPET_KINDS and not allow_sensitive_snippets:
            return replace(self, snippet="")
        return self


PRECOMPUTED_EVIDENCE = "luma-files"


def _match_score(result: SearchResult, terms: tuple[str, ...]) -> int:
    """Score one result with the shared Luma ranking model; -1 drops it.

    Apps are listed wholesale, so they must match by name, generic name or
    description. Other providers already matched the query (often on content
    the result title does not show), so their results always stay and the
    title only decides how high they go. Built-in file results arrive scored.
    """
    if result.score_evidence == PRECOMPUTED_EVIDENCE:
        return 0
    if result.kind == "app":
        score = ranking.score_app(result.title, terms)
        if score is not None:
            return score
        subtitle = _fold(result.subtitle)
        if subtitle and all(len(term) >= 3 and term in subtitle for term in terms):
            return 200
        return -1
    return ranking.score_provider_result(
        result.kind, result.title, terms, 0, result.provider_id
    )


def rank_results(
    results: Iterable[SearchResult],
    query: str,
    *,
    limit: int = 40,
    allow_sensitive_snippets: bool = False,
) -> tuple[SearchResult, ...]:
    """Filter, privacy-normalize, de-duplicate, and deterministically rank."""
    terms = normalize_terms(query)
    if not terms or limit <= 0:
        return ()

    best: dict[tuple[str, str], SearchResult] = {}
    for candidate in results:
        candidate = candidate.public(
            allow_sensitive_snippets=allow_sensitive_snippets
        )
        match = _match_score(candidate, terms)
        if match < 0:
            continue
        ranked = replace(candidate, score=match + candidate.score)
        key = (ranked.provider_id, ranked.result_id)
        current = best.get(key)
        if current is None or ranked.score > current.score:
            best[key] = ranked

    ordered = sorted(
        best.values(),
        key=lambda result: (
            -result.score,
            result.provider_priority,
            KIND_PRIORITY[result.kind],
            locale.strxfrm(result.title.casefold()),
            result.provider_id,
            result.result_id,
        ),
    )
    return tuple(ordered[: min(limit, 100)])
