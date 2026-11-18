"""Project-scoped Designer Wiki: approved visual baseline and version history."""

import hashlib
import logging
from uuid import UUID

from fastapi import APIRouter, Query, Request, status
from platform_core.fastapi import auth_disabled, require_project
from pydantic import BaseModel, Field

from ..core.errors import not_found
from ..models import RunCreate, RunView
from .access import DESIGNER_ROLE, current_username

router = APIRouter(prefix="/api/v1", tags=["Project Wiki"])
logger = logging.getLogger(__name__)


class RestoreWikiVersion(BaseModel):
    version: int = Field(ge=1)


def _require(project_id: str, role: str | None = None) -> None:
    if not auth_disabled() or project_id:
        require_project(project_id, role)


@router.get("/project-wiki")
async def get_project_wiki(request: Request, project_id: str = Query(..., min_length=1)) -> dict:
    _require(project_id)
    wiki = await request.app.state.store.get_project_design_wiki(project_id)
    if wiki is None:
        return {"project_id": project_id, "current": None, "versions": []}
    return {"project_id": project_id, "current": wiki, "versions": wiki["versions"]}


@router.post("/project-wiki/restore")
async def restore_project_wiki(request: Request, body: RestoreWikiVersion,
                               project_id: str = Query(..., min_length=1)) -> dict:
    _require(project_id, DESIGNER_ROLE)
    try:
        version = await request.app.state.store.restore_project_design_wiki(
            project_id, body.version, approved_by=current_username(),
        )
    except ValueError as error:
        raise not_found("Project Design Wiki version") from error
    wiki = await request.app.state.store.get_project_design_wiki(project_id)
    if wiki is not None:
        try:
            from platform_core import artifacts
            from ..storage.layout import project_design_wiki_key

            current = wiki["current"] if "current" in wiki else wiki
            key = project_design_wiki_key(project_id, version)
            from platform_core.storage import get_storage

            markdown = get_storage().get(key)
            artifacts.register(
                project_id=project_id,
                kind="project_design_wiki",
                source_module="designer",
                source_ref=project_id,
                version=version,
                title=f"Project Design System v{version}",
                storage_key=key,
                content_hash=hashlib.sha256(markdown).hexdigest(),
                meta={
                    "source_session_id": current["source_session_id"],
                    "source_feature_id": current["source_feature_id"],
                    "restored_from_version": body.version,
                },
                created_by=None,
            )
        except Exception:  # noqa: BLE001 - restoring the baseline must not fail because indexing is unavailable.
            logger.exception("Could not publish restored Project Design Wiki artifact for %s", project_id)
    return {"project_id": project_id, "restored_from": body.version, "version": version}


@router.post("/sessions/{session_id}/style-previews", response_model=RunView,
             status_code=status.HTTP_202_ACCEPTED)
async def create_style_previews(request: Request, session_id: UUID) -> RunView:
    """Ask for alternate directions in this feature without changing the project default."""
    session_uuid = session_id
    store = request.app.state.store
    session = await store.get_session(session_uuid)
    if session is None:
        raise not_found("Session")
    _require(session.project_id or "")
    brief = await store.get_design_brief(session_uuid)
    brief.style_candidates = []
    brief.selected_style_key = ""
    brief.style_name = None
    brief.style_direction = ""
    brief.selected_style_contract = {}
    brief.style_preview_shown = False
    await store.save_design_brief(session_uuid, brief)
    platform = (brief.platforms or ["responsive"])[0]
    run = await store.create_run(
        session_uuid,
        RunCreate(
            prompt="Tạo ba hướng phong cách thiết kế khác nhau cho feature hiện tại để tôi chọn trước khi lập UI spec.",
            screen_name="Style direction",
            platform=platform,
        ),
    )
    await store.set_run_data(run.id, run.revision, "project_style_mode", {"mode": "explore"})
    await request.app.state.events.emit(run.id, run.revision, "run.created", {
        "stage": run.stage, "message": "Style preview requested",
    })
    request.app.state.runs.start(run.id)
    return run
