import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Callable
from enum import Enum
from typing import Any, TypeVar
from uuid import UUID, uuid4

from psycopg import Connection, errors
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from pydantic import BaseModel

from ..models import (
    SCREEN_WIDTHS,
    DesignBrief,
    DesignSystemDetail,
    DesignSystemView,
    RunCreate,
    RunStage,
    RunStatus,
    RunView,
    ScreenVersionView,
    ScreenView,
    ScreenWrite,
    SessionView,
    WorkflowEvent,
    utc_now,
)
from . import layout
from .schema import drop_inline_html, has_inline_html, initialize_schema

logger = logging.getLogger("designer_agent.store")


def _storage():
    from platform_core.storage import get_storage

    return get_storage()


def _delete_prefix(prefix: str) -> int:
    storage, count = _storage(), 0
    for key in list(storage.keys(prefix)):
        storage.delete(key)
        count += 1
    return count

T = TypeVar("T")


Row = dict[str, Any]


def _json(value: BaseModel | dict[str, Any]) -> str:
    return value.model_dump_json(by_alias=True) if isinstance(value, BaseModel) else json.dumps(value, ensure_ascii=False)


def _param(value: Any) -> Any:
    # StrEnum members (RunStage/RunStatus) are stored by value, as SQLite did.
    return value.value if isinstance(value, Enum) else value


class Store:
    """Designer persistence: metadata in Postgres (schema ``designer``), file content in platform
    storage under the keys of ``storage.layout`` (screen HTML is one immutable object per version).
    Every connection pins ``search_path`` to ``schema``.
    """

    def __init__(self, dsn: str, schema: str = "designer", max_size: int = 10) -> None:
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", schema):
            raise ValueError(f"invalid schema name {schema!r}")
        self.dsn = dsn
        self.schema = schema
        self._event_lock = asyncio.Lock()
        self._pool = ConnectionPool(
            dsn,
            min_size=1,
            max_size=max_size,
            kwargs={"row_factory": dict_row},
            configure=self._configure,
            open=False,
        )

    def _configure(self, conn: Connection) -> None:
        conn.execute(f'SET search_path TO "{self.schema}"')
        conn.commit()

    async def initialize(self) -> None:
        await self._call(self._initialize)

    def _initialize(self) -> None:
        with Connection.connect(self.dsn, autocommit=True) as bootstrap:
            bootstrap.execute(f'CREATE SCHEMA IF NOT EXISTS "{self.schema}"')
        self._pool.open(wait=True)
        with self._pool.connection() as db:
            initialize_schema(db)
            if has_inline_html(db):
                self._migrate_screen_html(db)
                drop_inline_html(db)

    def _migrate_screen_html(self, db: Connection) -> None:
        """Move screen HTML stored inline by older versions into storage (one object per version)."""
        rows = db.execute(
            "SELECT v.session_id, v.screen_id, v.version, v.html, s.project_id FROM screen_versions v "
            "JOIN sessions s ON s.id = v.session_id WHERE v.storage_key IS NULL AND v.html IS NOT NULL"
        ).fetchall()
        for row in rows:
            key, digest, size = self._put_html(row["project_id"], row["session_id"], row["screen_id"], row["version"], row["html"])
            db.execute(
                "UPDATE screen_versions SET storage_key=%s, content_hash=%s, size=%s "
                "WHERE session_id=%s AND screen_id=%s AND version=%s",
                (key, digest, size, row["session_id"], row["screen_id"], row["version"]),
            )
        db.execute(
            "UPDATE screens c SET storage_key=v.storage_key, content_hash=v.content_hash, size=v.size "
            "FROM screen_versions v WHERE v.session_id=c.session_id AND v.screen_id=c.screen_id "
            "AND v.version=c.version AND c.storage_key IS NULL"
        )
        left = db.execute("SELECT count(*) AS n FROM screen_versions WHERE storage_key IS NULL").fetchone()["n"]
        if left:
            raise RuntimeError(f"{left} screen version(s) could not be moved to storage")
        if rows:
            logger.info("Moved %d screen version(s) from the database to storage", len(rows))

    @staticmethod
    def _put_html(project_id, session_id, screen_id: str, version: int, html: str) -> tuple[str, str, int]:
        data = html.encode("utf-8")
        key = layout.screen_version_key(project_id, str(session_id), screen_id, version)
        _storage().put(key, data, content_type="text/html; charset=utf-8")
        return key, hashlib.sha256(data).hexdigest(), len(data)

    async def close(self) -> None:
        await self._call(self._pool.close)

    async def create_session(
        self,
        title: str,
        *,
        project_id: str | None = None,
        created_by: str | None = None,
        source: str | None = None,
        source_ref: str | None = None,
        source_artifact_id: str | None = None,
    ) -> SessionView:
        now = utc_now()
        session = SessionView(
            id=uuid4(),
            title=title,
            created_at=now,
            updated_at=now,
            project_id=project_id,
            created_by=created_by,
            source=source,
            source_ref=source_ref,
            source_artifact_id=source_artifact_id,
        )
        await self._execute(
            "INSERT INTO sessions (id, title, created_at, updated_at, design_brief, "
            "project_id, created_by, source, source_ref, source_artifact_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(session.id), session.title, now.isoformat(), now.isoformat(), None,
                project_id, created_by, source, source_ref, source_artifact_id,
            ),
        )
        return session

    async def list_sessions(self, project_id: str | None = None) -> list[SessionView]:
        if project_id:
            rows = await self._fetchall(
                "SELECT * FROM sessions WHERE project_id = ? ORDER BY updated_at DESC", (project_id,)
            )
        else:
            rows = await self._fetchall("SELECT * FROM sessions ORDER BY updated_at DESC")
        return [self._session_from_row(row) for row in rows]

    # ── Platform access helpers ─────────────────────────
    async def session_project(self, session_id: UUID | str) -> tuple[bool, str | None]:
        row = await self._fetchone("SELECT project_id FROM sessions WHERE id = ?", (str(session_id),))
        return (row is not None, row["project_id"] if row else None)

    async def run_project(self, run_id: UUID | str) -> tuple[bool, str | None]:
        row = await self._fetchone(
            "SELECT s.project_id FROM runs r JOIN sessions s ON s.id = r.session_id WHERE r.id = ?",
            (str(run_id),),
        )
        return (row is not None, row["project_id"] if row else None)

    async def get_session(self, session_id: UUID) -> SessionView | None:
        row = await self._fetchone("SELECT * FROM sessions WHERE id = ?", (str(session_id),))
        return self._session_from_row(row) if row else None

    async def delete_session(self, session_id: UUID) -> bool:
        """Delete a session (runs, events, reviews, run data, screens via FK cascades) and its files."""
        return await self._call(lambda: self._delete_session(session_id))

    def _delete_session(self, session_id: UUID) -> bool:
        with self._pool.connection() as db:
            row = db.execute(self._sql("SELECT project_id FROM sessions WHERE id = ?"), (str(session_id),)).fetchone()
            if row is None:
                return False
            db.execute(self._sql("DELETE FROM sessions WHERE id = ?"), (str(session_id),))
        # Files after the rows: a failure here leaves unreferenced objects, never dangling rows.
        try:
            _delete_prefix(layout.session_prefix(row["project_id"], str(session_id)))
        except Exception:  # noqa: BLE001
            logger.exception("Could not delete the files of session %s", session_id)
        return True

    async def get_design_brief(self, session_id: UUID) -> DesignBrief:
        session = await self.get_session(session_id)
        return session.design_brief if session and session.design_brief else DesignBrief()

    async def save_design_brief(self, session_id: UUID, brief: DesignBrief) -> None:
        await self._execute(
            "UPDATE sessions SET design_brief=?, updated_at=? WHERE id=?",
            (brief.model_dump_json(), utc_now().isoformat(), str(session_id)),
        )

    async def create_run(
        self, session_id: UUID, request: RunCreate, *, triggered_by: str | None = None
    ) -> RunView:
        now = utc_now()
        run_id = uuid4()
        run = RunView(
            id=run_id,
            session_id=session_id,
            revision=1,
            prompt=request.prompt,
            screen_name=request.screen_name,
            platform=request.platform,
            style_name=request.style_name,
            stage=RunStage.REQUIREMENT,
            status=RunStatus.QUEUED,
            library_ids=request.library_ids,
            created_at=now,
            updated_at=now,
        )
        await self._execute(
            "INSERT INTO runs (id, session_id, revision, prompt, screen_name, platform, "
            "stage, status, library_ids, specification, error, intent, assistant_message, style_name, created_at, updated_at, "
            "triggered_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(run.id),
                str(run.session_id),
                run.revision,
                run.prompt,
                run.screen_name,
                run.platform,
                run.stage,
                run.status,
                json.dumps(run.library_ids),
                None,
                None,
                None,
                None,
                run.style_name,
                now.isoformat(),
                now.isoformat(),
                triggered_by,
            ),
        )
        if request.assets:
            await self.set_run_data(
                run.id,
                run.revision,
                "exact_assets",
                [item.model_dump(mode="json") for item in request.assets],
            )
        return run

    async def list_runs(self, session_id: UUID) -> list[RunView]:
        rows = await self._fetchall(
            "SELECT * FROM runs WHERE session_id=? ORDER BY created_at DESC", (str(session_id),)
        )
        return [self._run_from_row(row) for row in rows]

    async def get_run(self, run_id: UUID) -> RunView | None:
        row = await self._fetchone("SELECT * FROM runs WHERE id = ?", (str(run_id),))
        return self._run_from_row(row) if row else None

    async def interrupt_unfinished_runs(self) -> int:
        """Runs live in process memory: after a restart, mark them failed (retryable) instead of "running" forever."""
        rows = await self._fetchall("SELECT id, stage FROM runs WHERE status IN ('running', 'queued')")
        for row in rows:
            await self.update_run(
                UUID(str(row["id"])),
                stage=RunStage.FAILED,
                status=RunStatus.FAILED,
                error={
                    "code": "RUN_INTERRUPTED",
                    "title": "The run was interrupted by a service restart",
                    "detail": f"It stopped during {row['stage']}.",
                    "retryable": True,
                    "action": "Retry the run.",
                    "stage": row["stage"],
                },
            )
        return len(rows)

    async def update_run(
        self,
        run_id: UUID,
        *,
        stage: RunStage | None = None,
        status: RunStatus | None = None,
        specification: BaseModel | dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        intent: str | None = None,
        assistant_message: str | None = None,
        screen_name: str | None = None,
        platform: str | None = None,
        style_name: str | None = None,
    ) -> RunView:
        now = utc_now().isoformat()
        await self._execute(
            "UPDATE runs SET stage=COALESCE(?, stage), status=COALESCE(?, status), specification=COALESCE(?, specification), "
            "error=?, intent=COALESCE(?, intent), assistant_message=COALESCE(?, assistant_message), "
            "screen_name=COALESCE(?, screen_name), platform=COALESCE(?, platform), style_name=COALESCE(?, style_name), "
            "updated_at=? WHERE id=?",
            (
                stage,
                status,
                _json(specification) if specification is not None else None,
                json.dumps(error) if error else None,
                intent,
                assistant_message,
                screen_name,
                platform,
                style_name,
                now,
                str(run_id),
            ),
        )
        run = await self.get_run(run_id)
        if run is None:
            raise LookupError(str(run_id))
        return run

    async def bump_revision(self, run_id: UUID) -> RunView:
        await self._execute(
            "UPDATE runs SET revision=revision+1, stage=?, status=?, error=NULL, updated_at=? WHERE id=?",
            (RunStage.SPECIFICATION, RunStatus.QUEUED, utc_now().isoformat(), str(run_id)),
        )
        run = await self.get_run(run_id)
        if run is None:
            raise LookupError(str(run_id))
        return run

    async def add_event(self, run_id: UUID, revision: int, event_type: str, payload: dict[str, Any]) -> WorkflowEvent:
        async with self._event_lock:
            for attempt in range(5):
                try:
                    row = await self._fetchone(
                        "SELECT COALESCE(MAX(sequence), 0) AS value FROM events WHERE run_id = ?",
                        (str(run_id),),
                    )
                    if row is None:
                        raise RuntimeError("Event sequence query returned no row")
                    event = WorkflowEvent(
                        run_id=run_id,
                        revision=revision,
                        sequence=int(row["value"]) + 1,
                        type=event_type,
                        payload=payload,
                    )
                    await self._execute(
                        "INSERT INTO events (id, run_id, revision, sequence, event_type, payload, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            str(event.id),
                            str(run_id),
                            revision,
                            event.sequence,
                            event.type,
                            json.dumps(payload),
                            event.time.isoformat(),
                        ),
                    )
                    return event
                except errors.UniqueViolation:
                    if attempt < 4:
                        await asyncio.sleep(0.05 * (attempt + 1))
                        continue
                    raise
            raise RuntimeError("Failed to allocate event sequence after retries")

    async def list_events(self, run_id: UUID, after: int = 0) -> list[WorkflowEvent]:
        rows = await self._fetchall(
            "SELECT * FROM events WHERE run_id=? AND sequence>? ORDER BY sequence",
            (str(run_id), after),
        )
        return [
            WorkflowEvent(
                id=row["id"],
                run_id=row["run_id"],
                revision=row["revision"],
                sequence=row["sequence"],
                type=row["event_type"],
                time=row["created_at"],
                payload=json.loads(row["payload"]),
            )
            for row in rows
        ]

    async def add_review(self, run_id: UUID, revision: int, checkpoint: str, decision: str, feedback: str) -> None:
        await self._execute(
            "INSERT INTO reviews (id, run_id, revision, checkpoint, decision, feedback, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid4()),
                str(run_id),
                revision,
                checkpoint,
                decision,
                feedback,
                utc_now().isoformat(),
            ),
        )

    # ── Run data: per-run JSON values (token usage, attached assets …) ──
    async def set_run_data(self, run_id: UUID, revision: int, key: str, value: dict[str, Any] | list[Any]) -> None:
        await self._execute(
            "INSERT INTO run_data (run_id, revision, artifact_key, value, created_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(run_id, revision, artifact_key) DO UPDATE SET value=excluded.value",
            (str(run_id), revision, key, json.dumps(value), utc_now().isoformat()),
        )

    async def get_run_data(self, run_id: UUID, revision: int, key: str) -> Any | None:
        row = await self._fetchone(
            "SELECT value FROM run_data WHERE run_id=? AND revision=? AND artifact_key=?",
            (str(run_id), revision, key),
        )
        return json.loads(row["value"]) if row else None

    async def get_latest_session_run_data(self, session_id: UUID, key: str) -> Any | None:
        """Return the newest value of a kind from any run in the session.

        Clarification turns create a new run, while uploaded assets belong to
        the original message. Session fallback keeps those exact bytes attached
        when the user later answers a question such as "mobile nhé".
        """
        row = await self._fetchone(
            "SELECT a.value FROM run_data a "
            "JOIN runs r ON r.id=a.run_id "
            "WHERE r.session_id=? AND a.artifact_key=? "
            "ORDER BY a.created_at DESC LIMIT 1",
            (str(session_id), key),
        )
        return json.loads(row["value"]) if row else None

    async def list_run_data(self, run_id: UUID, revision: int) -> dict[str, Any]:
        rows = await self._fetchall(
            "SELECT artifact_key, value FROM run_data WHERE run_id=? AND revision=?",
            (str(run_id), revision),
        )
        return {row["artifact_key"]: json.loads(row["value"]) for row in rows}

    # ── Screens: metadata here, HTML in storage (layout.screen_version_key) ──
    async def list_screens(self, session_id: UUID, *, with_html: bool = False) -> list[ScreenView]:
        rows = await self._fetchall(
            "SELECT * FROM screens WHERE session_id=? ORDER BY position, created_at", (str(session_id),)
        )
        screens = [self._screen_from_row(row) for row in rows]
        if with_html:
            htmls = await asyncio.gather(*(self._read_html(row["storage_key"]) for row in rows))
            for screen, html in zip(screens, htmls):
                screen.html = html
        return screens

    async def get_screen(self, session_id: UUID, screen_id: str, version: int | None = None) -> ScreenView | None:
        row = await self._fetchone(
            "SELECT * FROM screens WHERE session_id=? AND screen_id=?", (str(session_id), screen_id)
        )
        if row is None:
            return None
        if version is not None and version != row["version"]:
            old = await self._fetchone(
                "SELECT storage_key FROM screen_versions WHERE session_id=? AND screen_id=? AND version=?",
                (str(session_id), screen_id, version),
            )
            if old is None:
                return None
            row = {**row, "version": version, "storage_key": old["storage_key"]}
        screen = self._screen_from_row(row)
        screen.html = await self._read_html(row["storage_key"])
        return screen

    async def _read_html(self, key: str | None) -> str:
        if not key:
            return ""
        return (await asyncio.to_thread(lambda: _storage().get(key))).decode("utf-8")

    async def save_screen(
        self,
        session_id: UUID,
        screen_id: str,
        body: ScreenWrite,
        *,
        run_id: UUID | str | None = None,
        updated_by: str | None = None,
    ) -> ScreenView:
        """Create or update a screen. Every save writes a new immutable HTML object and a version row."""
        return await self._call(lambda: self._save_screen(session_id, screen_id, body, run_id, updated_by))

    def _save_screen(self, session_id, screen_id: str, body: ScreenWrite, run_id, updated_by) -> ScreenView:
        now = utc_now().isoformat()
        sid, rid = str(session_id), (str(run_id) if run_id else None)
        width = body.width or SCREEN_WIDTHS[body.platform]
        with self._pool.connection() as db, db.transaction():
            session = db.execute(self._sql("SELECT project_id FROM sessions WHERE id=?"), (sid,)).fetchone()
            if session is None:
                raise LookupError(sid)
            current = db.execute(
                self._sql("SELECT version FROM screens WHERE session_id=? AND screen_id=? FOR UPDATE"), (sid, screen_id)
            ).fetchone()
            version = current["version"] + 1 if current else 1
            # The object is written before the rows commit; a failed commit leaves an object no row
            # points to (rewritten by the next save of that version), never a row without its HTML.
            key, digest, size = self._put_html(session["project_id"], sid, screen_id, version, body.html)
            if current is None:
                position = db.execute(
                    self._sql("SELECT COALESCE(MAX(position), -1) + 1 AS p FROM screens WHERE session_id=?"), (sid,)
                ).fetchone()["p"]
                db.execute(
                    self._sql(
                        "INSERT INTO screens (session_id, screen_id, name, platform, width, position, version, "
                        "storage_key, content_hash, size, run_id, updated_by, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                    ),
                    (sid, screen_id, body.name, body.platform, width, position, version, key, digest, size,
                     rid, updated_by, now, now),
                )
            else:
                db.execute(
                    self._sql(
                        "UPDATE screens SET name=?, platform=?, width=?, version=?, storage_key=?, content_hash=?, size=?, "
                        "run_id=?, updated_by=?, updated_at=? WHERE session_id=? AND screen_id=?"
                    ),
                    (body.name, body.platform, width, version, key, digest, size, rid, updated_by, now, sid, screen_id),
                )
            db.execute(
                self._sql(
                    "INSERT INTO screen_versions (session_id, screen_id, version, storage_key, content_hash, size, "
                    "run_id, updated_by, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                ),
                (sid, screen_id, version, key, digest, size, rid, updated_by, body.note or None, now),
            )
            row = db.execute(
                self._sql("SELECT * FROM screens WHERE session_id=? AND screen_id=?"), (sid, screen_id)
            ).fetchone()
        screen = self._screen_from_row(row)
        screen.html = body.html
        return screen

    async def list_screen_versions(self, session_id: UUID, screen_id: str) -> list[ScreenVersionView]:
        rows = await self._fetchall(
            "SELECT version, created_at, updated_by, run_id, note FROM screen_versions "
            "WHERE session_id=? AND screen_id=? ORDER BY version DESC",
            (str(session_id), screen_id),
        )
        return [ScreenVersionView.model_validate(dict(row)) for row in rows]

    async def delete_screen(self, session_id: UUID, screen_id: str) -> bool:
        def delete() -> bool:
            with self._pool.connection() as db, db.transaction():
                session = db.execute(self._sql("SELECT project_id FROM sessions WHERE id=?"), (str(session_id),)).fetchone()
                db.execute(
                    self._sql("DELETE FROM screen_versions WHERE session_id=? AND screen_id=?"),
                    (str(session_id), screen_id),
                )
                cursor = db.execute(
                    self._sql("DELETE FROM screens WHERE session_id=? AND screen_id=?"), (str(session_id), screen_id)
                )
                deleted = cursor.rowcount > 0
            if deleted and session is not None:
                prefix = layout.session_prefix(session["project_id"], str(session_id)) + f"screens/{screen_id}/"
                try:
                    _delete_prefix(prefix)
                except Exception:  # noqa: BLE001
                    logger.exception("Could not delete the files of screen %s", screen_id)
            return deleted

        return await self._call(delete)

    # ── Design systems ──────────────────────────────────
    async def create_design_system(
        self, ds_id: str, *, name: str, project_id: str | None, source_name: str | None,
        source_key: str | None, created_by: str | None,
    ) -> None:
        now = utc_now().isoformat()
        await self._execute(
            "INSERT INTO design_systems (id, project_id, name, source_name, source_key, status, created_by, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'importing', ?, ?, ?)",
            (ds_id, project_id, name, source_name, source_key, created_by, now, now),
        )

    async def finish_design_system(
        self, ds_id: str, *, theme_css: str, fonts: list[str], index: dict[str, Any], snippets: list[tuple[str, str, str]]
    ) -> None:
        """``snippets``: (variant_id, component_id, html)."""

        def finish() -> None:
            with self._pool.connection() as db, db.transaction():
                db.execute(self._sql("DELETE FROM design_system_snippets WHERE design_system_id=?"), (ds_id,))
                with db.cursor() as cur:
                    cur.executemany(
                        self._sql(
                            "INSERT INTO design_system_snippets (design_system_id, variant_id, component_id, html) "
                            "VALUES (?, ?, ?, ?)"
                        ),
                        [(ds_id, v, c, h) for v, c, h in snippets],
                    )
                db.execute(
                    self._sql(
                        "UPDATE design_systems SET status='ready', error=NULL, theme_css=?, fonts=?, index_json=?, "
                        "updated_at=? WHERE id=?"
                    ),
                    (theme_css, json.dumps(fonts), json.dumps(index, ensure_ascii=False), utc_now().isoformat(), ds_id),
                )

        await self._call(finish)

    async def fail_design_system(self, ds_id: str, error: str) -> None:
        await self._execute(
            "UPDATE design_systems SET status='failed', error=?, updated_at=? WHERE id=?",
            (error[:2000], utc_now().isoformat(), ds_id),
        )

    async def list_design_systems(self, project_id: str | None) -> list[DesignSystemView]:
        rows = await self._fetchall(
            "SELECT id, project_id, name, source_name, status, error, created_by, created_at, "
            "CASE WHEN index_json IS NULL THEN NULL ELSE index_json::json->'tokens' END AS tokens, "
            "CASE WHEN index_json IS NULL THEN 0 ELSE json_array_length(index_json::json->'components') END AS components "
            "FROM design_systems WHERE project_id IS NULL OR project_id = ? ORDER BY created_at DESC",
            (project_id,),
        )
        out = []
        for row in rows:
            value = dict(row)
            value["summary"] = {"tokens": value.pop("tokens") or {}, "components": value.pop("components") or 0}
            out.append(DesignSystemView.model_validate(value))
        return out

    async def get_design_system(self, ds_id: str) -> DesignSystemDetail | None:
        row = await self._fetchone("SELECT * FROM design_systems WHERE id=?", (ds_id,))
        if row is None:
            return None
        value = dict(row)
        index = json.loads(value.pop("index_json") or "{}")
        value["fonts"] = json.loads(value["fonts"] or "[]")
        value["theme_css"] = value["theme_css"] or ""
        value["components"] = index.get("components", [])
        value["design_system"] = index.get("design_system", {})
        value["summary"] = {"tokens": index.get("tokens", {}), "components": len(value["components"])}
        return DesignSystemDetail.model_validate(value)

    async def get_design_system_source(self, ds_id: str) -> tuple[str | None, bytes | None]:
        row = await self._fetchone("SELECT source_name, source_key FROM design_systems WHERE id=?", (ds_id,))
        if row is None or not row.get("source_key"):
            return (row.get("source_name") if row else None), None
        from platform_core.storage import get_storage

        return row.get("source_name"), get_storage().get(str(row["source_key"]))

    async def get_snippets(self, ds_id: str, variant_ids: list[str]) -> dict[str, str]:
        if not variant_ids:
            return {}
        rows = await self._fetchall(
            "SELECT variant_id, html FROM design_system_snippets WHERE design_system_id=? AND variant_id = ANY(?)",
            (ds_id, list(variant_ids)),
        )
        return {row["variant_id"]: row["html"] for row in rows}

    async def delete_design_system(self, ds_id: str) -> bool:
        def delete() -> bool:
            with self._pool.connection() as db, db.transaction():
                row = db.execute(self._sql("SELECT project_id FROM design_systems WHERE id=?"), (ds_id,)).fetchone()
                db.execute(self._sql("UPDATE sessions SET design_system_id=NULL WHERE design_system_id=?"), (ds_id,))
                deleted = db.execute(self._sql("DELETE FROM design_systems WHERE id=?"), (ds_id,)).rowcount > 0
            if deleted and row is not None:
                try:
                    _delete_prefix(layout.design_system_prefix(row["project_id"], ds_id))
                except Exception:  # noqa: BLE001
                    logger.exception("Could not delete the files of design system %s", ds_id)
            return deleted

        return await self._call(delete)

    async def set_session_visual_theme(self, session_id: UUID, theme: dict[str, Any] | None) -> None:
        await self._execute(
            "UPDATE sessions SET visual_theme=?, updated_at=? WHERE id=?",
            (json.dumps(theme) if theme else None, utc_now().isoformat(), str(session_id)),
        )

    async def get_project_design_wiki(self, project_id: str) -> dict[str, Any] | None:
        row = await self._fetchone(
            "SELECT version, baseline_json, source_session_id, source_feature_id, updated_at "
            "FROM project_design_wiki WHERE project_id=?", (project_id,),
        )
        if row is None:
            return None
        current_baseline: dict[str, Any] | None = None
        from wiki.project_files import ProjectFileWiki

        files = ProjectFileWiki()
        try:
            from wiki.basic_memory import BasicMemoryClient

            note = await asyncio.to_thread(
                BasicMemoryClient().read_note,
                project_id,
                files.markdown_root(project_id),
                "design-system.md",
            )
            content = note.get("content", note.get("markdown", ""))
            if not isinstance(content, str) or not content:
                raise ValueError("Basic Memory bm_read returned no Markdown content")
            metadata, _ = files.parse_markdown(content)
            if str(metadata.get("version")) != str(row["version"]):
                raise ValueError(
                    f"Basic Memory Project Wiki version {metadata.get('version')} "
                    f"does not match metadata version {row['version']}"
                )
            candidate = metadata.get("baseline")
            if isinstance(candidate, dict):
                current_baseline = candidate
        except Exception as error:
            # Rows written before Basic Memory became canonical still live in the
            # shared Markdown/JSON files. Only use these when bm_read says the note
            # is absent; network, checksum, or parse errors should remain visible.
            message = str(error).casefold()
            legacy_note_missing = "not_found" in message or "not found" in message
            if not legacy_note_missing:
                logger.exception("Could not read canonical Basic Memory Design Wiki for %s", project_id)
                raise
            if files.exists(project_id, "design-system.md"):
                content = files.read(project_id, "design-system.md").decode("utf-8")
                metadata, _ = files.parse_markdown(content)
                candidate = metadata.get("baseline")
                if isinstance(candidate, dict):
                    current_baseline = candidate
            if current_baseline is None and files.exists(project_id, "design-system/design-system.json"):
                candidate = json.loads(files.read(project_id, "design-system/design-system.json").decode("utf-8"))
                current_baseline = candidate if isinstance(candidate, dict) else None
        if current_baseline is None and row["baseline_json"]:
            current_baseline = json.loads(row["baseline_json"])
        if current_baseline is None:
            raise ValueError(f"Project Design Wiki note for {project_id} is missing its baseline")
        versions = await self._fetchall(
            "SELECT version, baseline_json, source_session_id, source_feature_id, updated_at, markdown_key "
            "FROM project_design_wiki_versions WHERE project_id=? ORDER BY version DESC",
            (project_id,),
        )
        from platform_core.storage import get_storage
        from wiki.project_files import ProjectFileWiki

        files = ProjectFileWiki()
        storage = get_storage()
        version_items = []
        for item in versions:
            version_baseline = None
            try:
                snapshot = storage.get(item["markdown_key"]).decode("utf-8")
                metadata, _ = files.parse_markdown(snapshot)
                candidate = metadata.get("baseline")
                if isinstance(candidate, dict):
                    version_baseline = candidate
            except Exception:
                logger.exception("Could not read Project Wiki snapshot v%s for %s", item["version"], project_id)
            if version_baseline is None and item["baseline_json"]:
                version_baseline = json.loads(item["baseline_json"])
            version_items.append({
                "version": item["version"],
                "source_session_id": item["source_session_id"],
                "source_feature_id": item["source_feature_id"],
                "updated_at": item["updated_at"],
                "baseline": version_baseline or {},
            })
        return {
            "version": row["version"],
            "baseline": current_baseline,
            "source_session_id": row["source_session_id"],
            "source_feature_id": row["source_feature_id"],
            "updated_at": row["updated_at"],
            "versions": version_items,
        }

    async def approve_project_design_baseline(
        self, project_id: str, baseline: dict[str, Any], markdown: str, session_id: UUID,
        source_feature_id: str | None = None, approved_by: str | None = None,
        approved_at: str | None = None, expected_version: int | None = None,
    ) -> int:
        """Promote the approved visual contract to the project's reusable Wiki baseline."""
        now = approved_at or utc_now().isoformat()
        def save() -> int:
            with self._pool.connection() as db:
                row = db.execute(self._sql(
                    "SELECT version FROM project_design_wiki WHERE project_id=? FOR UPDATE"
                ), (project_id,)).fetchone()
                version = int(row["version"]) + 1 if row else 1
                if expected_version is not None and version != expected_version:
                    raise ValueError(
                        f"Project Wiki changed after preview (expected v{expected_version}, next is v{version})"
                    )
                from platform_core.storage import get_storage
                from .layout import project_design_wiki_key
                from wiki.basic_memory import BasicMemoryClient
                from wiki.project_files import ProjectFileWiki

                storage = get_storage()
                files = ProjectFileWiki(storage)
                markdown_key = project_design_wiki_key(project_id, version)
                project_payload = {
                    **baseline,
                    "schema_version": "1.0",
                    "project_id": project_id,
                    "version": version,
                    "status": "approved",
                    "approved_by": approved_by or "human-review",
                    "approved_at": now,
                    "source_session_id": str(session_id),
                    "source_feature_id": source_feature_id,
                }
                note_metadata = {
                    "title": "Project Design System",
                    "type": "design_system",
                    "project_id": project_id,
                    "version": str(version),
                    "status": "approved",
                    "baseline": baseline,
                    "source_session_id": str(session_id),
                    "source_feature_id": source_feature_id,
                    "approved_by": project_payload["approved_by"],
                    "approved_at": now,
                }
                note = files._yaml(note_metadata) + markdown.rstrip() + "\n"
                snapshot_bytes = note.encode("utf-8")
                storage.put(markdown_key, snapshot_bytes, content_type="text/markdown; charset=utf-8")

                # Basic Memory owns the current Markdown note. Platform storage
                # keeps an S3-backed copy when it is the configured storage backend.
                memory = BasicMemoryClient()
                root = files.markdown_root(project_id)
                memory.ensure_project(project_id, root)
                note_path = "design-system.md"
                note_key = files.key(project_id, note_path)
                current_checksum = None
                try:
                    current = memory.read_note(project_id, root, note_path)
                    current_checksum = current.get("checksum")
                    if not current_checksum:
                        raise RuntimeError("Basic Memory bm_read did not return a note checksum")
                except RuntimeError as error:
                    if "not_found" not in str(error).casefold() and "not found" not in str(error).casefold():
                        raise
                memory.write_note(
                    project_id, root, note_path, note,
                    expected_checksum=current_checksum,
                )
                storage.put(note_key, note.encode("utf-8"), content_type="text/markdown; charset=utf-8")

                db.execute(self._sql(
                    "INSERT INTO project_design_wiki_versions "
                    "(project_id, version, baseline_json, markdown_key, source_session_id, source_feature_id, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)"
                ), (project_id, version, None, markdown_key, str(session_id), source_feature_id, now))
                db.execute(self._sql(
                    "INSERT INTO project_design_wiki "
                    "(project_id, version, baseline_json, markdown_key, source_session_id, source_feature_id, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(project_id) DO UPDATE SET "
                    "version=EXCLUDED.version, baseline_json=NULL, "
                    "markdown_key=EXCLUDED.markdown_key, "
                    "source_session_id=EXCLUDED.source_session_id, source_feature_id=EXCLUDED.source_feature_id, "
                    "updated_at=EXCLUDED.updated_at"
                ), (project_id, version, None, markdown_key,
                    str(session_id), source_feature_id, now))
                return version
        return await self._call(save)

    async def restore_project_design_wiki(
        self, project_id: str, version: int, *, approved_by: str | None = None,
    ) -> int:
        row = await self._fetchone(
            "SELECT baseline_json, markdown_key, source_session_id, source_feature_id "
            "FROM project_design_wiki_versions WHERE project_id=? AND version=?",
            (project_id, version),
        )
        if row is None:
            raise ValueError(f"Project Design Wiki version {version} was not found")
        from platform_core.storage import get_storage
        from wiki.project_files import ProjectFileWiki

        content = get_storage().get(row["markdown_key"]).decode("utf-8")
        metadata, markdown = ProjectFileWiki.parse_markdown(content)
        baseline = metadata.get("baseline")
        if not isinstance(baseline, dict):
            if not row["baseline_json"]:
                raise ValueError(f"Project Design Wiki baseline for version {version} was not found")
            baseline = json.loads(row["baseline_json"])
        if not markdown.strip():
            raise ValueError(f"Project Design Wiki Markdown for version {version} was empty")
        return await self.approve_project_design_baseline(
            project_id, baseline, markdown,
            UUID(row["source_session_id"]), row["source_feature_id"], approved_by=approved_by or "restore",
        )

    async def set_session_design_system(self, session_id: UUID, ds_id: str | None) -> None:
        await self._execute(
            "UPDATE sessions SET design_system_id=?, updated_at=? WHERE id=?",
            (ds_id, utc_now().isoformat(), str(session_id)),
        )

    @staticmethod
    def _screen_from_row(row: Row) -> ScreenView:
        value = {k: v for k, v in row.items() if k not in {"storage_key", "content_hash", "size", "html"}}
        value["id"] = value.pop("screen_id")
        return ScreenView.model_validate(value)

    def _run_from_row(self, row: Row) -> RunView:
        value = dict(row)
        value["library_ids"] = json.loads(value["library_ids"])
        value["specification"] = json.loads(value["specification"]) if value["specification"] else None
        value["error"] = json.loads(value["error"]) if value["error"] else None
        return RunView.model_validate(value)

    @staticmethod
    def _session_from_row(row: Row) -> SessionView:
        value = dict(row)
        value["design_brief"] = json.loads(value["design_brief"]) if value.get("design_brief") else None
        value["visual_theme"] = json.loads(value["visual_theme"]) if value.get("visual_theme") else None
        return SessionView.model_validate(value)

    @staticmethod
    def _sql(sql: str) -> str:
        # Queries keep SQLite's "?" placeholders; psycopg uses "%s".
        return sql.replace("%", "%%").replace("?", "%s")

    async def _execute(self, sql: str, params: tuple[Any, ...]) -> None:
        def execute() -> None:
            with self._pool.connection() as db:
                db.execute(self._sql(sql), tuple(_param(p) for p in params))

        await self._call(execute)

    async def _fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> Row | None:
        def fetch() -> Row | None:
            with self._pool.connection() as db:
                return db.execute(self._sql(sql), tuple(_param(p) for p in params)).fetchone()

        return await self._call(fetch)

    async def _fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[Row]:
        def fetch() -> list[Row]:
            with self._pool.connection() as db:
                return db.execute(self._sql(sql), tuple(_param(p) for p in params)).fetchall()

        return await self._call(fetch)

    async def _call(self, function: Callable[[], T]) -> T:
        return await asyncio.to_thread(function)
