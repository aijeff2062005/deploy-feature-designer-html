import asyncio
import hashlib
import inspect
import json
import logging
from collections.abc import Awaitable, Callable, Coroutine
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from ..api.stream import EventStream
from ..core.config import Settings
from ..core.errors import AppError, unwrap_app_error
from ..core.llm import AdkProvider
from ..core.prompts import PromptLibrary
from ..models import ReviewCreate, RunStage, RunStatus
from ..render import Renderer
from ..storage.store import Store
from .designer import HtmlDesigner, _trace_async_method
from .publish import publish_design

logger = logging.getLogger("designer_agent.runs")


async def _project_wiki_snapshot(store: Store, designer: HtmlDesigner, run_id: UUID, session: Any, brief: Any,
                                approved_by: str | None, revision: int) -> dict[str, Any] | None:
    """Build the exact versioned Wiki files without writing them."""
    if not session or not session.project_id or not brief or not brief.selected_style_contract:
        return None
    wiki = await store.get_project_design_wiki(session.project_id)
    previous_baseline = (wiki or {}).get("baseline") or {}
    previous_contract = previous_baseline.get("contract") or {}
    same_project_style = bool(
        previous_contract.get("key")
        and previous_contract.get("key") == brief.selected_style_contract.get("key")
    )
    contract = previous_contract if same_project_style else brief.selected_style_contract
    palette = contract.get("palette") or {}
    palette_lines = [f"- {name}: `{value}`" for name, value in palette.items()]
    visual_system = previous_baseline.get("visual_system") if same_project_style else None
    source_session_id = (wiki or {}).get("source_session_id") if same_project_style else session.id
    source_session_id = source_session_id or session.id
    source_feature_id = (wiki or {}).get("source_feature_id") if same_project_style else session.source_ref
    source_title = previous_baseline.get("source_feature_title") if same_project_style else session.title
    design_system_id = previous_baseline.get("design_system_id") if same_project_style else session.design_system_id
    platforms = previous_baseline.get("platforms") if same_project_style else brief.platforms
    try:
        visual_system_version = int(visual_system.get("schema_version") or 0 if isinstance(visual_system, dict) else 0)
    except (TypeError, ValueError):
        visual_system_version = 0
    if not isinstance(visual_system, dict) or not visual_system.get("platforms") or visual_system_version < 3:
        capture_session = session
        if same_project_style and source_session_id:
            try:
                capture_session = await store.get_session(UUID(str(source_session_id))) or session
            except (TypeError, ValueError):
                capture_session = session
        try:
            visual_system = await designer.capture_project_wiki_visual_system(capture_session)
        except Exception:  # noqa: BLE001 - preserve the selected contract if extraction is unavailable.
            logger.exception("Could not capture computed visual styles for project %s", session.project_id)
            visual_system = {}
    visual_lines: list[str] = []
    for platform, snapshot in (visual_system.get("platforms") or {}).items():
        profile = snapshot.get("profile") or {}
        visual_lines.extend([f"### {platform}", f"- Screens inspected: {len(profile.get('screens') or [])}"])
        for name, record in sorted((profile.get("tokens") or {}).items()):
            if record.get("conflict"):
                sources = ", ".join(source for item in record.get("values", []) for source in item.get("sources", []))
                visual_lines.append(f"- Token `{name}`: conflict across {sources or 'approved screens'}; no value selected.")
            elif record.get("value"):
                visual_lines.append(f"- Token `{name}`: `{record['value']}`")
        for role, variants in sorted((profile.get("roles") or {}).items()):
            for variant, properties in sorted(variants.items()):
                resolved = [f"{name}=`{item['value']}`" for name, item in sorted(properties.items())
                            if isinstance(item, dict) and not item.get("conflict") and item.get("value")]
                conflicts = [name for name, item in properties.items()
                             if isinstance(item, dict) and item.get("conflict")]
                if resolved:
                    visual_lines.append(f"- Component `{role}/{variant}`: " + ", ".join(resolved))
                for name in conflicts:
                    visual_lines.append(f"- Component `{role}/{variant}` property `{name}` conflicts; no value selected.")
    markdown = "\n".join([
        "# Project Design System", "",
        "Reusable visual baseline promoted from a human-approved Designer feature.",
        "Feature-specific requirements, screens, copy, and interaction states remain in their own BA package and UI spec.", "",
        "## Direction", f"- Name: {brief.style_name or contract.get('name', '—')}",
        f"- Summary: {brief.style_direction or contract.get('direction') or contract.get('tagline') or '—'}",
        f"- Heading font: {contract.get('heading_font') or '—'}", f"- Body font: {contract.get('body_font') or '—'}",
        f"- Radius: {contract.get('radius') or '—'}", f"- Density: {contract.get('density') or '—'}",
        f"- Composition: {contract.get('composition') or '—'}", f"- Surface language: {contract.get('surface_language') or '—'}",
        f"- Shape language: {contract.get('shape_language') or '—'}", "", "## Color tokens",
        *(palette_lines or ["- No palette tokens were included in the approved contract."]), "",
        "## Structured visual contract",
        "Values are collected from explicitly annotated semantic tokens and component roles across all approved screens. Conflicts are preserved and never resolved by choosing the first or largest element. Legacy inferred DOM measurements are not used as design authority.",
        *(visual_lines or ["- No semantic token or component-role annotations were available; inheritance uses the approved direction contract only."]), "",
        "## Provenance", f"- Approved from Designer session: `{session.id}`",
        f"- Source feature title: {source_title or session.title}", f"- Source feature: `{source_feature_id or 'not linked'}`", "",
    ])
    baseline = {
        "contract": contract, "style_name": brief.style_name, "style_direction": brief.style_direction,
        "design_system_id": design_system_id, "source_feature_title": source_title or session.title,
        "platforms": platforms, "visual_system": visual_system,
    }
    wiki_version = int((wiki or {}).get("version") or 0)
    version = wiki_version + 1
    now = datetime.now(timezone.utc).isoformat()
    project_payload = {
        **baseline, "schema_version": "1.0", "project_id": session.project_id, "version": version,
        "status": "approved", "approved_by": approved_by or "human-review", "approved_at": now,
        "source_session_id": str(source_session_id), "source_feature_id": source_feature_id,
    }
    from wiki.project_files import ProjectFileWiki
    files = ProjectFileWiki()
    markdown_metadata = {
        "source_session_id": str(source_session_id), "source_feature_id": source_feature_id,
        "approved_by": project_payload["approved_by"], "approved_at": now,
    }
    yaml = files._yaml({"title": "Project Design System", "type": "design_system", "project_id": session.project_id,
                        "version": str(version), "status": "approved", **markdown_metadata})
    screens = await store.list_screens(session.id, with_html=True)
    template_files = [
        {"path": f"templates/design-system/v{version}/{screen.id}.html", "content": screen.html or "",
         "screen_name": screen.name, "platform": screen.platform}
        for screen in screens if screen.html
    ]
    manifest = {
        "project_id": session.project_id, "version": version, "source_session_id": str(session.id),
        "source_feature_id": source_feature_id, "asset_references": contract.get("assets") or [],
        "visual_system": visual_system or {},
        "note": "Versioned references and visual tokens; original uploaded assets remain in project asset storage.",
    }
    json_content = json.dumps(project_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    markdown_content = yaml + markdown.rstrip() + "\n"
    return {
        "revision": revision, "project_id": session.project_id, "version": version,
        "approved_at": now, "baseline": baseline,
        "files": [
            {"path": f"design-system/design-system.v{version:04d}.json", "content": json_content},
            {"path": "design-system/design-system.json", "content": json_content},
            {"path": "design-system.md", "content": markdown_content},
            {"path": f"history/design-system/v{version:04d}.md", "content": markdown_content},
            *template_files,
            {"path": f"assets/design-system/v{version}/manifest.json", "content": json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"},
        ],
        "run_id": str(run_id), "source_session_id": str(source_session_id),
        "source_feature_id": source_feature_id, "markdown": markdown, "manifest": manifest,
    }


class RunService:
    def __init__(
        self,
        store: Store,
        events: EventStream,
        llm: AdkProvider,
        prompts: PromptLibrary,
        settings: Settings,
        renderer: Renderer,
    ) -> None:
        self.store = store
        self.events = events
        self.llm = llm
        self.prompts = prompts
        self.settings = settings
        self.renderer = renderer
        self.tasks: dict[UUID, asyncio.Task[None]] = {}
        self.project_wiki_previews: dict[UUID, dict[str, Any]] = {}
        self.designer = HtmlDesigner(self)

    async def _trace_decision(self, run_id: UUID, revision: int, name: str, condition: str,
                              chosen: str, details: dict[str, Any] | None = None) -> None:
        await self.events.emit(run_id, revision, "debug.decision", {
            "phase": "run orchestration",
            "function": f"decision.{name}",
            "condition": condition,
            "chosen": chosen,
            "details": details or {},
        })

    async def project_wiki_preview(self, run_id: UUID, *, approved_by: str | None = None) -> dict[str, Any]:
        run = await self._require_run(run_id)
        if run.stage != RunStage.REVIEW_FINAL or run.status != RunStatus.WAITING_REVIEW:
            raise AppError("REVIEW_NOT_EXPECTED", "Design is not ready for final review", "Open the final Designer review first.", 409)
        session = await self.store.get_session(run.session_id)
        brief = await self.store.get_design_brief(run.session_id)
        snapshot = await _project_wiki_snapshot(self.store, self.designer, run_id, session, brief,
                                                approved_by, run.revision)
        if snapshot is None:
            raise AppError("PROJECT_WIKI_UNAVAILABLE", "Project Wiki preview unavailable", "This session has no project or approved design contract.", 409)
        self.project_wiki_previews[run_id] = snapshot
        return snapshot

    def _spawn(
        self, run_id: UUID, operation: Coroutine[Any, Any, None], name: str
    ) -> None:
        existing = self.tasks.get(run_id)
        if existing and not existing.done():
            existing.cancel()
        task = asyncio.create_task(self._guard(run_id, operation), name=name)
        self.tasks[run_id] = task

        def discard(completed: asyncio.Task[None]) -> None:
            if self.tasks.get(run_id) is completed:
                self.tasks.pop(run_id, None)
            if not completed.cancelled():
                # Platform Assets: the spec and the design bundle are published to the project.
                asyncio.get_running_loop().create_task(publish_design(self, run_id))

        task.add_done_callback(discard)

    async def _guard(self, run_id: UUID, operation: Coroutine[Any, Any, None]) -> None:
        """Errors end the run as blocked (configuration) or failed (retryable), never silently."""
        try:
            await operation
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            run = await self.store.get_run(run_id)
            if run is None:
                return
            app_error = unwrap_app_error(error) or AppError(
                "DESIGNER_FAILED", "Designer failed", f"{type(error).__name__}: {error}", 500, True,
                action="Retry the run.",
            )
            if app_error.code in {"LLM_NOT_CONFIGURED"}:
                await self._trace_decision(run_id, run.revision, "run.error_classification", "error code == LLM_NOT_CONFIGURED", "block_for_configuration", {
                    "error_code": app_error.code, "stage": run.stage.value,
                })
                await self._block(run_id, run.revision, app_error)
            else:
                await self._trace_decision(run_id, run.revision, "run.error_classification", "error code != LLM_NOT_CONFIGURED", "mark_failed_and_retryable", {
                    "error_code": app_error.code, "stage": run.stage.value,
                    "error_type": type(error).__name__, "error": str(error)[:1000],
                })
                await self._fail(run_id, run.revision, app_error)

    def _run(self, run_id: UUID, step: Callable[[], Awaitable[None]], name: str) -> None:
        self._spawn(run_id, step(), f"{name}:{run_id}")

    def start(self, run_id: UUID, feedback: str = "None") -> None:
        self._run(run_id, lambda: self.designer.analyze(run_id, feedback), "analyze")

    async def review(self, run_id: UUID, request: ReviewCreate, *, approved_by: str | None = None) -> None:
        run = await self._require_run(run_id)
        await self._trace_decision(run_id, run.revision, "review.revision_check", "request.revision == current revision", "accept" if request.revision == run.revision else "reject", {
            "request_revision": request.revision, "current_revision": run.revision,
        })
        if request.revision != run.revision:
            raise AppError("REVISION_CONFLICT", "Review is for an older revision", f"Current revision is {run.revision}.", 409)
        expected = {"specification": RunStage.REVIEW_SPEC, "final": RunStage.REVIEW_FINAL}[request.checkpoint]
        await self._trace_decision(run_id, run.revision, "review.stage_check", "run is waiting at requested checkpoint", "accept" if run.status == RunStatus.WAITING_REVIEW and run.stage == expected else "reject", {
            "checkpoint": request.checkpoint, "expected_stage": expected.value,
            "actual_stage": run.stage.value, "actual_status": run.status.value,
        })
        if run.status != RunStatus.WAITING_REVIEW or run.stage != expected:
            raise AppError("REVIEW_NOT_EXPECTED", "Nothing to review", f"Run is {run.stage}/{run.status}.", 409)
        preview = self.project_wiki_previews.get(run_id)
        if request.decision == "approved" and request.checkpoint == "final" and preview:
            current_wiki = await self.store.get_project_design_wiki(preview["project_id"])
            next_version = int((current_wiki or {}).get("version") or 0) + 1
            if next_version != preview["version"]:
                self.project_wiki_previews.pop(run_id, None)
                raise AppError("PROJECT_WIKI_PREVIEW_STALE", "Project Wiki changed", "Reopen Approve Design to preview the latest version.", 409)
        await self.store.add_review(run_id, run.revision, request.checkpoint, request.decision, request.feedback)
        await self.events.emit(run_id, run.revision, "review.recorded", request.model_dump(mode="json"))
        if request.decision == "rejected":
            await self._trace_decision(run_id, run.revision, "review.decision", "decision == rejected", "cancel_run", request.model_dump(mode="json"))
            await self.store.update_run(run_id, stage=RunStage.FINISHED, status=RunStatus.CANCELLED,
                                        assistant_message="Đã dừng theo yêu cầu.")
            return
        if request.checkpoint == "specification":
            if request.decision == "approved":
                await self._trace_decision(run_id, run.revision, "review.next_stage", "checkpoint == specification and decision == approved", "build_screens")
                self._run(run_id, lambda: self.designer.build(run_id), "build")
            else:
                await self._trace_decision(run_id, run.revision, "review.next_stage", "checkpoint == specification and decision == changes_requested", "rewrite_specification", {"feedback": request.feedback})
                self._run(run_id, lambda: self.designer.write_spec(run_id, request.feedback or "Revise it."), "spec")
            return
        if request.decision == "approved":
            await self._trace_decision(run_id, run.revision, "review.next_stage", "checkpoint == final and decision == approved", "complete_run")
            session = await self.store.get_session(run.session_id)
            brief = await self.store.get_design_brief(run.session_id)
            if session and session.project_id and brief and brief.selected_style_contract:
                wiki = await self.store.get_project_design_wiki(session.project_id)
                previous_baseline = (wiki or {}).get("baseline") or {}
                previous_contract = previous_baseline.get("contract") or {}
                same_project_style = bool(
                    previous_contract.get("key")
                    and previous_contract.get("key") == brief.selected_style_contract.get("key")
                )
                # Inherited features may confirm the existing style, but must
                # not replace its canonical visual snapshot with a drifted screen.
                contract = previous_contract if same_project_style else brief.selected_style_contract
                palette = contract.get("palette") or {}
                palette_lines = [f"- {name}: `{value}`" for name, value in palette.items()]
                visual_system = previous_baseline.get("visual_system") if same_project_style else None
                source_session_id = (wiki or {}).get("source_session_id") if same_project_style else session.id
                source_feature_id = (wiki or {}).get("source_feature_id") if same_project_style else session.source_ref
                source_title = previous_baseline.get("source_feature_title") if same_project_style else session.title
                design_system_id = previous_baseline.get("design_system_id") if same_project_style else session.design_system_id
                platforms = previous_baseline.get("platforms") if same_project_style else brief.platforms
                try:
                    visual_system_version = int(
                        visual_system.get("schema_version") or 0
                        if isinstance(visual_system, dict) else 0
                    )
                except (TypeError, ValueError):
                    visual_system_version = 0
                if (
                    not isinstance(visual_system, dict)
                    or not visual_system.get("platforms")
                    or visual_system_version < 3
                ):
                    capture_session = session
                    if same_project_style and source_session_id:
                        try:
                            capture_session = await self.store.get_session(UUID(str(source_session_id))) or session
                        except (TypeError, ValueError):
                            capture_session = session
                    try:
                        visual_system = await self.designer.capture_project_wiki_visual_system(capture_session)
                    except Exception:  # noqa: BLE001 — preserve the approved contract if visual extraction is unavailable.
                        logger.exception("Could not capture computed visual styles for project %s", session.project_id)
                        visual_system = {}
                visual_lines = []
                for platform, snapshot in (visual_system.get("platforms") or {}).items():
                    profile = snapshot.get("profile") or {}
                    visual_lines.extend([
                        f"### {platform}",
                        f"- Screens inspected: {len(profile.get('screens') or [])}",
                    ])
                    for name, record in sorted((profile.get("tokens") or {}).items()):
                        if record.get("conflict"):
                            sources = ", ".join(source for item in record.get("values", []) for source in item.get("sources", []))
                            visual_lines.append(f"- Token `{name}`: conflict across {sources or 'approved screens'}; no value selected.")
                        elif record.get("value"):
                            visual_lines.append(f"- Token `{name}`: `{record['value']}`")
                    for role, variants in sorted((profile.get("roles") or {}).items()):
                        for variant, properties in sorted(variants.items()):
                            resolved = [f"{name}=`{item['value']}`" for name, item in sorted(properties.items())
                                        if isinstance(item, dict) and not item.get("conflict") and item.get("value")]
                            conflicts = [name for name, item in properties.items()
                                         if isinstance(item, dict) and item.get("conflict")]
                            if resolved:
                                visual_lines.append(f"- Component `{role}/{variant}`: " + ", ".join(resolved))
                            for name in conflicts:
                                visual_lines.append(f"- Component `{role}/{variant}` property `{name}` conflicts; no value selected.")
                markdown = "\n".join([
                    "# Project Design System",
                    "",
                    "Reusable visual baseline promoted from a human-approved Designer feature.",
                    "Feature-specific requirements, screens, copy, and interaction states remain in their own BA package and UI spec.",
                    "",
                    "## Direction",
                    f"- Name: {brief.style_name or contract.get('name', '—')}",
                    f"- Summary: {brief.style_direction or contract.get('direction') or contract.get('tagline') or '—'}",
                    f"- Heading font: {contract.get('heading_font') or '—'}",
                    f"- Body font: {contract.get('body_font') or '—'}",
                    f"- Radius: {contract.get('radius') or '—'}",
                    f"- Density: {contract.get('density') or '—'}",
                    f"- Composition: {contract.get('composition') or '—'}",
                    f"- Surface language: {contract.get('surface_language') or '—'}",
                    f"- Shape language: {contract.get('shape_language') or '—'}",
                    "",
                    "## Color tokens",
                    *(palette_lines or ["- No palette tokens were included in the approved contract."]),
                    "",
                    "## Structured visual contract",
                    "Values are collected from explicitly annotated semantic tokens and component roles across all approved screens. Conflicts are preserved and never resolved by choosing the first or largest element. Legacy inferred DOM measurements are not used as design authority.",
                    *(visual_lines or ["- No semantic token or component-role annotations were available; inheritance uses the approved direction contract only."]),
                    "",
                    "## Provenance",
                    f"- Approved from Designer session: `{session.id}`",
                    f"- Source feature title: {source_title or session.title}",
                    f"- Source feature: `{source_feature_id or 'not linked'}`",
                    "",
                ])
                baseline = {
                    "contract": contract,
                    "style_name": brief.style_name,
                    "style_direction": brief.style_direction,
                    "design_system_id": design_system_id,
                    "source_feature_title": source_title or session.title,
                    "platforms": platforms,
                    "visual_system": visual_system,
                }
                snapshot = self.project_wiki_previews.pop(run_id, None)
                if snapshot and snapshot.get("revision") == run.revision:
                    baseline = snapshot["baseline"]
                    markdown = snapshot["markdown"]
                    visual_system = baseline.get("visual_system") or {}
                    source_session_id = snapshot["source_session_id"]
                    source_feature_id = snapshot["source_feature_id"]
                try:
                    version = await self.store.approve_project_design_baseline(
                        session.project_id, baseline, markdown, UUID(str(source_session_id)), source_feature_id,
                        approved_by=approved_by,
                        approved_at=snapshot.get("approved_at") if snapshot and snapshot.get("revision") == run.revision else None,
                        expected_version=snapshot.get("version") if snapshot and snapshot.get("revision") == run.revision else None,
                    )
                    # Keep the approved HTML screens and their asset references beside
                    # the versioned contract so the next feature can inspect/reuse them.
                    from wiki.project_files import ProjectFileWiki

                    project_files = ProjectFileWiki()
                    if snapshot and snapshot.get("revision") == run.revision:
                        template_files = [item for item in snapshot["files"] if item["path"].endswith(".html")]
                        for item in template_files:
                            project_files.storage.put(
                                project_files.key(session.project_id, item["path"]),
                                item["content"].encode("utf-8"),
                                content_type="text/html; charset=utf-8",
                            )
                        manifest_file = next(item for item in snapshot["files"] if item["path"].endswith("/manifest.json"))
                        project_files.storage.put(
                            project_files.key(session.project_id, manifest_file["path"]),
                            manifest_file["content"].encode("utf-8"),
                            content_type="application/json",
                        )
                    else:
                        approved_screens = await self.store.list_screens(session.id, with_html=True)
                        for screen in approved_screens:
                            if screen.html:
                                project_files.storage.put(
                                    project_files.key(session.project_id, f"templates/design-system/v{version}/{screen.id}.html"),
                                    screen.html.encode("utf-8"), content_type="text/html; charset=utf-8",
                                )
                        project_files.write_json(session.project_id, f"assets/design-system/v{version}/manifest.json", {
                            "project_id": session.project_id, "version": version,
                            "source_session_id": str(session.id), "source_feature_id": source_feature_id,
                            "asset_references": contract.get("assets") or [], "visual_system": visual_system or {},
                            "note": "Versioned references and visual tokens; original uploaded assets remain in project asset storage.",
                        })
                except Exception:  # noqa: BLE001 - do not finish Designer approval before Project Wiki is saved.
                    logger.exception("Could not save project Design Wiki for %s", session.project_id)
                    raise
                else:
                    try:
                        from wiki.basic_memory import BasicMemoryClient
                        from wiki.project_files import ProjectFileWiki

                        files = ProjectFileWiki()
                        await asyncio.to_thread(
                            BasicMemoryClient().ensure_project,
                            session.project_id, files.markdown_root(session.project_id),
                        )
                    except Exception:  # noqa: BLE001 - approval remains saved; BM indexing can retry later.
                        logger.exception("Project Design Wiki saved but Basic Memory indexing is pending for %s", session.project_id)
                    await self.events.emit(run_id, run.revision, "project.wiki.updated", {
                        "kind": "design_baseline", "version": version,
                        "message": "Đã cập nhật chuẩn thiết kế dùng chung của project từ thiết kế được duyệt.",
                    })
                    try:
                        from platform_core import artifacts
                        from ..storage.layout import project_design_wiki_key

                        markdown_bytes = markdown.encode("utf-8")
                        artifacts.register(
                            project_id=session.project_id,
                            kind="project_design_wiki",
                            source_module="designer",
                            source_ref=session.project_id,
                            version=version,
                            title=f"Project Design System v{version}",
                            storage_key=project_design_wiki_key(session.project_id, version),
                            content_hash=hashlib.sha256(markdown_bytes).hexdigest(),
                            meta={"source_session_id": str(session.id), "source_feature_id": session.source_ref},
                            created_by=session.created_by,
                        )
                    except Exception:  # noqa: BLE001 — artifact indexing is best effort after the Wiki save.
                        logger.exception("Could not publish project Design Wiki artifact for %s", session.project_id)
            await self.store.update_run(run_id, stage=RunStage.FINISHED, status=RunStatus.COMPLETED)
            await self.events.emit(run_id, run.revision, "stage.changed", {"stage": "finished", "status": "completed"})
            asyncio.get_running_loop().create_task(publish_design(self, run_id))
            return
        targets = await self.designer.targets_for(run, request.feedback)
        await self._trace_decision(run_id, run.revision, "review.next_stage", "checkpoint == final and decision == changes_requested", "rebuild_selected_screens", {
            "feedback": request.feedback, "selected_targets": targets,
        })
        self._run(run_id, lambda: self.designer.build(run_id, targets, instruction=request.feedback, review=True), "rebuild")

    async def retry(self, run_id: UUID) -> None:
        run = await self._require_run(run_id)
        await self._trace_decision(run_id, run.revision, "retry.status_check", "run status is blocked / failed / cancelled", "accept" if run.status in {RunStatus.BLOCKED, RunStatus.FAILED, RunStatus.CANCELLED} else "reject", {
            "status": run.status.value,
        })
        if run.status not in {RunStatus.BLOCKED, RunStatus.FAILED, RunStatus.CANCELLED}:
            raise AppError("RUN_NOT_RETRYABLE", "Run is not retryable", f"Run status is {run.status}.", 409)
        failed_at = (run.error or {}).get("stage") or ""
        if run.specification and failed_at in {"assembly", "review_final"}:
            await self._trace_decision(run_id, run.revision, "retry.resume_point", "spec exists and failure stage is assembly / review_final", "resume_build", {"failed_at": failed_at})
            self._run(run_id, lambda: self.designer.build(run_id, review=run.intent != "design"), "build")
        elif failed_at in {"specification", "review_spec"}:
            await self._trace_decision(run_id, run.revision, "retry.resume_point", "failure stage is specification / review_spec", "rewrite_spec", {"failed_at": failed_at})
            self._run(run_id, lambda: self.designer.write_spec(run_id), "spec")
        else:
            await self._trace_decision(run_id, run.revision, "retry.resume_point", "no stage-specific resume rule matched", "restart_analysis", {"failed_at": failed_at})
            self.start(run_id)

    async def cancel(self, run_id: UUID) -> None:
        run = await self._require_run(run_id)
        task = self.tasks.pop(run_id, None)
        await self._trace_decision(run_id, run.revision, "cancel.active_task", "task exists and is not done", "cancel_task" if task and not task.done() else "no_active_task", {
            "task_found": task is not None, "task_done": task.done() if task else None,
            "stage": run.stage.value, "status": run.status.value,
        })
        if task and not task.done():
            task.cancel()
        await self.store.update_run(run_id, status=RunStatus.CANCELLED)
        await self.events.emit(run_id, run.revision, "run.cancelled", {"stage": run.stage.value})

    async def _require_run(self, run_id: UUID):
        run = await self.store.get_run(run_id)
        if run is None:
            raise AppError("RUN_NOT_FOUND", "Run not found", str(run_id), 404)
        return run

    async def delete_session(self, session_id: UUID) -> None:
        session = await self.store.get_session(session_id)
        if session is None:
            raise AppError("SESSION_NOT_FOUND", "Session not found", str(session_id), 404)
        runs = await self.store.list_runs(session_id)
        tasks: list[asyncio.Task[None]] = []
        for run in runs:
            task = self.tasks.pop(run.id, None)
            if task and not task.done():
                task.cancel()
                tasks.append(task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if not await self.store.delete_session(session_id):
            raise AppError("SESSION_NOT_FOUND", "Session not found", str(session_id), 404)

    async def _block(self, run_id: UUID, revision: int, error: AppError) -> None:
        run = await self.store.get_run(run_id)
        payload = self._problem(error, run.stage if run else None)
        await self.store.update_run(
            run_id, stage=RunStage.BLOCKED, status=RunStatus.BLOCKED, error=payload
        )
        await self.events.emit(run_id, revision, "action.required", payload)

    async def _fail(self, run_id: UUID, revision: int, error: AppError) -> None:
        run = await self.store.get_run(run_id)
        payload = self._problem(error, run.stage if run else None)
        await self.store.update_run(
            run_id, stage=RunStage.FAILED, status=RunStatus.FAILED, error=payload
        )
        await self.events.emit(run_id, revision, "run.failed", payload)

    @staticmethod
    def _problem(error: AppError, stage: RunStage | None = None) -> dict[str, Any]:
        return {
            "code": error.code,
            "title": error.title,
            "detail": error.detail,
            "retryable": error.retryable,
            "action": error.action,
            "stage": stage.value if stage else None,
        }

    async def shutdown(self) -> None:
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


# Record run orchestration methods as well as the Designer's internal helpers.
for _method_name, _method in tuple(vars(RunService).items()):
    if inspect.iscoroutinefunction(_method) and _method_name not in {"_trace_decision", "shutdown"}:
        setattr(RunService, _method_name, _trace_async_method(_method))
