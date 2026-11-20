"""Wiki MVP document adapter (approved pages only). Same 3-table store, not a fork.

Visibility for BA/VA agents (matches MVP intent):
- ``namespace=project`` + ``project_id`` — private to that BA feature project
- ``namespace=company`` — org-wide corpus visible to every project
Never return another project's private pages. Company is not a fake project_id;
upload with Namespace=company in wiki.html (project_id is ignored for scope).
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from factory_wiki.paths import (
    ensure_manifest,
    ensure_mvp_importable,
    wiki_service_root,
)

AGENT_VA = "validation-agent"
AGENT_BA = "ba-agent"
logger = logging.getLogger(__name__)

# MVP namespaces — company is shared; project is scoped by feature.project_id.
NAMESPACE_COMPANY = "company"
NAMESPACE_PROJECT = "project"

# SQL fragment: approved pages visible to a feature's project_id.
_VISIBLE_APPROVED_SQL = (
    "status = ? AND ("
    "(namespace = ? AND project_id = ?) OR namespace = ?"
    ")"
)


def _mvp():
    ensure_mvp_importable()
    from wiki.db import connect, row_to_dict
    from wiki.models import Actor, WikiError
    from wiki.permissions import require_project_scope, require_tool

    return connect, row_to_dict, Actor, WikiError, require_project_scope, require_tool


def agent_actor(project_id: str, name: str = AGENT_VA):
    _connect, _row, Actor, *_rest = _mvp()
    return Actor(
        actor=name,
        actor_type="agent",
        agent_name=name,
        project_id=project_id,
    )


def human_actor(project_id: str):
    _connect, _row, Actor, *_rest = _mvp()
    return Actor(actor="ba.lead", actor_type="human", project_id=project_id)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _visible_approved_args(project_id: str) -> tuple[Any, ...]:
    return ("approved", NAMESPACE_PROJECT, project_id, NAMESPACE_COMPANY)


class WikiStore:
    """Search/fetch port used by VA and the feature documents API.

    Opens the Wiki Knowledge MVP tables (``wiki_page`` / ``wiki_revision`` /
    ``wiki_source``) in the shared PostgreSQL schema and applies permission helpers so agents cannot
    search drafts or other projects. Does not import ``WikiService`` (PDF
    ingest) — document I/O only.

    Scope: feature ``project_id`` private pages **plus** ``namespace=company``.
    """

    def __init__(self, root: Path | None = None, *, agent_name: str = AGENT_VA) -> None:
        connect, row_to_dict, _Actor, WikiError, require_project_scope, require_tool = _mvp()
        self._connect = connect
        self.root = wiki_service_root(root)
        ensure_manifest(self.root)
        self.conn = connect(self.root)
        self._configure_conn()
        self._row_to_dict = row_to_dict
        self._WikiError = WikiError
        self._require_project_scope = require_project_scope
        self._require_tool = require_tool
        self.agent_name = agent_name

    def _configure_conn(self) -> None:
        # Autocommit so a long-lived handle sees commits from wiki.html / WikiService.
        self.conn.isolation_level = None
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")

    def reopen(self) -> None:
        """Close and re-open the PostgreSQL-backed Wiki connection."""
        try:
            self.conn.close()
        except Exception:
            pass
        self.conn = self._connect(self.root)
        self._configure_conn()

    def close(self) -> None:
        self.conn.close()

    def _agent_may_read(self, project_id: str, agent_name: str | None) -> bool:
        actor = agent_actor(project_id, agent_name or self.agent_name)
        try:
            self._require_tool(self.root, actor, "wiki.search_pages")
            self._require_project_scope(
                actor, namespace=NAMESPACE_PROJECT, project_id=project_id
            )
        except self._WikiError as exc:
            if getattr(exc, "http_status", 400) in {403, 404, 412}:
                return False
            raise PermissionError(exc.message) from exc
        return True

    def _hit_from_row(self, row: Any) -> dict[str, Any]:
        body = row["body"] or ""
        return {
            "page_id": row["page_id"],
            "title": row["title"],
            "snippet": body[:160] + ("..." if len(body) > 160 else ""),
            "version": row["version"],
            "bucket_id": row["bucket_id"],
            "namespace": row["namespace"] if "namespace" in row.keys() else NAMESPACE_PROJECT,
            "project_id": row["project_id"] if "project_id" in row.keys() else None,
            "status": "approved",
            "updated_at": row["updated_at"] if "updated_at" in row.keys() else "",
        }

    def list_approved(
        self,
        *,
        project_id: str,
        limit: int = 500,
        agent_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Approved pages for this project plus company wiki. Drafts never appear."""
        self.reopen()
        if not self._agent_may_read(project_id, agent_name):
            return []

        rows = self.conn.execute(
            f"""
            SELECT page_id, title, body, version, bucket_id, status, updated_at,
                   namespace, project_id
            FROM wiki_page
            WHERE {_VISIBLE_APPROVED_SQL}
            ORDER BY updated_at DESC, page_id ASC
            LIMIT ?
            """,
            (*_visible_approved_args(project_id), max(1, min(limit, 500))),
        ).fetchall()
        return [self._hit_from_row(row) for row in rows]

    def search(
        self,
        query: str,
        *,
        project_id: str,
        status: str = "approved",
        limit: int = 5,
        bucket_id: str | None = None,
        agent_name: str | None = None,
    ) -> list[dict[str, Any]]:
        if status != "approved":
            raise PermissionError("Agent search must force status=approved")
        self.reopen()
        if not self._agent_may_read(project_id, agent_name):
            return []

        # Basic Memory ranks candidates; PostgreSQL remains authoritative for
        # approval state and the agent's project boundary.
        bm_hits: list[dict[str, Any]] = []
        bm_url = os.getenv("BASIC_MEMORY_MCP_URL", "").strip()
        if bm_url and query and not bucket_id:
            try:
                from wiki.basic_memory import BasicMemoryClient
                from wiki.project_files import ProjectFileWiki

                files = ProjectFileWiki()
                for hit in BasicMemoryClient(bm_url).search(
                    project_id, files.markdown_root(project_id), query, max(limit * 3, 10),
                ):
                    path = str(hit.get("file_path") or hit.get("path") or hit.get("identifier") or "")
                    parts = PurePosixPath(path.replace("\\", "/")).parts
                    if "pages" in parts and parts.index("pages") + 1 < len(parts):
                        page_id = parts[parts.index("pages") + 1]
                    else:
                        page_id = PurePosixPath(path).stem
                    page_id = str(hit.get("page_id") or page_id)
                    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", page_id):
                        continue
                    row = self.conn.execute(
                        f"SELECT page_id, title, body, version, bucket_id, status, updated_at, namespace, project_id "
                        f"FROM wiki_page WHERE {_VISIBLE_APPROVED_SQL} AND page_id = ?",
                        (*_visible_approved_args(project_id), page_id),
                    ).fetchone()
                    if row:
                        result = self._hit_from_row(row)
                        result["score"] = hit.get("score")
                        result["snippet"] = str(
                            hit.get("snippet") or hit.get("excerpt") or result["snippet"]
                        )
                        bm_hits.append(result)
                    elif path.replace("\\", "/").casefold().startswith("features/"):
                        note = BasicMemoryClient(bm_url).read_note(
                            project_id, files.markdown_root(project_id), path.replace("\\", "/"),
                        )
                        content = str(note.get("content", note.get("markdown", "")) or "")
                        metadata, note_body = files.parse_markdown(content)
                        if (metadata.get("project_id") != project_id
                                or metadata.get("status") != "approved"
                                or metadata.get("type") != "ba_feature"):
                            continue
                        artifact_id = str(metadata.get("source_artifact_id") or "")
                        artifact = self.conn.execute(
                            "SELECT id, title, version, created_at FROM core.artifacts "
                            "WHERE id=? AND project_id=? AND kind='ba_package'",
                            (artifact_id, project_id),
                        ).fetchone()
                        if artifact:
                            bm_hits.append({
                                "page_id": artifact["id"], "artifact_id": artifact["id"],
                                "feature_id": metadata.get("feature_id"),
                                "namespace": NAMESPACE_PROJECT, "project_id": project_id,
                                "bucket_id": "ba_feature", "title": artifact["title"],
                                "snippet": note_body[:240],
                                "version": str(artifact["version"]), "status": "approved",
                                "updated_at": str(artifact["created_at"]), "score": hit.get("score"),
                            })
            except Exception:
                logger.exception("Basic Memory search failed; using PostgreSQL Wiki search")

        sql = [
            "SELECT page_id, title, body, version, bucket_id, status, updated_at,",
            "namespace, project_id",
            f"FROM wiki_page WHERE {_VISIBLE_APPROVED_SQL}",
        ]
        args: list[Any] = list(_visible_approved_args(project_id))
        if bucket_id:
            sql.append("AND bucket_id = ?")
            args.append(bucket_id)
        if query:
            sql.append(
                "AND (title LIKE ? OR ifnull(abbrev,'') LIKE ? OR ifnull(expansion,'') LIKE ? "
                "OR (namespace = ? AND body LIKE ?))"
            )
            like = f"%{query}%"
            args.extend([like, like, like, NAMESPACE_COMPANY, like])
        sql.append("ORDER BY updated_at DESC, page_id ASC LIMIT ?")
        args.append(max(1, min(limit, 50)))
        rows = self.conn.execute(" ".join(sql), args).fetchall()
        results = bm_hits + [self._hit_from_row(row) for row in rows]
        ordered: list[dict[str, Any]] = []
        seen: set[str] = set()
        for hit in results:
            if hit["page_id"] not in seen:
                seen.add(hit["page_id"])
                ordered.append(hit)
        return ordered[:max(1, min(limit, 50))]

    def fetch(
        self,
        page_id: str,
        *,
        project_id: str,
        agent_name: str | None = None,
    ) -> dict | None:
        self.reopen()
        actor = agent_actor(project_id, agent_name or self.agent_name)
        try:
            self._require_tool(self.root, actor, "wiki.fetch_page")
        except self._WikiError as exc:
            raise PermissionError(exc.message) from exc

        row = self.conn.execute(
            "SELECT * FROM wiki_page WHERE page_id = ?", (page_id,)
        ).fetchone()
        if not row:
            # Feature notes map back to approved artifact metadata in PostgreSQL;
            # the actual Wiki body is read from its Basic Memory note.
            artifact = self.conn.execute(
                "SELECT id, project_id, title, version, storage_key, created_at, meta FROM core.artifacts "
                "WHERE id=? AND project_id=? AND kind='ba_package'",
                (page_id, project_id),
            ).fetchone()
            if not artifact or not artifact["storage_key"]:
                return None
            try:
                self._require_project_scope(
                    actor, namespace=NAMESPACE_PROJECT, project_id=artifact["project_id"],
                )
            except self._WikiError:
                return None
            if artifact["project_id"] != project_id:
                return None
            try:
                from managers.service.assets import feature_markdown_path
                from wiki.basic_memory import BasicMemoryClient
                from wiki.project_files import ProjectFileWiki

                files = ProjectFileWiki()
                meta = artifact["meta"] or {}
                if isinstance(meta, str):
                    import json
                    meta = json.loads(meta)
                feature_id = str(meta.get("feature_id") or artifact["id"])
                note_path = feature_markdown_path(project_id, artifact["title"], feature_id, files)
                note = BasicMemoryClient().read_note(
                    project_id, files.markdown_root(project_id), note_path,
                )
                content = str(note.get("content", note.get("markdown", "")) or "")
                note_metadata, package = files.parse_markdown(content)
                if (note_metadata.get("source_artifact_id") != artifact["id"]
                        or str(note_metadata.get("source_version")) != str(artifact["version"])
                        or note_metadata.get("status") != "approved"):
                    raise ValueError("Basic Memory feature note metadata does not match its artifact")
            except Exception as error:
                message = str(error).casefold()
                if ("not_found" in message or "not found" in message
                        or "basic_memory_mcp_url is not configured" in message):
                    from platform_core.storage import get_storage

                    package = get_storage().get(artifact["storage_key"]).decode("utf-8")
                else:
                    logger.exception("Could not read canonical Basic Memory feature Wiki for artifact %s", page_id)
                    return None
            return {
                "page_id": artifact["id"], "artifact_id": artifact["id"],
                "namespace": NAMESPACE_PROJECT, "project_id": project_id,
                "bucket_id": "ba_feature", "title": artifact["title"],
                "status": "approved", "version": str(artifact["version"]),
                "body": package, "updated_at": str(artifact["created_at"]),
            }
        page = self._row_to_dict(row)
        try:
            self._require_project_scope(
                actor, namespace=page["namespace"], project_id=page.get("project_id")
            )
        except self._WikiError:
            return None

        ns = page.get("namespace")
        if actor.actor_type == "agent" and page.get("status") != "approved":
            raise PermissionError("Agent fetch denied for non-approved page")

        if ns == NAMESPACE_PROJECT:
            if page.get("project_id") != project_id:
                return None
            if actor.project_id and actor.project_id != page.get("project_id"):
                return None
        elif ns == NAMESPACE_COMPANY:
            pass  # shared org corpus — any feature project may read when approved
        else:
            return None

        body = page["body"]
        if ns == NAMESPACE_PROJECT:
            try:
                from wiki.basic_memory import BasicMemoryClient
                from wiki.project_files import ProjectFileWiki

                safe_id = re.sub(r"[^A-Za-z0-9_-]", "-", str(page_id)).strip("-")[:100] or "page"
                files = ProjectFileWiki()
                note = BasicMemoryClient().read_note(
                    project_id, files.markdown_root(project_id), f"pages/{safe_id}.md",
                )
                content = note.get("content", note.get("markdown", ""))
                metadata, body = files.parse_markdown(content)
                if (str(metadata.get("version")) != str(page["version"])
                        or metadata.get("page_id") != page_id
                        or metadata.get("project_id") != project_id
                        or metadata.get("status") != page["status"]):
                    raise ValueError("Basic Memory page metadata does not match PostgreSQL metadata")
                heading = f"# {page['title']}"
                if body.startswith(heading):
                    body = body[len(heading):].lstrip()
                footer = f"Source Wiki page: `{page_id}`."
                if body.rstrip().endswith(footer):
                    body = body.rstrip()[:-len(footer)].rstrip()
            except Exception as error:
                message = str(error).casefold()
                legacy_fallback = (
                    "not_found" in message or "not found" in message
                    or "basic_memory_mcp_url is not configured" in message
                )
                if not body or not legacy_fallback:
                    logger.exception("Could not read canonical BA Project Wiki page %s", page_id)
                    return None

        return {
            "page_id": page["page_id"],
            "namespace": page["namespace"],
            "project_id": page.get("project_id"),
            "bucket_id": page["bucket_id"],
            "title": page["title"],
            "status": page["status"],
            "version": page["version"],
            "body": body,
            "updated_at": page["updated_at"],
        }

    def seed_page(
        self,
        *,
        page_id: str,
        title: str,
        body: str,
        project_id: str | None,
        bucket_id: str = "business_rule",
        status: str = "approved",
        version: str = "1.0.0",
        namespace: str = NAMESPACE_PROJECT,
    ) -> None:
        """Test helper: insert a page into the MVP schema (human path, not agent)."""
        if namespace not in {NAMESPACE_COMPANY, NAMESPACE_PROJECT}:
            raise ValueError(f"namespace must be {NAMESPACE_COMPANY!r} or {NAMESPACE_PROJECT!r}")
        if namespace == NAMESPACE_PROJECT and not project_id:
            raise ValueError("project_id is required when namespace=project")
        now = _now()
        stored_project = None if namespace == NAMESPACE_COMPANY else project_id
        self.conn.execute(
            """
            INSERT INTO wiki_page (
              page_id, namespace, project_id, bucket_id, title, abbrev, expansion,
              disambiguation, status, version, body, source_ids, owner, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(page_id) DO UPDATE SET
              status=excluded.status,
              body=excluded.body,
              title=excluded.title,
              namespace=excluded.namespace,
              project_id=excluded.project_id,
              updated_at=excluded.updated_at
            """,
            (
                page_id,
                namespace,
                stored_project,
                bucket_id,
                title,
                None,
                None,
                None,
                status,
                version,
                body,
                "[]",
                "ba.lead",
                now,
            ),
        )
        n = self.conn.execute("SELECT COUNT(*) AS c FROM wiki_revision").fetchone()["c"]
        self.conn.execute(
            """
            INSERT INTO wiki_revision (
              revision_id, page_id, version, status, body_snapshot, diff_ref, actor, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (f"REV-{n + 1:04d}", page_id, version, status, body, None, "ba.lead", now),
        )
        self.conn.commit()


def get_wiki_store(root: Path | None = None) -> WikiStore:
    return WikiStore(root)
