"""Approved-wiki context for BA discussion inject (ack-then-delta).

Uses WikiStore search/list (approved only). Does not approve or write wiki.
Never auto-overwrite BA artifacts from wiki hits.
"""

from __future__ import annotations

import json
from typing import Any

from factory_wiki.store import WikiStore, get_wiki_store
from managers.control.session import resolve_project_id


def _feature_keywords(state: dict[str, Any]) -> list[str]:
    artifacts = state.get("artifacts") or {}
    fd = artifacts.get("feature_definition") or {}
    texts: list[str] = []
    for key in ("goal",):
        if fd.get(key):
            texts.append(str(fd[key]))
    for item in (fd.get("scope_in") or [])[:6]:
        texts.append(str(item))
    title = state.get("feature_title")
    if title:
        texts.append(str(title))
    description = state.get("feature_description")
    if description:
        texts.append(str(description)[:120])
    out: list[str] = []
    seen: set[str] = set()
    for text in texts:
        cleaned = " ".join(str(text).split())
        if len(cleaned) < 4:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned[:80])
    return out[:8]


def collect_approved_wiki_hits(
    state: dict[str, Any],
    *,
    store: WikiStore | None = None,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """Return approved wiki hits for the feature project (drafts never appear)."""
    project_id = resolve_project_id(state)
    own_store = store is None
    wiki = store or get_wiki_store()
    try:
        seen: set[str] = set()
        hits: list[dict[str, Any]] = []
        # Put feature-relevant search hits first. Listing recent pages first
        # used to fill the limit before relevant pages could be considered.
        for query in _feature_keywords(state):
            for hit in wiki.search(
                query,
                project_id=project_id,
                agent_name="ba-agent",
                limit=8,
            ):
                pid = str(hit.get("page_id") or "")
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                hits.append(hit)
                if len(hits) >= limit:
                    break
            if len(hits) >= limit:
                break
        if len(hits) < limit:
            for hit in wiki.list_approved(
                project_id=project_id, agent_name="ba-agent", limit=limit
            ):
                pid = str(hit.get("page_id") or "")
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                hits.append(hit)
                if len(hits) >= limit:
                    break
        return hits[:limit]
    finally:
        if own_store:
            wiki.close()


def format_approved_wiki_context(
    state: dict[str, Any],
    *,
    store: WikiStore | None = None,
    limit: int = 12,
    include_content: bool = False,
) -> str:
    """Approved project/company Wiki context, optionally including source bodies."""
    own_store = store is None
    wiki = store or get_wiki_store()
    try:
        hits = collect_approved_wiki_hits(state, store=wiki, limit=limit)
    except Exception:
        if own_store:
            wiki.close()
        raise
    header = (
        "=== APPROVED WIKI (already agreed) ===\n"
        "Rules: (1) Read approved sources and decide which facts are project-wide or clearly applicable to this feature. "
        "(2) Reuse evidence-backed applicable facts without asking the user to repeat them; cite the source. "
        "(3) Similarity or frequency alone does not prove applicability; do not copy unrelated feature-specific scope or rules. "
        "(4) Agents never approve or write wiki — humans approve. "
        "(5) If sources conflict or applicability is unclear, ask a focused question. "
        "(6) Do not tell the user that no context was found; ask naturally about unresolved requirements.\n"
    )
    if not hits:
        if own_store:
            wiki.close()
        return header + "(no approved wiki pages for this project + company corpus)\n"
    compact = []
    remaining_chars = 18_000
    for hit in hits:
        item = {
            "page_id": hit.get("page_id"),
            "title": hit.get("title"),
            "namespace": hit.get("namespace"),
            "version": hit.get("version"),
            "snippet": hit.get("snippet"),
        }
        if include_content and remaining_chars > 0:
            page_id = str(hit.get("page_id") or "")
            try:
                page = wiki.fetch(page_id, project_id=resolve_project_id(state), agent_name="ba-agent") if page_id else None
            except Exception:
                page = None
            if page:
                body = str(page.get("body") or "")
                body = body[:min(6_000, remaining_chars)]
                item["approved_source_content"] = body
                remaining_chars -= len(body)
        compact.append(item)
    if own_store:
        wiki.close()
    return header + json.dumps(compact, ensure_ascii=False, indent=2) + "\n"
