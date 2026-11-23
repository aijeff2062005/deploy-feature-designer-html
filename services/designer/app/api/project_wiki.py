"""Project-scoped Designer Wiki: approved visual baseline and version history."""

import asyncio
import base64
import hashlib
import json
import logging
import re
from uuid import UUID

from fastapi import APIRouter, Query, Request, status
from platform_core.fastapi import auth_disabled, require_project
from pydantic import BaseModel, Field

from ..core.errors import not_found
from ..models import RunCreate, RunView
from .access import DESIGNER_ROLE, current_username
from ..storage.layout import project_design_wiki_key
from ..models import SessionView

router = APIRouter(prefix="/api/v1", tags=["Project Wiki"])
logger = logging.getLogger(__name__)


class RestoreWikiVersion(BaseModel):
    version: int = Field(ge=1)


class ApproveProjectTemplate(BaseModel):
    session_id: UUID
    run_id: UUID
    option_key: str = Field(min_length=1, max_length=100)


class ApproveFigmaTemplate(BaseModel):
    design_system_id: str = Field(min_length=1, max_length=80)
    frame_id: str = Field(min_length=1, max_length=120)


class CreateProjectDesignSession(BaseModel):
    project_id: str = Field(min_length=1, max_length=120)
    project_name: str = Field(min_length=1, max_length=200)
    project_description: str = Field(default="", max_length=4_000)
    design_system_id: str | None = Field(default=None, min_length=1, max_length=80)


def _require(project_id: str, role: str | None = None) -> None:
    if not auth_disabled() or project_id:
        require_project(project_id, role)


@router.post("/project-design-system/sessions", response_model=SessionView, status_code=status.HTTP_201_CREATED)
async def create_project_design_session(request: Request, body: CreateProjectDesignSession) -> SessionView:
    """Start a project-wide Design System Agent conversation with known project context."""
    _require(body.project_id, DESIGNER_ROLE)
    session = await request.app.state.store.create_session(
        f"{body.project_name.strip()} · Project Design System",
        project_id=body.project_id,
        created_by=current_username(),
        source="project-design-system",
    )
    from ..models import DesignBrief

    brief = DesignBrief(
        product_name=body.project_name.strip(),
        product_description=body.project_description.strip(),
    )
    await request.app.state.store.save_design_brief(session.id, brief)
    session.design_brief = brief
    if body.design_system_id:
        design_system = await request.app.state.store.get_design_system(body.design_system_id)
        if design_system is None or design_system.project_id != body.project_id or design_system.status != "ready":
            raise not_found("Imported project Design System")
        await request.app.state.store.set_session_design_system(session.id, design_system.id)
    return session


@router.post("/project-wiki/approve-template", status_code=status.HTTP_201_CREATED)
async def approve_project_template(
    request: Request,
    body: ApproveProjectTemplate,
    project_id: str = Query(..., min_length=1),
) -> dict[str, object]:
    """Save a selected rendered project-wide style preview as a versioned Wiki baseline."""
    _require(project_id, DESIGNER_ROLE)
    store = request.app.state.store
    session = await store.get_session(body.session_id)
    run = await store.get_run(body.run_id)
    if session is None or run is None or run.session_id != body.session_id or session.project_id != project_id:
        raise not_found("Project style preview")

    brief = await store.get_design_brief(session.id)
    if not brief.style_preview_shown:
        raise not_found("Generated project style preview")
    candidate = next((item for item in brief.style_candidates if item.key == body.option_key), None)
    if candidate is None:
        raise not_found("Style option")
    events = await store.list_events(run.id)
    preview_event = next((item for item in reversed(events) if item.type == "style.previews"), None)
    options = (preview_event.payload or {}).get("options", []) if preview_event else []
    option = next((item for item in options if item.get("key") == body.option_key), None)
    if not option or not isinstance(option.get("html"), str) or not isinstance(option.get("compiled_css"), str):
        raise not_found("Rendered style template")

    previous = await store.get_project_design_wiki(project_id)
    expected_version = int((previous or {}).get("version", 0)) + 1
    from platform_core.storage import get_storage
    from wiki.project_files import ProjectFileWiki

    storage = get_storage()
    project_files = ProjectFileWiki(storage)
    platform = preview_event.payload.get("platform", "responsive") if preview_event else "responsive"
    screen_name = preview_event.payload.get("screen_name", "Project sample") if preview_event else "Project sample"
    sample_name = str(option.get("name") or candidate.name)
    sample_base = f"templates/design-system/v{expected_version}/selected-ui"
    design_system_path = f"assets/design-system/v{expected_version}/design-system.json"
    image_match = re.match(r"data:([^;]+);base64,(.+)", str(option.get("image") or ""), re.DOTALL)
    if not image_match:
        raise not_found("Rendered project preview image")
    image_mime = image_match.group(1)
    image_bytes = base64.b64decode(image_match.group(2))
    selected_sample = {
        "key": candidate.key,
        "name": sample_name,
        "tagline": option.get("tagline") or candidate.tagline or "",
        "recommended": bool(option.get("recommended", candidate.recommended)),
        "screen_id": preview_event.payload.get("screen_id") if preview_event else None,
        "screen_name": screen_name,
        "platform": platform,
        "preview_width": int(option.get("preview_width") or 390),
        "preview_height": int(option.get("preview_height") or 844),
        "html_path": f"{sample_base}.html",
        "css_path": f"{sample_base}.css",
        "preview_path": f"{sample_base}.jpg",
    }
    from ..designsystem.contract import qna_contract

    design_system = qna_contract(candidate.model_dump(mode="json"), selected_sample, expected_version)
    previous_design_system = ((previous or {}).get("baseline") or {}).get("design_system") or {}
    old_tokens = {item.get("name"): item.get("value") for item in previous_design_system.get("tokens", [])
                 if isinstance(item, dict) and item.get("name")}
    new_tokens = {item.get("name"): item.get("value") for item in design_system["tokens"]}
    token_changes = {
        "added": sorted(name for name in new_tokens.keys() - old_tokens.keys()),
        "removed": sorted(name for name in old_tokens.keys() - new_tokens.keys()),
        "changed": [{"name": name, "from": old_tokens[name], "to": new_tokens[name]}
                    for name in sorted(old_tokens.keys() & new_tokens.keys())
                    if old_tokens[name] != new_tokens[name]],
    }
    design_system["version_comparison"] = {"from_version": (previous or {}).get("version"),
                                           "to_version": expected_version, "tokens": token_changes}
    project_files.write_json(project_id, design_system_path, design_system)
    storage.put(project_files.key(project_id, selected_sample["html_path"]),
                str(option["html"]).encode("utf-8"), content_type="text/html; charset=utf-8")
    storage.put(project_files.key(project_id, selected_sample["css_path"]),
                str(option["compiled_css"]).encode("utf-8"), content_type="text/css; charset=utf-8")
    storage.put(project_files.key(project_id, selected_sample["preview_path"]), image_bytes,
                content_type=image_mime)
    project_files.write_json(project_id, f"assets/design-system/v{expected_version}/manifest.json", {
        "project_id": project_id, "version": expected_version,
        "source_session_id": str(session.id), "source_run_id": str(run.id),
        "selected_style_contract": candidate.model_dump(mode="json"),
        "selected_preview": selected_sample, "preview_image_mime": image_mime,
        "design_system_path": design_system_path, "design_system": design_system,
    })
    template_body = (
        f"# {sample_name} — shared UI template\n\n"
        "The selected project-wide style preview. Feature Designers reuse this as a visual baseline "
        "and adapt each screen to its feature's approved requirements.\n\n"
        f"- Style key: `{candidate.key}`\n- Platform: {platform}\n"
        f"- Source session: `{session.id}`\n- HTML: `{selected_sample['html_path']}`\n"
        f"- Compiled CSS: `{selected_sample['css_path']}`\n- Preview: `{selected_sample['preview_path']}`\n"
        f"- Shared contract: `{design_system_path}`\n"
    )
    project_files.write_markdown(
        project_id, f"history/templates/v{expected_version}/selected-ui.md",
        title=sample_name, document_type="design_template", version=str(expected_version),
        status="approved", body=template_body,
        metadata={"style_key": candidate.key, "source_session_id": str(session.id),
                  "html_path": selected_sample["html_path"], "css_path": selected_sample["css_path"],
                  "preview_path": selected_sample["preview_path"]},
    )
    template_note_path = f"TEMPLATES/selected-ui-v{expected_version}.md"
    template_note = project_files._yaml({
        "title": sample_name, "type": "design_template", "project_id": project_id,
        "namespace": "project", "version": str(expected_version), "status": "approved",
        "style_key": candidate.key, "source_session_id": str(session.id),
        "html_path": selected_sample["html_path"], "css_path": selected_sample["css_path"],
        "preview_path": selected_sample["preview_path"],
        "design_system_path": design_system_path,
    }) + template_body
    await asyncio.to_thread(project_files.write_basic_memory_note, project_id, template_note_path, template_note)
    contract = candidate.model_dump(mode="json")
    baseline = {
        "contract": contract,
        "design_system_id": session.design_system_id,
        "platforms": brief.platforms or [preview_event.payload.get("platform", "responsive")],
        "selected_ui_sample": selected_sample,
        "design_system_path": design_system_path,
        "design_system": design_system,
        "token_changes": token_changes,
        "source_session_id": str(session.id),
        "source_run_id": str(run.id),
        "source_feature_id": None,
        "source_feature_title": None,
    }
    palette = contract.get("palette", {})
    markdown = "\n".join([
        "# Project Design System", "",
        "Reusable project-wide visual baseline and selected rendered UI sample.", "",
        "## Approved direction", f"- Name: {candidate.name}", f"- Summary: {candidate.direction or candidate.tagline}",
        f"- Density: {candidate.density}", f"- Radius: {candidate.radius}", "",
        "## Semantic color tokens", *[f"- {name}: `{value}`" for name, value in palette.items()], "",
        "## Reusable UI template", f"- Sample: {screen_name}",
        f"- HTML: `{selected_sample['html_path']}`", f"- CSS: `{selected_sample['css_path']}`",
        f"- Preview: `{selected_sample['preview_path']}`", f"- Preview key: `{candidate.key}`", "",
        "## Shared contract", f"- JSON: `{design_system_path}`",
        f"- Compared with version {(previous or {}).get('version') or 'none'}: "
        f"{len(token_changes['added'])} added, {len(token_changes['removed'])} removed, "
        f"{len(token_changes['changed'])} changed tokens.",
        f"- Components currently catalogued: {len(design_system['components'])}",
        "- Items needing follow-up:", *[f"  - {item['question']}" for item in design_system["unresolved"]], "",
        "## Provenance", f"- Design System Agent session: `{session.id}`", f"- Preview run: `{run.id}`", "",
    ])
    version = await store.approve_project_design_baseline(
        project_id, baseline, markdown, session.id, approved_by=current_username(),
        expected_version=expected_version,
    )
    template_entries: list[dict[str, str]] = [{
        "title": sample_name, "path": f"selected-ui-v{version}.md",
        "page_id": f"selected-ui-v{version}", "version": str(version),
    }]
    try:
        from wiki.basic_memory import BasicMemoryClient

        memory = BasicMemoryClient()
        current_index = await asyncio.to_thread(
            memory.read_note, project_id, project_files.markdown_root(project_id), "TEMPLATES/README.md",
        )
        index_body = project_files.parse_markdown(
            str(current_index.get("content", current_index.get("markdown", "")) or "")
        )[1]
        managed = re.search(r"<!-- BEGIN MANAGED PROJECT WIKI INDEX -->(.*?)<!-- END MANAGED PROJECT WIKI INDEX -->",
                            index_body, re.S)
        if managed:
            for line in managed.group(1).splitlines():
                link = re.match(r"- \[(.*?)\]\((.*?)\).*?`([^`]+)`.*?v(.+)", line.strip())
                if link:
                    template_entries.append({"title": link.group(1), "path": link.group(2),
                                              "page_id": link.group(3), "version": link.group(4)})
    except Exception:
        logger.info("No previous template index to preserve for %s", project_id)
    unique_templates = {item["path"]: item for item in template_entries}
    await asyncio.to_thread(
        project_files.sync_project_index, project_id, "TEMPLATES/README.md",
        list(unique_templates.values()), title="Project templates",
    )
    try:
        from platform_core import artifacts

        markdown_key = project_design_wiki_key(project_id, version)
        markdown_bytes = get_storage().get(markdown_key)
        artifacts.register(
            project_id=project_id, kind="project_design_wiki", source_module="designer",
            source_ref=project_id, version=version, title=f"Project Design System v{version}",
            storage_key=markdown_key, content_hash=hashlib.sha256(markdown_bytes).hexdigest(),
            meta={"source_session_id": str(session.id), "source_run_id": str(run.id)},
            created_by=current_username(),
        )
    except Exception:  # noqa: BLE001 - the canonical Wiki save is already complete.
        logger.exception("Could not publish Project Design Wiki artifact for %s", project_id)
    return {"project_id": project_id, "version": version, "name": candidate.name,
            "template_key": project_files.key(project_id, selected_sample["html_path"]),
            "selected_ui_sample": selected_sample}


@router.post("/project-wiki/approve-figma-template", status_code=status.HTTP_201_CREATED)
async def approve_figma_template(
    request: Request, body: ApproveFigmaTemplate, project_id: str = Query(..., min_length=1),
) -> dict[str, object]:
    """Promote a selected Figma frame and imported tokens to the project Wiki baseline."""
    _require(project_id, DESIGNER_ROLE)
    store = request.app.state.store
    design_system = await store.get_design_system(body.design_system_id)
    if design_system is None or design_system.project_id != project_id or design_system.status != "ready":
        raise not_found("Imported Figma Design System")
    source_name, source_bytes = await store.get_design_system_source(body.design_system_id)
    if not source_bytes:
        raise not_found("Original Figma file")
    from ..designsystem.importer import import_fig

    imported = await asyncio.to_thread(import_fig, source_bytes, design_system.name)
    frame = next((item for item in imported.template_candidates if item["id"] == body.frame_id), None)
    if frame is None:
        raise not_found("Selected Figma screen")
    from ..models import DesignBrief, StyleCandidate
    from ..render.document import fragment_of
    from ..storage.layout import project_design_wiki_key
    from platform_core.storage import get_storage
    from wiki.project_files import ProjectFileWiki

    session = await store.create_session(
        f"{design_system.name} · Figma Design System", project_id=project_id,
        created_by=current_username(), source="project-design-system",
    )
    await store.set_session_design_system(session.id, design_system.id)
    await store.save_design_brief(session.id, DesignBrief(product_name=design_system.name))
    candidate = StyleCandidate(
        key=f"figma-{design_system.id}", name=design_system.name or "Figma Design System",
        tagline=f"Imported from {source_name or 'Figma'}", direction="Project visual system imported from the selected Figma file.",
        palette={role.replace("-", "_"): value for role, slug_name in (imported.tokens.semantic or {}).items()
                 if (value := imported.tokens.colors.get(slug_name))},
        heading_font=imported.tokens.fonts[0] if imported.tokens.fonts else "Inter",
        body_font=imported.tokens.fonts[0] if imported.tokens.fonts else "Inter",
    )
    previous = await store.get_project_design_wiki(project_id)
    version = int((previous or {}).get("version", 0)) + 1
    file_wiki = ProjectFileWiki(get_storage())
    base = f"templates/design-system/v{version}/selected-ui"
    contract_path = f"assets/design-system/v{version}/design-system.json"
    rendered = await request.app.state.renderer.render(
        frame["html"], width=max(320, min(1440, int(frame["width"] or 390))),
        height=max(640, min(1100, int(frame["height"] or 844))), theme_css=design_system.theme_css,
        fonts=design_system.fonts, lint=False, screenshot="jpeg",
    )
    if not rendered.image:
        raise not_found("Rendered Figma template preview")
    sample = {
        "key": candidate.key, "name": frame["name"], "screen_name": frame["name"], "screen_id": frame["id"],
        "platform": "mobile" if frame["width"] <= 600 else "responsive",
        "preview_width": rendered.width, "preview_height": rendered.height,
        "html_path": f"{base}.html", "css_path": f"{base}.css", "preview_path": f"{base}.jpg",
    }
    contract = imported.index()["design_system"]
    contract["source"].update({"design_system_id": design_system.id, "filename": source_name or design_system.source_name})
    contract["version_comparison"] = {"from_version": (previous or {}).get("version"), "to_version": version,
                                      "tokens": {"added": [item["name"] for item in contract["tokens"]],
                                                 "removed": [], "changed": []}}
    storage = get_storage()
    for path, content, mime in (
        (sample["html_path"], fragment_of(frame["html"]).encode("utf-8"), "text/html; charset=utf-8"),
        (sample["css_path"], (rendered.css or design_system.theme_css).encode("utf-8"), "text/css; charset=utf-8"),
        (sample["preview_path"], rendered.image, "image/jpeg"),
    ):
        storage.put(file_wiki.key(project_id, path), content, content_type=mime)
    file_wiki.write_json(project_id, contract_path, contract)
    file_wiki.write_json(project_id, f"assets/design-system/v{version}/manifest.json", {
        "project_id": project_id, "version": version, "design_system_id": design_system.id,
        "selected_style_contract": candidate.model_dump(mode="json"), "selected_preview": sample,
        "design_system_path": contract_path,
    })
    markdown = "\n".join([
        "# Project Design System", "", "Imported from the approved Figma project file.", "",
        f"- Figma file: {source_name or design_system.source_name}", f"- Selected template frame: {frame['name']}",
        f"- Components: {len(imported.components)}", f"- Tokens: {len(contract['tokens'])}",
        f"- Unresolved items: {len(imported.unresolved)}", f"- Template HTML: `{sample['html_path']}`",
        f"- Token contract: `{contract_path}`", "",
    ])
    baseline = {
        "contract": candidate.model_dump(mode="json"), "design_system_id": design_system.id,
        "selected_ui_sample": sample, "design_system_path": contract_path, "design_system": contract,
        "source_session_id": str(session.id), "source_run_id": None,
        "source_feature_id": None, "source_feature_title": None,
    }
    file_wiki.write_markdown(project_id, f"history/templates/v{version}/figma-template.md",
                             title=frame["name"], document_type="design_template", version=str(version),
                             status="approved", body=markdown,
                             metadata={"design_system_id": design_system.id, "frame_id": frame["id"],
                                       "contract_path": contract_path})
    await asyncio.to_thread(file_wiki.write_basic_memory_note, project_id,
                            f"TEMPLATES/figma-template-v{version}.md",
                            file_wiki._yaml({"title": frame["name"], "type": "design_template",
                                             "project_id": project_id, "namespace": "project",
                                             "version": str(version), "status": "approved",
                                             "contract_path": contract_path}) + markdown)
    saved_version = await store.approve_project_design_baseline(
        project_id, baseline, markdown, session.id, approved_by=current_username(), expected_version=version,
    )
    return {"project_id": project_id, "version": saved_version, "name": frame["name"],
            "design_system_id": design_system.id, "selected_ui_sample": sample}


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
