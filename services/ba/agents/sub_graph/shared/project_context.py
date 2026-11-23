"""Load approved project BA knowledge before starting a new feature."""

from __future__ import annotations

import json
import re
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _section(body: str, heading: str) -> str:
    match = re.search(
        rf"^##\s+{re.escape(heading)}\s*$([\s\S]*?)(?=^##\s|\Z)",
        body,
        re.IGNORECASE | re.MULTILINE,
    )
    return match.group(1).strip() if match else ""


def _bullets(text: str) -> list[str]:
    return list(dict.fromkeys(
        re.sub(r"^[-*+]\s+", "", line).strip()
        for line in text.splitlines()
        if re.match(r"^\s*[-*+]\s+", line) and re.sub(r"^[-*+]\s+", "", line).strip()
    ))


def _feature_record(project_id: str, artifact: dict[str, Any], files, memory) -> dict[str, Any] | None:
    from managers.service.assets import feature_markdown_path

    metadata = artifact.get("meta") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            metadata = {}
    feature_id = str(metadata.get("feature_id") or artifact.get("source_ref") or "")
    if not feature_id:
        return None
    path = feature_markdown_path(project_id, str(artifact.get("title") or "Feature"), feature_id, files)
    note = memory.read_note(project_id, files.markdown_root(project_id), path)
    content = str(note.get("content", note.get("markdown", "")) or "")
    note_metadata, body = files.parse_markdown(content)
    if (note_metadata.get("project_id") != project_id
            or note_metadata.get("status") != "approved"
            or note_metadata.get("type") != "ba_feature"
            or str(note_metadata.get("feature_id") or "") != feature_id):
        return None

    title = str(artifact.get("title") or feature_id)
    goal = _section(body, "Mục tiêu và mô tả")
    actors = _bullets(_section(body, "Actors"))
    scope = _bullets(_section(body, "Trong phạm vi"))
    continuity = _section(body, "Liên kết giữa các feature")
    return {
        "feature_id": feature_id,
        "title": title,
        "version": str(artifact.get("version") or note_metadata.get("source_version") or ""),
        "goal": goal[:500],
        "actors": actors,
        "scope_in": scope[:8],
        "continuity": continuity[:1200],
        "source": path,
    }


def load_project_context(project_id: str, title: str, description: str = "") -> dict[str, Any]:
    """Read latest exported BA notes from this project's Basic Memory workspace.

    This retrieves candidate knowledge only. The BA Agent decides whether each
    item is project-wide or applicable to this feature; this service never fills
    feature artifacts based on frequency or similarity.
    """
    if not project_id:
        return {"status": "unavailable", "reason": "missing_project_id", "related_features": []}
    try:
        from factory_wiki.paths import ensure_mvp_importable
        ensure_mvp_importable()
        from wiki.basic_memory import BasicMemoryClient
        from wiki.project_files import ProjectFileWiki
        from platform_core import artifacts

        memory = BasicMemoryClient()
        if not memory.endpoint:
            return {"status": "unavailable", "reason": "basic_memory_not_configured", "related_features": []}
        files = ProjectFileWiki()
        rows = [row for row in artifacts.list_for_project(project_id)
                if row.get("kind") == "ba_package" and not row.get("stale")]
        records = []
        for row in rows[:40]:
            try:
                record = _feature_record(project_id, row, files, memory)
                if record:
                    records.append(record)
            except Exception:
                # One unavailable or outdated note must not prevent context from
                # other approved features from being used.
                logger.debug("Project Wiki note could not be read for BA context: %s", row.get("title"), exc_info=True)
                continue

        def tokens(value: str) -> set[str]:
            return {word.casefold() for word in re.findall(r"[\wÀ-ỹ]+", value, re.UNICODE) if len(word) > 2}

        target = tokens(f"{title} {description}")
        ranked = []
        for record in records:
            corpus = tokens(" ".join([
                record["title"], record["goal"], record["continuity"], *record["scope_in"]
            ]))
            overlap = len(target & corpus)
            ratio = overlap / max(1, len(target))
            ranked.append((ratio, overlap, record))
        ranked.sort(key=lambda item: (item[0], item[1], item[2]["version"]), reverse=True)
        # Retrieval ranking selects possible sources only. Applicability and
        # reuse remain an Agent decision, never a deterministic actor rule.
        related = [record for ratio, overlap, record in ranked if overlap >= 1][:8]
        actor_sources: dict[str, dict[str, Any]] = {}
        for record in related:
            for actor in record["actors"]:
                source = {
                    "feature_id": record["feature_id"],
                    "title": record["title"],
                    "goal": record["goal"],
                }
                entry = actor_sources.setdefault(actor.casefold(), {"value": actor, "sources": []})
                entry["sources"].append(source)
        return {
            "status": "ready" if records else "empty",
            "actor_mentions": list(actor_sources.values()),
            "related_features": related,
        }
    except Exception:
        logger.exception("Could not load Basic Memory context for BA project %s", project_id)
        return {"status": "unavailable", "reason": "wiki_read_failed", "related_features": []}


def fd_context_prompt(context: dict[str, Any] | None) -> str:
    """Compact, cited context block for the BA Feature Definition Agent."""
    context = context or {}
    if context.get("status") not in {"ready", "empty"}:
        return "=== PROJECT MEMORY ===\nProject Wiki context is unavailable; do not claim that it was checked."
    return (
        "=== PROJECT MEMORY (Basic Memory, project-scoped) ===\n"
        "Read the approved Wiki sources below and decide which facts are clearly project-wide or applicable to this feature. "
        "Reuse only evidence-backed facts; cite their source in your response. Similarity or frequency alone does not prove that a fact applies. "
        "Do not treat an unrelated feature's actors, scope, or rules as shared. Use an incoming/outgoing handoff only when the source names this feature or the destination matches it. "
        "When applicability is uncertain or sources conflict, ask the BA instead of filling the artifact. "
        "Do not tell the user that no context was found; ask naturally about the unresolved requirements.\n"
        + json.dumps({
            "actors_mentioned_in_approved_features": context.get("actor_mentions", []),
            "related_features": context.get("related_features", []),
        }, ensure_ascii=False, indent=2)
    )
