from __future__ import annotations

import hashlib
import json
import logging
import os
import re

from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from wiki.audit import log_audit
from wiki.db import Connection, connect, row_to_dict
from wiki.extract import extract_pdf
from wiki.models import (
    Actor,
    ProposePageIn,
    SearchHit,
    SearchQuery,
    WikiError,
    WikiPage,
    WikiSource,
)
from wiki.llm_config import build_sorter
from wiki.paths import blob_key, delete_blob, put_blob
from wiki.permissions import require_project_scope, require_tool
from wiki.sort import Sorter

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _slug(text: str, fallback: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").upper()
    return slug[:24] or fallback


class WikiService:
    def __init__(self, root: Path, sorter: Sorter | None = None) -> None:
        self.root = root
        self.sorter = sorter or build_sorter()
        self._conn = connect(root)

    @property
    def conn(self) -> "Connection":
        return self._conn

    def close(self) -> None:
        self._conn.close()

    def reset_store(self) -> dict:
        """Wipe the wiki tables + their blobs only. Never touches BA/ADK schemas."""
        from wiki.db import TABLES, schema_name

        schema = schema_name()
        names = {
            row[0]
            for row in self.conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = ?",
                (schema,),
            )
        }
        foreign = names & {
            "ba_packages",
            "ba_package_sections",
            "sessions",
            "events",
            "app_states",
            "user_states",
            "adk_internal_metadata",
        }
        if foreign:
            raise WikiError(
                "COMMON_PRECONDITION_FAILED",
                f"refusing to clear schema {schema}: contains BA/ADK tables {sorted(foreign)}",
                409,
            )
        keys = [
            str(row["storage_uri"])
            for row in self.conn.execute("SELECT storage_uri FROM wiki_source")
        ] if "wiki_source" in names else []
        for table in TABLES:
            if table in names:
                self.conn.execute(f"DELETE FROM {table}")
        self.conn.commit()
        removed_blobs = 0
        for key in keys:
            try:
                removed_blobs += int(delete_blob(key))
            except ValueError:
                continue  # legacy absolute paths are not ours to delete
        return {"pages": 0, "sources": 0, "removed_blobs": removed_blobs, "wiki_db": f"postgres:{schema}"}

    def ingest_source(
        self,
        actor: Actor,
        *,
        filename: str,
        data: bytes,
        namespace: str,
        project_id: str | None,
        source_id: str | None = None,
        sort_mode: str | None = None,
    ) -> dict:
        require_tool(self.root, actor, "wiki.ingest_source")
        require_project_scope(actor, namespace=namespace, project_id=project_id)
        if namespace not in {"company", "project"}:
            raise WikiError("COMMON_SCHEMA_INVALID", "namespace must be company or project")
        if not data:
            raise WikiError("COMMON_SCHEMA_INVALID", "empty file")

        prev_sorter = self.sorter
        if sort_mode:
            self.sorter = build_sorter(sort_mode)
        try:
            return self._ingest_source_body(
                actor,
                filename=filename,
                data=data,
                namespace=namespace,
                project_id=project_id,
                source_id=source_id,
            )
        finally:
            self.sorter = prev_sorter

    def _ingest_source_body(
        self,
        actor: Actor,
        *,
        filename: str,
        data: bytes,
        namespace: str,
        project_id: str | None,
        source_id: str | None = None,
    ) -> dict:
        digest = _sha256(data)
        existing = self.conn.execute(
            """
            SELECT * FROM wiki_source
            WHERE sha256 = ?
              AND namespace = ?
              AND (
                (CAST(? AS TEXT) IS NULL AND project_id IS NULL)
                OR project_id = ?
              )
            """,
            (digest, namespace, project_id, project_id),
        ).fetchone()
        if existing:
            src = WikiSource(**row_to_dict(existing))
            page_ids = [
                str(r["page_id"])
                for r in self.conn.execute(
                    """
                    SELECT page_id FROM wiki_page
                    WHERE source_ids LIKE ?
                    ORDER BY updated_at DESC
                    """,
                    (f'%"{src.source_id}"%',),
                ).fetchall()
            ]
            log_audit(
                self.root,
                operation="wiki.ingest_source",
                actor=actor,
                extra={"source_id": src.source_id, "dedup": True, "proposed_page_ids": page_ids},
            )
            return {
                "source_id": src.source_id,
                "extract_status": "complete",
                "sort_status": "unchanged_existing_source",
                "sort_backend": "dedup",
                "proposed_page_ids": page_ids,
            }

        source_id = source_id or f"SRC-{digest[:8].upper()}"
        # Same bytes may already exist under another project/namespace — use a distinct source_id.
        if self.conn.execute(
            "SELECT 1 FROM wiki_source WHERE source_id = ?", (source_id,)
        ).fetchone():
            source_id = f"SRC-{digest[:8].upper()}-{_slug_project(project_id or namespace)}"
        suffix = Path(filename).suffix.lower()
        if data.startswith(b"%PDF"):
            ext = ".pdf"
        elif suffix in {".txt", ".md", ".markdown"}:
            ext = suffix
        else:
            ext = ".txt" if suffix == "" else suffix
        storage_key = blob_key(f"{source_id}{ext}", namespace, project_id)
        blob = put_blob(storage_key, data)

        markdown, page_map = extract_pdf(blob, filename)
        ingested_at = _now()
        markdown_extract: str | None = markdown
        if namespace == "project" and project_id and os.getenv("BASIC_MEMORY_MCP_URL", "").strip():
            from wiki.project_files import ProjectFileWiki

            files = ProjectFileWiki()
            source_note_path = f"sources/{source_id}.md"
            source_metadata = {
                "source_id": source_id, "filename": filename, "sha256": digest,
                "project_id": project_id, "namespace": "project", "version": "1",
                "status": "approved", "type": "source_extraction", "ingested_at": ingested_at,
                "ingested_by": actor.actor,
            }
            source_note = files._yaml(source_metadata) + markdown.rstrip() + "\n"
            files.write_basic_memory_note(project_id, source_note_path, source_note)
            snapshot_key = files.key(project_id, f"history/sources/{source_id}.md")
            files.storage.put(snapshot_key, source_note.encode("utf-8"), content_type="text/markdown; charset=utf-8")
            markdown_extract = None
        self.conn.execute(
            """
            INSERT INTO wiki_source (
              source_id, filename, sha256, namespace, project_id, storage_uri,
              markdown_extract, page_map, ingested_at, ingested_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id,
                filename,
                digest,
                namespace,
                project_id,
                storage_key,
                markdown_extract,
                json.dumps(page_map),
                ingested_at,
                actor.actor,
            ),
        )

        drafts = self.sorter.sort(
            markdown,
            source_id=source_id,
            namespace=namespace,
            project_id=project_id,
            owner=actor.actor,
            now=ingested_at,
        )
        sort_backend = getattr(self.sorter, "last_backend", "heuristic")
        page_ids: list[str] = []
        for page in drafts:
            existing = self.conn.execute(
                "SELECT * FROM wiki_page WHERE page_id = ?", (page.page_id,)
            ).fetchone()
            if existing:
                old = row_to_dict(existing)
                merged = list(dict.fromkeys(old["source_ids"] + page.source_ids))
                same_scope = (old.get("namespace") == page.namespace) and (
                    (old.get("project_id") or None) == (page.project_id or None)
                )
                # Approved or cross-scope collisions: only attach the new source id.
                # Same-scope drafts must refresh title/body or uploads look stuck.
                if str(old.get("status") or "") == "approved" or not same_scope:
                    self.conn.execute(
                        "UPDATE wiki_page SET source_ids = ? WHERE page_id = ?",
                        (json.dumps(merged), page.page_id),
                    )
                    page_ids.append(page.page_id)
                    continue
                page.source_ids = merged
                page.status = "draft"
                page.namespace = old["namespace"]
                page.project_id = old.get("project_id")
                page_ids.append(
                    self._upsert_page(page, actor.actor, create_revision=True)
                )
                continue
            page_ids.append(self._upsert_page(page, actor.actor, create_revision=True))

        self.conn.commit()
        log_audit(
            self.root,
            operation="wiki.ingest_source",
            actor=actor,
            extra={
                "source_id": source_id,
                "proposed_page_ids": page_ids,
                "sort_backend": sort_backend,
            },
        )
        return {
            "source_id": source_id,
            "extract_status": "complete",
            "sort_status": "draft_pending_review",
            "sort_backend": sort_backend,
            "proposed_page_ids": page_ids,
        }

    def get_source(self, actor: Actor, source_id: str) -> WikiSource:
        require_tool(self.root, actor, "wiki.read_source")
        row = self.conn.execute(
            "SELECT * FROM wiki_source WHERE source_id = ?", (source_id,)
        ).fetchone()
        if not row:
            raise WikiError("COMMON_REFERENCE_NOT_FOUND", f"unknown source_id {source_id}", 404)
        src = WikiSource(**row_to_dict(row))
        require_project_scope(actor, namespace=src.namespace, project_id=src.project_id)
        if actor.actor_type == "agent" and src.namespace == "project":
            if actor.project_id != src.project_id:
                raise WikiError("COMMON_PERMISSION_DENIED", "agent cannot read another project", 403)
        if src.markdown_extract is None:
            if src.namespace != "project" or not src.project_id:
                raise WikiError("COMMON_PRECONDITION_FAILED", "source extraction is missing from metadata", 409)
            from wiki.basic_memory import BasicMemoryClient
            from wiki.project_files import ProjectFileWiki

            files = ProjectFileWiki()
            try:
                note = BasicMemoryClient().read_note(
                    src.project_id, files.markdown_root(src.project_id), f"sources/{src.source_id}.md",
                )
                content = note.get("content", note.get("markdown", ""))
                if not isinstance(content, str) or not content:
                    raise ValueError("Basic Memory returned no source Markdown")
                metadata, markdown = files.parse_markdown(content)
                if metadata.get("project_id") != src.project_id or metadata.get("source_id") != src.source_id:
                    raise ValueError("Basic Memory source metadata does not match PostgreSQL")
                src.markdown_extract = markdown
            except Exception as error:
                try:
                    from platform_core.storage import get_storage

                    snapshot = get_storage().get(files.key(src.project_id, f"history/sources/{src.source_id}.md")).decode("utf-8")
                    metadata, markdown = files.parse_markdown(snapshot)
                    if metadata.get("project_id") != src.project_id or metadata.get("source_id") != src.source_id:
                        raise ValueError("snapshot metadata does not match source")
                    src.markdown_extract = markdown
                except Exception as recovery_error:
                    raise WikiError("COMMON_REFERENCE_NOT_FOUND", f"source Markdown unavailable: {error}; recovery copy unavailable: {recovery_error}", 404) from recovery_error
        log_audit(self.root, operation="wiki.read_source", actor=actor, extra={"source_id": source_id})
        return src

    def propose_page(self, actor: Actor, payload: ProposePageIn) -> WikiPage:
        require_tool(self.root, actor, "wiki.propose_page")
        require_project_scope(
            actor, namespace=payload.namespace, project_id=payload.project_id
        )
        now = _now()
        page_id = payload.page_id or f"PG-{_slug(payload.abbrev or payload.title or 'PAGE', 'NEW')}"
        existing = self.conn.execute(
            "SELECT * FROM wiki_page WHERE page_id = ?", (page_id,)
        ).fetchone()
        page = WikiPage(
            page_id=page_id,
            namespace=payload.namespace,
            project_id=payload.project_id,
            bucket_id=payload.bucket_id,
            title=payload.title or payload.abbrev or page_id,
            abbrev=payload.abbrev,
            expansion=payload.expansion,
            disambiguation=payload.disambiguation,
            status="draft" if existing is None else "proposed",
            version=payload.version,
            body=payload.body,
            source_ids=payload.source_ids,
            owner=payload.owner or actor.actor,
            updated_at=now,
        )
        if existing and dict(existing)["status"] == "approved":
            # new version, back to proposed — never overwrite approved in place without revision
            major, minor, patch = _parse_semver(dict(existing)["version"])
            page.version = f"{major}.{minor + 1}.0"
            page.status = "proposed"
        self._upsert_page(page, actor.actor, create_revision=True)
        self._write_project_file(page, actor.actor)
        self.conn.commit()
        log_audit(
            self.root,
            operation="wiki.propose_page",
            actor=actor,
            extra={"page_id": page.page_id, "status": page.status},
        )
        return self.fetch_page(actor, page.page_id, page.version, as_propose_followup=True)

    def restore_revision(self, actor: Actor, page_id: str, revision_id: str) -> WikiPage:
        """Restore an immutable Project Wiki snapshot as a new proposed revision."""
        require_tool(self.root, actor, "wiki.propose_page")
        page_row = self.conn.execute("SELECT * FROM wiki_page WHERE page_id = ?", (page_id,)).fetchone()
        revision = self.conn.execute(
            "SELECT * FROM wiki_revision WHERE revision_id = ? AND page_id = ?", (revision_id, page_id),
        ).fetchone()
        if not page_row or not revision:
            raise WikiError("COMMON_REFERENCE_NOT_FOUND", "page or revision not found", 404)
        page_data = row_to_dict(page_row)
        if page_data["namespace"] != "project" or not page_data.get("project_id"):
            raise WikiError("COMMON_PRECONDITION_FAILED", "only project Wiki revisions can be restored", 409)
        require_project_scope(actor, namespace="project", project_id=page_data["project_id"])
        body = revision["body_snapshot"] or ""
        if not body:
            ref = revision["diff_ref"]
            if not ref:
                raise WikiError("COMMON_REFERENCE_NOT_FOUND", "revision has no recovery snapshot", 404)
            try:
                from platform_core.storage import get_storage
                from wiki.project_files import ProjectFileWiki

                content = get_storage().get(str(ref)).decode("utf-8")
                metadata, body = ProjectFileWiki.parse_markdown(content)
                if metadata.get("project_id") != page_data["project_id"] or metadata.get("page_id") != page_id:
                    raise ValueError("snapshot metadata does not match requested page")
                heading = f"# {page_data['title']}"
                if body.startswith(heading):
                    body = body[len(heading):].lstrip()
                footer = f"Source Wiki page: `{page_id}`."
                if body.rstrip().endswith(footer):
                    body = body.rstrip()[:-len(footer)].rstrip()
            except Exception as error:
                raise WikiError("COMMON_REFERENCE_NOT_FOUND", f"recovery snapshot unavailable: {error}", 404) from error
        major, minor, _patch = _parse_semver(str(page_data["version"]))
        payload = ProposePageIn(
            namespace="project", project_id=page_data["project_id"], bucket_id=page_data["bucket_id"],
            title=page_data["title"], abbrev=page_data.get("abbrev"), expansion=page_data.get("expansion"),
            disambiguation=page_data.get("disambiguation"), body=body,
            source_ids=page_data.get("source_ids", []), owner=actor.actor, version=f"{major}.{minor + 1}.0",
        )
        restored = self.propose_page(actor, payload.model_copy(update={"page_id": page_id}))
        log_audit(self.root, operation="wiki.restore_revision", actor=actor,
                  extra={"page_id": page_id, "revision_id": revision_id, "new_version": restored.version})
        return restored

    def search_pages(self, actor: Actor, q: SearchQuery) -> list[SearchHit]:
        require_tool(self.root, actor, "wiki.search_pages")
        require_project_scope(actor, namespace=q.namespace, project_id=q.project_id)
        status = q.status
        if actor.actor_type == "agent":
            status = "approved"
        if not q.namespace:
            raise WikiError("COMMON_SCHEMA_INVALID", "namespace is required")

        sql = [
            "SELECT page_id, title, body, version, bucket_id, status, namespace, project_id",
            "FROM wiki_page WHERE namespace = ?",
        ]
        args: list = [q.namespace]
        if q.project_id:
            sql.append("AND project_id = ?")
            args.append(q.project_id)
        elif actor.actor_type == "agent" and actor.project_id:
            sql.append("AND (project_id = ? OR namespace = 'company')")
            args.append(actor.project_id)
        if q.bucket_id:
            sql.append("AND bucket_id = ?")
            args.append(q.bucket_id)
        if status:
            sql.append("AND status = ?")
            args.append(status)
        if q.query:
            if q.namespace == "project":
                sql.append("AND (title LIKE ? OR ifnull(abbrev,'') LIKE ? OR ifnull(expansion,'') LIKE ?)")
            else:
                sql.append("AND (title LIKE ? OR body LIKE ? OR ifnull(abbrev,'') LIKE ? OR ifnull(expansion,'') LIKE ?)")
            like = f"%{q.query}%"
            args.extend([like, like, like] if q.namespace == "project" else [like, like, like, like])

        sql.append("LIMIT ?")
        args.append(max(1, min(q.limit, 50)))
        rows = self.conn.execute(" ".join(sql), args).fetchall()
        hits: list[SearchHit] = []
        if q.namespace == "project" and q.project_id and q.query and status == "approved":
            try:
                from wiki.basic_memory import BasicMemoryClient
                from wiki.project_files import ProjectFileWiki

                files = ProjectFileWiki()
                for item in BasicMemoryClient().search(
                    q.project_id, files.markdown_root(q.project_id), q.query,
                    max(q.limit * 3, 10), status="approved",
                ):
                    path = str(item.get("file_path") or item.get("path") or item.get("identifier") or "")
                    parts = PurePosixPath(path.replace("\\", "/")).parts
                    if "pages" not in parts or parts.index("pages") + 1 >= len(parts):
                        continue
                    page_id = PurePosixPath(parts[parts.index("pages") + 1]).stem
                    row = self.conn.execute(
                        "SELECT page_id, title, version, bucket_id FROM wiki_page "
                        "WHERE page_id = ? AND namespace = ? AND project_id = ? AND status = ?",
                        (page_id, "project", q.project_id, "approved"),
                    ).fetchone()
                    if not row or (q.bucket_id and row["bucket_id"] != q.bucket_id):
                        continue
                    excerpt = str(item.get("snippet") or item.get("excerpt") or "")
                    hits.append(SearchHit(
                        page_id=row["page_id"], title=row["title"], snippet=excerpt[:240],
                        version=row["version"], bucket_id=row["bucket_id"],
                        score=float(item.get("score") or 0.7),
                    ))
            except Exception:
                logger.exception("Basic Memory Project Wiki search failed; using metadata-only search")
        needle = (q.query or "").lower()
        bm_page_ids = {hit.page_id for hit in hits}
        for row in rows:
            if row["page_id"] in bm_page_ids:
                continue
            body = row["body"] or ""
            snippet = body[:160] + ("..." if len(body) > 160 else "")
            score = 0.5
            if needle and needle in (row["title"] or "").lower():
                score = 0.91
            elif needle and needle in body.lower():
                score = 0.75
            hits.append(
                SearchHit(
                    page_id=row["page_id"],
                    title=row["title"],
                    snippet=snippet,
                    version=row["version"],
                    bucket_id=row["bucket_id"],
                    score=score,
                )
            )
        log_audit(
            self.root,
            operation="wiki.search_pages",
            actor=actor,
            extra={"namespace": q.namespace, "hits": len(hits)},
        )
        return hits

    def fetch_page(
        self,
        actor: Actor,
        page_id: str,
        version: str | None = None,
        *,
        as_propose_followup: bool = False,
    ) -> WikiPage:
        if not as_propose_followup:
            require_tool(self.root, actor, "wiki.fetch_page")
        row = self.conn.execute(
            "SELECT * FROM wiki_page WHERE page_id = ?", (page_id,)
        ).fetchone()
        if not row:
            raise WikiError("COMMON_REFERENCE_NOT_FOUND", f"unknown page_id {page_id}", 404)
        page_data = row_to_dict(row)
        require_project_scope(actor, namespace=page_data["namespace"], project_id=page_data.get("project_id"))
        if actor.actor_type == "agent" and page_data["namespace"] == "project":
            if actor.project_id != page_data.get("project_id"):
                raise WikiError(
                    "COMMON_PERMISSION_DENIED",
                    "agent cannot fetch another project_id",
                    403,
                )
            if not as_propose_followup and page_data["status"] != "approved":
                raise WikiError(
                    "COMMON_PRECONDITION_FAILED",
                    "agent may fetch only approved pages",
                    412,
                )
        resolved_version = page_data["version"]
        resolved_status = page_data["status"]
        body = page_data.get("body") or ""
        if version and version != resolved_version:
            rev = self.conn.execute(
                "SELECT * FROM wiki_revision WHERE page_id = ? AND version = ? ORDER BY created_at DESC",
                (page_id, version),
            ).fetchone()
            if not rev:
                raise WikiError(
                    "COMMON_REFERENCE_NOT_FOUND",
                    f"unknown version {version} for {page_id}",
                    404,
                )
            resolved_version = rev["version"]
            resolved_status = rev["status"]
            body = rev["body_snapshot"] or ""
            if not body and page_data["namespace"] == "project":
                safe_id = re.sub(r"[^A-Za-z0-9_-]", "-", str(page_id)).strip("-")[:100] or "page"
                try:
                    from platform_core.storage import get_storage
                    from wiki.project_files import ProjectFileWiki

                    snapshot_key = rev.get("diff_ref") or ProjectFileWiki().key(
                        str(page_data["project_id"]), f"history/pages/{safe_id}/v{resolved_version}.md",
                    )
                    content = get_storage().get(snapshot_key).decode("utf-8")
                    metadata, body = ProjectFileWiki.parse_markdown(content)
                    if (metadata.get("project_id") != page_data["project_id"]
                            or metadata.get("page_id") != page_id
                            or str(metadata.get("version")) != str(resolved_version)):
                        raise ValueError("Project Wiki snapshot metadata does not match its revision")
                    heading = f"# {page_data['title']}"
                    if body.startswith(heading):
                        body = body[len(heading):].lstrip()
                    footer = f"Source Wiki page: `{page_id}`."
                    if body.rstrip().endswith(footer):
                        body = body.rstrip()[:-len(footer)].rstrip()
                except Exception as error:
                    raise WikiError(
                        "COMMON_REFERENCE_NOT_FOUND",
                        f"snapshot for {page_id} version {resolved_version} is unavailable: {error}",
                        404,
                    ) from error
        elif page_data["namespace"] == "project":
            try:
                body = self._read_project_page_body(
                    str(page_data["project_id"]), str(page_id), str(page_data["title"]),
                    version=str(page_data["version"]), status=str(page_data["status"]),
                )
            except Exception as error:
                message = str(error).casefold()
                legacy_fallback = (
                    "not_found" in message or "not found" in message
                    or "basic_memory_mcp_url is not configured" in message
                )
                if not body or not legacy_fallback:
                    raise
        page = WikiPage(**{
            **page_data, "body": body, "version": resolved_version, "status": resolved_status,
        })
        if not as_propose_followup:
            log_audit(
                self.root,
                operation="wiki.fetch_page",
                actor=actor,
                extra={"page_id": page_id, "version": page.version},
            )
        return page

    @staticmethod
    def _read_project_page_body(
        project_id: str, page_id: str, title: str, *, version: str | None = None, status: str | None = None,
    ) -> str:
        from wiki.basic_memory import BasicMemoryClient
        from wiki.project_files import ProjectFileWiki

        files = ProjectFileWiki()
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "-", page_id).strip("-")[:100] or "page"
        try:
            note = BasicMemoryClient().read_note(
                project_id, files.markdown_root(project_id), f"pages/{safe_id}.md",
            )
            content = note.get("content", note.get("markdown", ""))
        except Exception:
            content = files.read(project_id, f"pages/{safe_id}.md").decode("utf-8")
        if not isinstance(content, str) or not content:
            raise ValueError("Basic Memory returned no Markdown content")
        metadata, body = files.parse_markdown(content)
        if (metadata.get("project_id") != project_id or metadata.get("page_id") != page_id
                or (version is not None and str(metadata.get("version")) != version)
                or (status is not None and str(metadata.get("status")) not in {status, "draft" if status == "proposed" else status})):
            raise ValueError("Basic Memory page metadata does not match PostgreSQL metadata")
        heading = f"# {title}"
        if body.startswith(heading):
            body = body[len(heading):].lstrip()
        footer = f"Source Wiki page: `{page_id}`."
        if body.rstrip().endswith(footer):
            body = body.rstrip()[:-len(footer)].rstrip()
        return body

    def approve_page(self, actor: Actor, page_id: str, version: str | None = None) -> WikiPage:
        require_tool(self.root, actor, "wiki.approve_page")
        row = self.conn.execute(
            "SELECT * FROM wiki_page WHERE page_id = ?", (page_id,)
        ).fetchone()
        if not row:
            raise WikiError("COMMON_REFERENCE_NOT_FOUND", f"unknown page_id {page_id}", 404)
        page_data = row_to_dict(row)
        if page_data["namespace"] == "project" and not page_data.get("body"):
            page_data["body"] = self._read_project_page_body(
                str(page_data["project_id"]), str(page_data["page_id"]), str(page_data["title"]),
            )
        page = WikiPage(**page_data)
        require_project_scope(actor, namespace=page.namespace, project_id=page.project_id)
        if version and version != page.version:
            raise WikiError(
                "COMMON_PRECONDITION_FAILED",
                f"version mismatch: current {page.version}",
                409,
            )
        now = _now()
        page.status = "approved"
        page.updated_at = now
        self._upsert_page(page, actor.actor, create_revision=True)
        self._write_project_file(page, actor.actor)
        self.conn.commit()
        log_audit(
            self.root,
            operation="wiki.approve_page",
            actor=actor,
            extra={"page_id": page_id, "version": page.version},
        )
        return page

    def demote_page(self, actor: Actor, page_id: str) -> WikiPage:
        """Human-only: approved → draft so VA no longer sees the page.

        Prefer this over hard delete when resolving V-WIKI conflicts — the
        body stays editable. Agents cannot call this tool.
        """
        require_tool(self.root, actor, "wiki.demote_page")
        row = self.conn.execute(
            "SELECT * FROM wiki_page WHERE page_id = ?", (page_id,)
        ).fetchone()
        if not row:
            raise WikiError("COMMON_REFERENCE_NOT_FOUND", f"unknown page_id {page_id}", 404)
        page_data = row_to_dict(row)
        if page_data["namespace"] == "project" and not page_data.get("body"):
            page_data["body"] = self._read_project_page_body(
                str(page_data["project_id"]), str(page_data["page_id"]), str(page_data["title"]),
            )
        page = WikiPage(**page_data)
        require_project_scope(actor, namespace=page.namespace, project_id=page.project_id)
        if page.status != "approved":
            raise WikiError(
                "COMMON_PRECONDITION_FAILED",
                f"only approved pages can be demoted (current={page.status})",
                409,
            )
        now = _now()
        page.status = "draft"
        page.updated_at = now
        self._upsert_page(page, actor.actor, create_revision=True)
        self._write_project_file(page, actor.actor)
        self.conn.commit()
        log_audit(
            self.root,
            operation="wiki.demote_page",
            actor=actor,
            extra={"page_id": page_id, "version": page.version, "status": "draft"},
        )
        return page

    def _write_project_file(self, page: WikiPage, actor_name: str) -> None:
        """Write project Wiki content to Basic Memory and recovery snapshots to shared storage."""
        if page.namespace != "project" or not page.project_id:
            return
        try:
            from wiki.project_files import ProjectFileWiki

            files = ProjectFileWiki()
            safe_id = re.sub(r"[^A-Za-z0-9_-]", "-", page.page_id).strip("-")[:100] or "page"
            status = page.status if page.status in {"approved", "archived"} else "draft"
            markdown = "\n".join([
                f"# {page.title}", "", page.body.strip(), "",
                f"Source Wiki page: `{page.page_id}`.",
            ])
            metadata = {
                "page_id": page.page_id,
                "bucket_id": page.bucket_id,
                "namespace": page.namespace,
                "source_ids": page.source_ids,
                "updated_by": actor_name,
                "updated_at": page.updated_at,
                "approved_by": actor_name if status == "approved" else None,
                "approved_at": page.updated_at if status == "approved" else None,
            }
            path = f"pages/{safe_id}.md"
            note = files._yaml({
                "title": page.title, "type": page.bucket_id, "project_id": page.project_id,
                "version": page.version, "status": status, **metadata,
            }) + markdown.rstrip() + "\n"
            revision = self.conn.execute(
                "SELECT revision_id FROM wiki_revision WHERE page_id = ? AND version = ? "
                "AND status = ? AND created_at = ? ORDER BY revision_id DESC LIMIT 1",
                (page.page_id, page.version, page.status, page.updated_at),
            ).fetchone()
            revision_id = str(revision["revision_id"]) if revision else "current"
            revision_path = f"history/pages/{safe_id}/revisions/{revision_id}.md"
            files.write_markdown(page.project_id, revision_path, title=page.title,
                                 document_type=page.bucket_id, version=page.version,
                                 status=status, body=markdown, metadata=metadata)
            revision_key = files.key(page.project_id, revision_path)
            if revision:
                self.conn.execute(
                    "UPDATE wiki_revision SET diff_ref = ? WHERE revision_id = ?",
                    (revision_key, revision_id),
                )
            if status == "approved":
                files.write_markdown(page.project_id, f"history/pages/{safe_id}/v{page.version}.md",
                                     title=page.title, document_type=page.bucket_id,
                                     version=page.version, status="approved", body=markdown,
                                     metadata=metadata)
            files.write_basic_memory_note(page.project_id, path, note)
            self._sync_approved_project_indexes(files, page.project_id)
        except Exception:
            logger.exception("Could not sync project Wiki page %s to Markdown", page.page_id)
            raise

    def _sync_approved_project_indexes(self, files: Any, project_id: str) -> None:
        """Publish approved BA knowledge as links in the canonical project Wiki maps."""
        pages = [item for item in self.list_pages(status="approved", project_id=project_id)
                 if item.namespace == "project" and item.project_id == project_id]
        buckets: dict[str, list[dict[str, str]]] = {
            "POLICY.md": [], "RULES.md": [], "GLOSSARY/TERMS.md": [],
            "GLOSSARY/ABBREVIATIONS.md": [], "GLOSSARY/DEFINITIONS.md": [], "PROJECT.md": [],
        }
        for item in pages:
            safe_id = re.sub(r"[^A-Za-z0-9_-]", "-", item.page_id).strip("-")[:100] or "page"
            prefix = "../pages/" if item.bucket_id == "glossary" else "pages/"
            entry = {"title": item.title, "path": f"{prefix}{safe_id}.md",
                     "page_id": item.page_id, "version": str(item.version)}
            if item.bucket_id == "constraint": buckets["POLICY.md"].append(entry)
            elif item.bucket_id in {"business_rule", "convention"}: buckets["RULES.md"].append(entry)
            elif item.bucket_id == "glossary":
                buckets["GLOSSARY/TERMS.md"].append(entry)
                buckets["GLOSSARY/DEFINITIONS.md"].append(entry)
                if item.abbrev or item.expansion:
                    buckets["GLOSSARY/ABBREVIATIONS.md"].append(entry)
            elif item.bucket_id == "org": buckets["GLOSSARY/DEFINITIONS.md"].append(entry)
            elif item.bucket_id == "progress": buckets["PROJECT.md"].append(entry)
        for path, entries in buckets.items():
            labels = {
                "POLICY.md": "Project policies", "RULES.md": "Project business rules",
                "GLOSSARY/TERMS.md": "Project terms", "GLOSSARY/ABBREVIATIONS.md": "Project abbreviations",
                "GLOSSARY/DEFINITIONS.md": "Project definitions", "PROJECT.md": "Project overview and progress",
            }
            files.sync_project_index(project_id, path, entries, title=labels[path])

    def delete_page(self, actor: Actor, page_id: str) -> dict[str, Any]:
        """Human-only hard delete of one page (+ revisions). Agents denied."""
        require_tool(self.root, actor, "wiki.delete_page")
        row = self.conn.execute(
            "SELECT * FROM wiki_page WHERE page_id = ?", (page_id,)
        ).fetchone()
        if not row:
            raise WikiError("COMMON_REFERENCE_NOT_FOUND", f"unknown page_id {page_id}", 404)
        page = row_to_dict(row)
        require_project_scope(actor, namespace=page["namespace"], project_id=page.get("project_id"))
        n = self.delete_pages([page_id])
        log_audit(
            self.root,
            operation="wiki.delete_page",
            actor=actor,
            extra={"page_id": page_id, "deleted": n},
        )
        return {"ok": True, "page_id": page_id, "deleted": n}

    def list_pages(self, *, status: str | None = None, project_id: str | None = None) -> list[WikiPage]:
        sql = "SELECT * FROM wiki_page WHERE 1=1"
        args: list = []
        if status:
            sql += " AND status = ?"
            args.append(status)
        if project_id:
            sql += " AND project_id = ?"
            args.append(project_id)
        sql += " ORDER BY updated_at DESC"
        pages = []
        for row in self.conn.execute(sql, args).fetchall():
            data = row_to_dict(row)
            if data["namespace"] == "project" and not data.get("body") and data.get("project_id"):
                try:
                    data["body"] = self._read_project_page_body(
                        str(data["project_id"]), str(data["page_id"]), str(data["title"]),
                        version=str(data["version"]), status=str(data["status"]),
                    )
                except Exception:
                    logger.exception("Could not read Project Wiki page %s from Basic Memory", data["page_id"])
                    data["body"] = ""
            pages.append(WikiPage(**data))
        return pages

    def delete_pages(self, page_ids: list[str]) -> int:
        if not page_ids:
            return 0
        placeholders = ",".join("?" for _ in page_ids)
        self.conn.execute(
            f"DELETE FROM wiki_revision WHERE page_id IN ({placeholders})",
            page_ids,
        )
        cur = self.conn.execute(
            f"DELETE FROM wiki_page WHERE page_id IN ({placeholders})",
            page_ids,
        )
        self.conn.commit()
        return int(cur.rowcount or 0)

    def _upsert_page(self, page: WikiPage, actor_name: str, *, create_revision: bool) -> str:
        self.conn.execute(
            """
            INSERT INTO wiki_page (
              page_id, namespace, project_id, bucket_id, title, abbrev, expansion,
              disambiguation, status, version, body, source_ids, owner, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(page_id) DO UPDATE SET
              namespace=excluded.namespace,
              project_id=excluded.project_id,
              bucket_id=excluded.bucket_id,
              title=excluded.title,
              abbrev=excluded.abbrev,
              expansion=excluded.expansion,
              disambiguation=excluded.disambiguation,
              status=excluded.status,
              version=excluded.version,
              body=excluded.body,
              source_ids=excluded.source_ids,
              owner=excluded.owner,
              updated_at=excluded.updated_at
            """,
            (
                page.page_id,
                page.namespace,
                page.project_id,
                page.bucket_id,
                page.title,
                page.abbrev,
                page.expansion,
                page.disambiguation,
                page.status,
                page.version,
                page.body if page.namespace != "project" or not os.getenv("BASIC_MEMORY_MCP_URL", "").strip() else None,
                json.dumps(page.source_ids),
                page.owner,
                page.updated_at,
            ),
        )
        if create_revision:
            # Use MAX, not COUNT — deletions leave gaps; COUNT+1 collides with existing REV-*.
            # Platform: serialize id allocation across connections until commit.
            self.conn.execute("SELECT pg_advisory_xact_lock(hashtext('wiki-revision-id'))")
            row = self.conn.execute(
                """
                SELECT MAX(CAST(SUBSTR(revision_id, 5) AS INTEGER)) AS m
                FROM wiki_revision
                WHERE revision_id LIKE 'REV-%'
                """
            ).fetchone()
            n = int(row["m"] or 0) if row is not None else 0
            revision_id = f"REV-{n + 1:04d}"
            self.conn.execute(
                """
                INSERT INTO wiki_revision (
                  revision_id, page_id, version, status, body_snapshot, diff_ref, actor, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    revision_id,
                    page.page_id,
                    page.version,
                    page.status,
                    page.body if page.namespace != "project" or not os.getenv("BASIC_MEMORY_MCP_URL", "").strip() else None,
                    None,
                    actor_name,
                    page.updated_at,
                ),
            )
        return page.page_id


def _slug_project(raw: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "", str(raw or "")).upper()
    return (cleaned[-8:] or "SCOPE")


def _parse_semver(version: str) -> tuple[int, int, int]:
    parts = (version or "1.0.0").split(".")
    nums = [(int(p) if p.isdigit() else 0) for p in parts[:3]]
    while len(nums) < 3:
        nums.append(0)
    return nums[0], nums[1], nums[2]
