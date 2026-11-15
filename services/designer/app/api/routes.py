import asyncio
import base64
import binascii
import json
import re
import struct
from string import Template
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, Query, Request, status
from platform_core.artifacts import get as get_platform_artifact
from platform_core.fastapi import auth_disabled, require_project
from platform_core.storage import get_storage
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..attachments.parser import parse_bpmn_document, parse_json_document, parse_text_document
from ..storage.assets import save_asset
from ..core.errors import AppError, not_found
from ..core.llm import STRUCTURED_PARSER_VERSION
from ..designsystem.service import design_context
from ..huly import client as huly_client
from ..models import ExactAsset, Platform, ReviewCreate, RunCreate, RunView, SessionCreate, SessionView
from ..render.assets import inline_assets
from .access import DESIGNER_ROLE, current_user_id, current_username

router = APIRouter(prefix="/api/v1")


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=50_000)
    screen_name_hint: str = Field(default="", max_length=120)
    platform_hint: Platform = "responsive"
    style_hint: str | None = None
    library_ids: list[str] = Field(default_factory=list)
    assets: list[ExactAsset] = Field(default_factory=list, max_length=4)


class HulyIssueCreate(BaseModel):
    run_id: UUID
    project_id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=240)
    objective: str = Field(min_length=1, max_length=8_000)
    current_state: str = Field(min_length=1, max_length=8_000)
    solution: str = Field(min_length=1, max_length=12_000)
    acceptance_criteria: str = Field(min_length=1, max_length=12_000)


class AttachmentParseRequest(BaseModel):
    name: str = Field(min_length=1, max_length=240)
    mime_type: str = Field(default="", max_length=120)
    data_base64: str = Field(min_length=1, max_length=12_000_000)


class AttachmentParseResult(BaseModel):
    name: str
    kind: str
    markdown: str
    truncated: bool = False
    asset: ExactAsset | None = None


class ImageAttachmentAnalysis(BaseModel):
    summary: str = Field(min_length=1, max_length=1_500)
    visible_text: list[str] = Field(default_factory=list, max_length=60)
    ui_elements: list[str] = Field(default_factory=list, max_length=40)
    requirements_and_states: list[str] = Field(default_factory=list, max_length=40)
    visual_role: str = Field(default="image", max_length=120)
    dominant_colors: list[str] = Field(default_factory=list, max_length=8)
    background_type: str = Field(default="unknown", max_length=80)
    recommended_fit: str = Field(default="contain", max_length=20)
    recommended_placement: str = Field(default="content area", max_length=240)
    placement_reason: str = Field(default="", max_length=600)
    safe_padding_percent: int = Field(default=0, ge=0, le=40)


IMAGE_SUFFIXES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".svg": "image/svg+xml"}
TEXT_SUFFIXES = {".txt", ".md", ".csv", ".yaml", ".yml"}
MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024

def _standard_asset_id(name: str, suffix: str) -> str:
    stem = unicodedata.normalize("NFKD", Path(name).stem)
    stem = stem.encode("ascii", "ignore").decode("ascii").casefold()
    slug = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")[:40] or "image"
    extension = suffix.lstrip(".").replace("jpeg", "jpg") or "img"
    return f"{slug}-{extension}-{uuid4().hex[:12]}"


def _image_size(raw: bytes) -> tuple[int, int] | None:
    if raw.startswith(b"\x89PNG\r\n\x1a\n") and len(raw) >= 24:
        return struct.unpack(">II", raw[16:24])
    if raw.startswith(b"\xff\xd8"):
        offset = 2
        while offset + 9 < len(raw):
            if raw[offset] != 0xFF:
                offset += 1
                continue
            marker = raw[offset + 1]
            offset += 2
            if marker in {0xD8, 0xD9}:
                continue
            if offset + 2 > len(raw):
                break
            length = int.from_bytes(raw[offset : offset + 2], "big")
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                if offset + 7 <= len(raw):
                    return (
                        int.from_bytes(raw[offset + 5 : offset + 7], "big"),
                        int.from_bytes(raw[offset + 3 : offset + 5], "big"),
                    )
                break
            if length < 2:
                break
            offset += length
    return None


@router.get("/health")
async def health(request: Request) -> dict[str, object]:
    settings = request.app.state.settings
    return {
        "status": "ok",
        "llmConfigured": bool(settings.api_key and settings.base_url),
        "model": settings.llm_model,
        "structuredParser": STRUCTURED_PARSER_VERSION,
        "apiVersion": "v1",
    }


@router.get("/huly/projects")
async def list_huly_projects(request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    projects = await huly_client.list_projects(settings)
    return {"projects": projects, "default_project_id": settings.huly_project}


def _read_ba_package(session: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Load the exact BA package version recorded when this Designer session was handed off."""
    artifact_id = getattr(session, "source_artifact_id", None)
    if not artifact_id:
        return None, None
    artifact = get_platform_artifact(artifact_id)
    if not artifact or artifact.get("kind") != "ba_package" or not artifact.get("storage_key"):
        return None, artifact
    raw = get_storage().get(artifact["storage_key"])
    try:
        package = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return None, artifact
    return (package if isinstance(package, dict) else None), artifact


def _task_draft_from_sources(session: Any, run: Any, screens: list[Any], package: dict[str, Any] | None, artifact: dict[str, Any] | None) -> dict[str, Any]:
    template_path = Path(__file__).resolve().parents[1] / "huly" / "task_draft_templates.json"
    templates = json.loads(template_path.read_text(encoding="utf-8"))
    fallback = templates["fallbacks"]

    def render(template: str, values: dict[str, Any]) -> str:
        return Template(template).safe_substitute(values).strip()

    def fallback_text(key: str) -> str:
        return str(fallback.get(key, ""))

    def join_or(values: list[str], key: str) -> str:
        return ", ".join(value for value in values if value) or fallback_text(key)

    payload = package.get("payload", {}) if isinstance(package, dict) else {}
    payload = payload if isinstance(payload, dict) else {}
    artifacts = payload.get("artifacts", {}) if isinstance(payload.get("artifacts"), dict) else {}
    feature = artifacts.get("feature_definition", {}) if isinstance(artifacts.get("feature_definition"), dict) else {}
    business_rules = [item for item in artifacts.get("business_rules", []) if isinstance(item, dict)]
    use_cases = [item for item in artifacts.get("use_cases", []) if isinstance(item, dict)]
    workflow = artifacts.get("bpmn_reference", {}) if isinstance(artifacts.get("bpmn_reference"), dict) else {}
    touchpoints = [item for item in artifacts.get("ui_touchpoints", []) if isinstance(item, dict)]
    exceptions = [item for item in workflow.get("exceptions", []) if isinstance(item, dict)]
    feature_id = str((package or {}).get("feature_id") or getattr(session, "source_ref", "") or "").strip()
    feature_title = str(payload.get("feature_title") or session.title).strip()
    goal = str(feature.get("goal") or payload.get("feature_description") or "").strip()
    rule_ids = [str(item.get("id", "")).strip() for item in business_rules if str(item.get("id", "")).strip()]
    use_case_ids = [str(item.get("id", "")).strip() for item in use_cases if str(item.get("id", "")).strip()]
    platform_names = {str(screen.platform).casefold() for screen in screens}
    platform_key = "mobile" if platform_names == {"mobile"} else "desktop" if platform_names == {"desktop"} else "responsive"
    platform = templates["platform_labels"][platform_key]
    title_values = {"platform": platform, "feature_title": feature_title, "screen_count": len(screens)}
    title = render(templates["title"], title_values)
    title_limit = int(templates.get("title_max_length", len(title)))
    if len(title) > title_limit:
        prefix = render(templates["title_prefix"], title_values)
        suffix = render(templates["title_suffix"], title_values)
        title = prefix + feature_title[:max(0, title_limit - len(prefix) - len(suffix))].rstrip() + suffix

    objective_values = {
        "goal": goal or fallback_text("goal"),
        "screen_scope": render(templates["screen_scope"], {"screen_count": len(screens), "platform": platform}),
    }
    objective = "\n".join(render(item, objective_values) for item in templates["objective"])

    completed = fallback_text("completed")
    if package:
        completed = render(templates["source_summary"], {
            "rule_count": len(business_rules),
            "step_count": len(workflow.get("steps", [])),
            "exception_count": len(exceptions),
            "use_case_count": len(use_cases),
        })
    gap = render(templates["implementation_gap"], {
        "screen_count": len(screens),
        "screen_names": ", ".join(screen.name for screen in screens) or fallback_text("gap"),
    }) if screens else fallback_text("gap")
    current_state = "\n".join(
        render(item, {"completed": completed, "gap": gap})
        for item in templates["current_state"]
    )

    use_case_rules = {
        str(case.get("id")): [str(value) for value in case.get("rule_ids", []) if value]
        for case in use_cases
    }
    rule_descriptions = {
        str(item.get("id")): str(item.get("statement") or "").strip()
        for item in business_rules
        if item.get("id") and item.get("statement")
    }
    touchpoint_map = {str(item.get("id", "")).casefold(): item for item in touchpoints if item.get("id")}
    run_spec = run.specification if isinstance(run.specification, dict) else {}
    spec_screens = run_spec.get("screens", []) if isinstance(run_spec.get("screens"), list) else []
    specs_by_id = {str(item.get("id")): item for item in spec_screens if isinstance(item, dict)}
    screen_details: list[str] = []
    for screen in screens:
        spec = specs_by_id.get(str(screen.id), {})
        source_ids = [str(value) for value in spec.get("source_ui_ids", []) if value]
        related_use_cases: set[str] = set()
        for source_id in source_ids:
            touchpoint = touchpoint_map.get(source_id.casefold())
            if touchpoint:
                related_use_cases.update(str(value) for value in touchpoint.get("related_uc_ids", []) if value)
        mapped_rules = sorted({
            rule_descriptions[rule_id]
            for use_case_id in related_use_cases
            for rule_id in use_case_rules.get(use_case_id, [])
            if rule_descriptions.get(rule_id)
        })
        screen_details.append(render(templates["screen_detail"], {
            "name": screen.name,
            "sections": ", ".join(str(value) for value in spec.get("sections", []) if value) or fallback_text("unknown"),
            "states": ", ".join(str(value) for value in spec.get("states", []) if value) or fallback_text("unknown"),
            "rules": "; ".join(mapped_rules) or fallback_text("unknown"),
        }))

    solution_values = {
        "screen_count": len(screens),
        "screen_details": "\n".join(screen_details) or fallback_text("gap"),
        "business_rules": render(templates["business_rule_detail"], {
            "items": "; ".join(str(item.get("statement") or "").strip() for item in business_rules if str(item.get("statement") or "").strip()) or fallback_text("business_rules")
        }),
        "exception_flows": "\n".join(
            render(templates["exception_detail"], {
                "condition": str(item.get("condition") or fallback_text("unknown")),
                "handling": str(item.get("handling") or fallback_text("unknown")),
                "outcome": render(templates["exception_outcome_suffix"], {"value": item.get("outcome")}) if item.get("outcome") else "",
            }) for item in exceptions
        ) or fallback_text("exception_flows"),
        "scope_out": render(templates["scope_out_line"], {"items": join_or(
            [str(item) for item in feature.get("scope_out", []) if item] if isinstance(feature.get("scope_out"), list) else [],
            "scope_out",
        )}),
    }
    solution = "\n".join(render(item, solution_values) for item in templates["solution"])

    criteria: list[str] = []
    for case in use_cases:
        for item in case.get("acceptance_criteria", []) if isinstance(case.get("acceptance_criteria"), list) else []:
            if isinstance(item, dict):
                criteria.append(render(templates["use_case_acceptance"], {
                    "given": item.get("given") or fallback_text("unknown"),
                    "when": item.get("when") or fallback_text("unknown"),
                    "then": item.get("then") or fallback_text("unknown"),
                }))
    use_case_criteria = "\n".join(criteria) or fallback_text("use_case_criteria")
    widths = ", ".join(str(screen.width) for screen in screens if str(screen.platform).casefold() == "mobile")
    if not widths:
        widths = fallback_text("responsive_width")
    viewport_criteria = render(templates["viewport_criteria"], {"widths": widths})
    exception_criteria = "\n".join(
        render(templates["exception_acceptance"], {
            "condition": str(item.get("condition") or fallback_text("unknown")),
            "handling": str(item.get("handling") or fallback_text("unknown")),
            "outcome": render(templates["exception_acceptance_outcome_suffix"], {"value": item.get("outcome")}) if item.get("outcome") else "",
        }) for item in exceptions
    ) or fallback_text("exception_criteria")
    criteria_values = {
        "acceptance_summary": templates.get("acceptance_summary", ""),
        "use_case_criteria": use_case_criteria,
        "viewport_criteria": viewport_criteria,
        "exception_criteria": exception_criteria,
        "scope_criteria": fallback_text("scope_criteria"),
        "traceability_criteria": fallback_text("traceability_criteria"),
    }
    acceptance_criteria = "\n".join(render(item, criteria_values) for item in templates["acceptance_criteria"])

    missing = []
    if not package:
        missing = ["BA package", "Feature ID", "FD/BR/WF/US traceability", "Scope Out"]
    else:
        missing = [label for label, present in (
            ("Feature ID", bool(feature_id)),
            ("BR IDs", bool(rule_ids)),
            ("US IDs", bool(use_case_ids)),
            ("WF exceptions", bool(exceptions)),
            ("Scope Out", bool(feature.get("scope_out"))),
        ) if not present]

    return {
        "title": title,
        "objective": objective,
        "current_state": current_state,
        "solution": solution,
        "acceptance_criteria": acceptance_criteria,
        "source": {
            "available": package is not None,
            "feature_id": feature_id,
            "artifact_id": artifact.get("id") if artifact else None,
            "version": artifact.get("version") if artifact else None,
            "missing": missing,
        },
    }


@router.get("/sessions/{session_id}/huly/preview")
async def get_huly_attachment_preview(request: Request, session_id: UUID, run_id: UUID) -> dict[str, Any]:
    store = request.app.state.store
    session = await store.get_session(session_id)
    if session is None:
        raise not_found("Session")
    run = await store.get_run(run_id)
    if run is None or run.session_id != session_id:
        raise not_found("Run")
    if run.stage != "finished" or run.status != "completed":
        raise AppError("DESIGN_NOT_APPROVED", "Design is not approved", "Approve the final design before previewing task artifacts.", 409)
    screens = await store.list_screens(session_id, with_html=True)
    if not screens:
        raise AppError("DESIGN_SCREENS_MISSING", "No screens to preview", "The approved session has no saved screens.", 409)
    try:
        ba_package, ba_artifact = await asyncio.to_thread(_read_ba_package, session)
    except Exception:  # noqa: BLE001 - the design preview remains available if BA assets are temporarily unavailable
        ba_package, ba_artifact = None, None
    task_draft = _task_draft_from_sources(session, run, screens, ba_package, ba_artifact)

    spec = run.specification if isinstance(run.specification, dict) else {}
    spec_screens = spec.get("screens") if isinstance(spec.get("screens"), list) else []
    screen_specs = {str(item.get("id")): item for item in spec_screens if isinstance(item, dict)}
    markdown = [
        "# UI Specification",
        "",
        f"**Project:** {session.title}",
        f"**Screen count:** {len(screens)}",
        "",
        "> Tài liệu bàn giao UI đã duyệt cho đội phát triển. Nội dung được tổng hợp từ đặc tả màn hình và trạng thái đã lưu; các yêu cầu API/backend chỉ được bao gồm khi có trong nguồn.",
        "",
    ]

    def add_list(label: str, values: Any, *, code: bool = False) -> None:
        if not isinstance(values, list) or not values:
            return
        markdown.extend([f"### {label}"] if not label.startswith("### ") else [label])
        markdown.extend(f"- `{value}`" if code else f"- {value}" for value in values if isinstance(value, (str, int, float)) and str(value).strip())
        markdown.append("")

    def merge_lists(*values: Any) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for value in values:
            if not isinstance(value, list):
                continue
            for entry in value:
                if not isinstance(entry, (str, int, float)):
                    continue
                text = str(entry).strip()
                if text and text.casefold() not in seen:
                    seen.add(text.casefold())
                    merged.append(text)
        return merged

    screen_names = {str(item.get("id")): str(item.get("name") or item.get("id")) for item in spec_screens if isinstance(item, dict)}

    for index, screen in enumerate(screens, start=1):
        item = screen_specs.get(screen.id, {})
        defaults = spec.get("approved_default_screens", {}).get(screen.id, {}) if isinstance(spec.get("approved_default_screens"), dict) else {}
        state_refs = spec.get("state_reference_screens", {}).get(screen.id, {}) if isinstance(spec.get("state_reference_screens"), dict) else {}
        state_specs = spec.get("state_specs", {}) if isinstance(spec.get("state_specs"), dict) else {}
        state = state_specs.get(screen.id, {})
        if not isinstance(state, dict):
            state = {}
        markdown.extend([f"## {index}. {screen.name}", "", f"**Nền tảng / kích thước:** {screen.platform} · {screen.width}px", "", f"**Mục đích:** {item.get('purpose') or 'Chưa có mô tả riêng trong UI spec.'}", ""])
        if item.get("layout"):
            markdown.extend([f"**Bố cục:** {item['layout']}", ""])
        add_list("Thành phần và nội dung hiển thị", merge_lists(
            item.get("sections"), item.get("data"), defaults.get("content_outline"),
            defaults.get("visible_copy"), item.get("must_show"),
        ))
        add_list("Hành động người dùng", merge_lists(defaults.get("default_actions")))
        navigation = item.get("navigation")
        if isinstance(navigation, list):
            destinations = [screen_names.get(str(target), str(target)) for target in navigation if str(target).strip()]
            add_list("Điều hướng", destinations)
        add_list("Trạng thái màn hình", merge_lists(state.get("states"), state_refs.get("states")))
        add_list("Nội dung hiển thị theo điều kiện", merge_lists(state.get("conditional_content"), state_refs.get("conditional_content")))
        refs = state_refs.get("state_spec") if isinstance(state_refs, dict) else None
        if isinstance(refs, list) and refs:
            markdown.extend(["### Kích hoạt và hiển thị ban đầu", ""])
            for ref in refs:
                if isinstance(ref, dict):
                    trigger = ref.get("trigger", "")
                    content = ", ".join(str(v) for v in ref.get("content", []) if v)
                    visibility = {"visible": "hiển thị", "hidden": "ẩn", "not_applicable": "không áp dụng"}.get(str(ref.get("initial_visibility")), "chưa xác định")
                    markdown.append(f"- **Khi:** {trigger or 'Chưa mô tả'} · **Ban đầu:** {visibility}" + (f" · **Nội dung:** {content}" if content else ""))
            markdown.append("")
        add_list("Quy tắc giao diện", merge_lists(state.get("rules"), state_refs.get("rules")))
        scenarios = state.get("state_scenarios")
        if isinstance(scenarios, list):
            scenario_lines = []
            for scenario in scenarios:
                if isinstance(scenario, dict):
                    parts = [str(scenario.get("name") or "Kịch bản")]
                    clicks = scenario.get("click_selectors")
                    if isinstance(clicks, list) and clicks:
                        parts.append("thao tác: " + " → ".join(str(value) for value in clicks))
                    visible = scenario.get("visible")
                    if isinstance(visible, list) and visible:
                        parts.append("hiển thị: " + ", ".join(str(value) for value in visible))
                    hidden = scenario.get("hidden")
                    if isinstance(hidden, list) and hidden:
                        parts.append("ẩn: " + ", ".join(str(value) for value in hidden))
                    scenario_lines.append("; ".join(parts))
            add_list("Kịch bản kiểm tra trạng thái", scenario_lines)

    design = spec.get("design") if isinstance(spec.get("design"), dict) else {}
    markdown.extend(["## Hướng thiết kế chung", ""])
    for label, key in (("Định hướng", "style_direction"), ("Mood", "mood"), ("Font tiêu đề", "heading_font"), ("Font nội dung", "body_font"), ("Bo góc", "radius"), ("Mật độ", "density")):
        value = spec.get(key) if key == "style_direction" else design.get(key)
        if value:
            markdown.append(f"- **{label}:** {value}")
    add_list("Nên làm", design.get("do"))
    add_list("Tránh", design.get("dont"))
    markdown.append("")

    brief = await store.get_design_brief(session_id)
    contract = brief.selected_style_contract if isinstance(brief.selected_style_contract, dict) else {}
    palette = contract.get("palette") if isinstance(contract.get("palette"), dict) else {}
    tokens = {
        "format": "agent-designer-style-contract-v1",
        "name": brief.style_name or contract.get("name") or "Agent Designer style contract",
        "description": "Thông số định hướng thị giác đã chọn. Đây chưa phải bộ design token đầy đủ được trích xuất từ CSS của mọi màn hình.",
        "source": "Approved Agent Designer style selection",
        "color": palette,
        "typography": {"fontFamily": {"heading": contract.get("heading_font") or "", "body": contract.get("body_font") or ""}},
        "shape": {"radius": contract.get("radius") or "", "density": contract.get("density") or ""},
        "visual_language": {key: contract[key] for key in ("composition", "surface_language", "shape_language", "hero_placement") if contract.get(key)},
        "not_included": ["CSS variables resolved from rendered screens", "Spacing and sizing scale", "Component-level tokens", "Per-screen overrides"],
    }
    tokens_available = bool(palette or contract.get("heading_font") or contract.get("body_font"))
    return {
        "screens": [
            {"id": screen.id, "name": screen.name, "platform": screen.platform, "width": screen.width}
            for screen in screens
        ],
        "spec_markdown": "\n".join(markdown).strip(),
        "design_tokens": tokens if tokens_available else None,
        "task_draft": task_draft,
    }


@router.post("/sessions/{session_id}/huly/issues", status_code=status.HTTP_201_CREATED)
async def create_huly_issue(request: Request, session_id: UUID, body: HulyIssueCreate) -> dict[str, Any]:
    store = request.app.state.store
    session = await store.get_session(session_id)
    if session is None:
        raise not_found("Session")
    run = await store.get_run(body.run_id)
    if run is None or run.session_id != session_id:
        raise not_found("Run")
    if run.stage != "finished" or run.status != "completed":
        raise AppError(
            "DESIGN_NOT_APPROVED",
            "Design is not approved",
            "Approve the final design before creating a Huly issue.",
            409,
        )
    screens = await store.list_screens(session_id, with_html=True)
    if not screens:
        raise AppError("DESIGN_SCREENS_MISSING", "No screens to hand off", "The approved session has no saved screens.", 409)
    projects = await huly_client.list_projects(request.app.state.settings)
    if not any(body.project_id in {project["id"], project["identifier"], project["huly_id"]} for project in projects):
        raise AppError("HULY_PROJECT_INVALID", "Huly project is unavailable", "Select a project from the current Huly workspace.", 422)
    description = "\n\n".join(
        [
            f"## Mục tiêu\n{body.objective.strip()}",
            f"## Hiện trạng\n{body.current_state.strip()}",
            f"## Giải pháp\n{body.solution.strip()}",
            f"## Tiêu chí chấp nhận (AC)\n{body.acceptance_criteria.strip()}",
            "## Màn hình thiết kế đã duyệt\n" + "\n".join(f"- {screen.name} ({screen.platform})" for screen in screens),
            f"Nguồn: Agent Designer · Session `{session_id}` · Run `{run.id}`",
        ]
    )
    huly_project = next(project for project in projects if body.project_id in {project["id"], project["identifier"], project["huly_id"]})
    design = await design_context(store, session)
    upload_root = Path("/tmp/huly-uploads")
    upload_root.mkdir(parents=True, exist_ok=True)
    attachments: list[dict[str, str]] = []
    with tempfile.TemporaryDirectory(prefix="approved-design-", dir=upload_root) as temp_dir:
        output_dir = Path(temp_dir)
        for index, screen in enumerate(screens, start=1):
            rendered = await request.app.state.renderer.render(
                await inline_assets(screen.html or "", session.project_id),
                width=screen.width,
                screenshot="png",
                lint=False,
                theme_css=design.theme_css,
                fonts=design.fonts,
            )
            if not rendered.image:
                raise AppError("HULY_EXPORT_FAILED", "Could not export screen PNG", f"PNG export failed for {screen.name}.", 502)
            filename = f"{index:02d}-{screen.id}.png"
            file_path = output_dir / filename
            file_path.write_bytes(rendered.image or b"")
            attachments.append({"filename": filename, "content_type": "image/png", "file_path": str(file_path), "description": f"Màn hình {index}: {screen.name}"})

        artifact_preview = await get_huly_attachment_preview(request, session_id, run.id)
        spec_path = output_dir / "ui-spec.md"
        spec_path.write_text(artifact_preview["spec_markdown"], encoding="utf-8")
        attachments.append({"filename": spec_path.name, "content_type": "text/markdown", "file_path": str(spec_path), "description": "Đặc tả UI của toàn bộ màn hình đã duyệt"})
        if artifact_preview["design_tokens"] is not None:
            tokens_path = output_dir / "design-tokens.json"
            tokens_path.write_text(json.dumps(artifact_preview["design_tokens"], ensure_ascii=False, indent=2), encoding="utf-8")
            attachments.append({"filename": tokens_path.name, "content_type": "application/json", "file_path": str(tokens_path), "description": "Design tokens/style contract đã duyệt"})

        result = await huly_client.create_issue(
            request.app.state.settings,
            project=huly_project["identifier"],
            title=body.title.strip(),
            description=description,
            attachments=attachments,
        )
    return result


@router.post("/attachments/parse", response_model=AttachmentParseResult)
async def parse_attachment(
    request: Request,
    body: AttachmentParseRequest,
    project_id: str | None = Query(None, description="Project that owns uploaded images"),
) -> AttachmentParseResult:
    project_id = (project_id or "").strip() or None
    if not auth_disabled() or project_id:
        require_project(project_id, DESIGNER_ROLE)
    try:
        raw = base64.b64decode(body.data_base64, validate=True)
    except (binascii.Error, ValueError) as error:
        raise AppError("ATTACHMENT_INVALID", "Invalid attachment", "Invalid base64 data.", 422) from error
    if len(raw) > MAX_ATTACHMENT_BYTES:
        raise AppError(
            "ATTACHMENT_TOO_LARGE",
            "Attachment is too large",
            "Use a file smaller than 8 MB.",
            413,
        )
    suffix = Path(body.name).suffix.casefold()
    safe_name = Path(body.name).name
    try:
        if suffix == ".json":
            parsed = parse_json_document(raw)
            return AttachmentParseResult(
                name=safe_name,
                kind="json",
                markdown=parsed.markdown,
                truncated=parsed.truncated,
            )
        if suffix in {".bpmn", ".bpmn2"}:
            parsed = parse_bpmn_document(raw)
            return AttachmentParseResult(
                name=safe_name,
                kind="bpmn",
                markdown=parsed.markdown,
                truncated=parsed.truncated,
            )
        if suffix in TEXT_SUFFIXES:
            parsed = parse_text_document(raw, suffix)
            return AttachmentParseResult(
                name=safe_name,
                kind="text",
                markdown=parsed.markdown,
                truncated=parsed.truncated,
            )
        if suffix in IMAGE_SUFFIXES:
            mime_type = IMAGE_SUFFIXES[suffix]
            asset_id = _standard_asset_id(safe_name, suffix)
            save_asset(project_id, asset_id, raw)
            analysis = await request.app.state.llm.structured(
                prompt=(
                    "Inspect this attached image as input to a UI design agent. "
                    "Extract visible copy, UI elements, layout or product requirements, "
                    "important states, and ambiguities. Also classify the image's semantic role "
                    "(logo, mascot, hero illustration, photo, background, icon, or screenshot), "
                    "dominant colors, background type, safest contain/cover behavior, useful "
                    "screen placement, placement rationale, and safe padding around the subject. "
                    "Be faithful to visible evidence; do not invent requirements."
                ),
                schema=ImageAttachmentAnalysis,
                image_bytes=raw,
                image_mime_type=mime_type,
            )
            markdown = [
                "# Attached image context",
                f"- File: `{safe_name}`",
                f"- Summary: {analysis.summary}",
                "",
                "## Visible text",
                *[f"- {item}" for item in analysis.visible_text],
                "",
                "## UI elements",
                *[f"- {item}" for item in analysis.ui_elements],
                "",
                "## Requirements and states",
                *[f"- {item}" for item in analysis.requirements_and_states],
                "",
                "## Asset placement guidance",
                f"- Role: {analysis.visual_role}",
                f"- Dominant colors: {', '.join(analysis.dominant_colors) or 'unknown'}",
                f"- Background: {analysis.background_type}",
                f"- Fit: {analysis.recommended_fit}",
                f"- Placement: {analysis.recommended_placement}",
                f"- Reason: {analysis.placement_reason or 'Use screen hierarchy and current layout.'}",
                f"- Safe padding: {analysis.safe_padding_percent}%",
            ]
            size = _image_size(raw)
            width, height = size or (None, None)
            return AttachmentParseResult(
                name=safe_name,
                kind="image",
                markdown="\n".join(markdown).strip(),
                asset=ExactAsset(
                    id=asset_id,
                    name=safe_name,
                    mime_type=mime_type,
                    width=width,
                    height=height,
                    aspect_ratio=(round(width / height, 6) if width and height else None),
                    visual_role=analysis.visual_role,
                    dominant_colors=analysis.dominant_colors,
                    background_type=analysis.background_type,
                    recommended_fit=("cover" if analysis.recommended_fit.casefold() == "cover" else "contain"),
                    recommended_placement=analysis.recommended_placement,
                    placement_reason=analysis.placement_reason,
                    safe_padding_percent=analysis.safe_padding_percent,
                ),
            )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise AppError(
            "ATTACHMENT_PARSE_FAILED",
            "Attachment could not be parsed",
            str(error),
            422,
        ) from error
    raise AppError(
        "ATTACHMENT_TYPE_UNSUPPORTED",
        "Unsupported attachment type",
        "Supported: PNG, JPG, SVG, WEBP, TXT, Markdown, JSON, CSV, YAML, BPMN.",
        415,
    )


@router.post("/sessions", response_model=SessionView, status_code=status.HTTP_201_CREATED)
async def create_session(request: Request, body: SessionCreate) -> SessionView:
    project_id = (body.project_id or "").strip() or None
    if not auth_disabled() or project_id:
        require_project(project_id, DESIGNER_ROLE)
    llm = getattr(request.app.state, "llm", None)
    if llm is not None:
        asyncio.create_task(llm.prewarm())
    return await request.app.state.store.create_session(
        body.title.strip() or "Untitled session",
        project_id=project_id,
        created_by=current_username(),
        source="ui",
    )


@router.get("/sessions", response_model=list[SessionView])
async def list_sessions(
    request: Request,
    project_id: str | None = Query(None, description="Platform project; required when auth is on"),
) -> list[SessionView]:
    pid = (project_id or "").strip() or None
    if not auth_disabled() or pid:
        require_project(pid)
    return await request.app.state.store.list_sessions(pid)


@router.delete("/sessions/{session_id}")
async def delete_session(request: Request, session_id: UUID) -> dict[str, str]:
    await request.app.state.runs.delete_session(session_id)
    return {"status": "deleted"}


@router.get("/sessions/{session_id}/runs", response_model=list[RunView])
async def list_runs(request: Request, session_id: UUID) -> list[RunView]:
    if not await request.app.state.store.get_session(session_id):
        raise not_found("Session")
    return await request.app.state.store.list_runs(session_id)


@router.post(
    "/sessions/{session_id}/messages",
    response_model=RunView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_message(
    request: Request, session_id: UUID, body: MessageCreate
) -> RunView:
    if not await request.app.state.store.get_session(session_id):
        raise not_found("Session")
    run = await request.app.state.store.create_run(
        session_id,
        RunCreate(
            prompt=body.content,
            screen_name=body.screen_name_hint or "Screen",
            platform=body.platform_hint,
            style_name=body.style_hint,
            library_ids=body.library_ids,
            assets=body.assets,
        ),
        triggered_by=current_user_id(),
    )
    await request.app.state.events.emit(
        run.id,
        run.revision,
        "run.created",
        {"stage": run.stage, "message": "Message accepted"},
    )
    request.app.state.runs.start(run.id)
    return run


@router.post(
    "/sessions/{session_id}/runs",
    response_model=RunView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_run(request: Request, session_id: UUID, body: RunCreate) -> RunView:
    if not await request.app.state.store.get_session(session_id):
        raise not_found("Session")
    run = await request.app.state.store.create_run(session_id, body, triggered_by=current_user_id())
    await request.app.state.events.emit(
        run.id,
        run.revision,
        "run.created",
        {"stage": run.stage, "message": "Request accepted"},
    )
    request.app.state.runs.start(run.id)
    return run


@router.get("/runs/{run_id}", response_model=RunView)
async def get_run(request: Request, run_id: UUID) -> RunView:
    run = await request.app.state.store.get_run(run_id)
    if run is None:
        raise not_found("Run")
    return run


@router.get("/runs/{run_id}/artifacts")
async def get_run_artifacts(request: Request, run_id: UUID) -> dict[str, Any]:
    run = await request.app.state.store.get_run(run_id)
    if run is None:
        raise not_found("Run")
    return await request.app.state.store.list_run_data(run.id, run.revision)


@router.get("/runs/{run_id}/events")
async def stream_events(
    request: Request,
    run_id: UUID,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    if not await request.app.state.store.get_run(run_id):
        raise not_found("Run")
    after = int(last_event_id) if last_event_id and last_event_id.isdigit() else 0
    return StreamingResponse(
        request.app.state.events.subscribe(run_id, after),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/runs/{run_id}/event-history")
async def event_history(request: Request, run_id: UUID) -> list[dict[str, Any]]:
    """Return persisted workflow and debug events for the local Logs page."""
    if not await request.app.state.store.get_run(run_id):
        raise not_found("Run")
    events = await request.app.state.store.list_events(run_id)
    return [event.model_dump(mode="json", by_alias=True) for event in events]


@router.post("/runs/{run_id}/reviews", status_code=status.HTTP_202_ACCEPTED)
async def review_run(request: Request, run_id: UUID, body: ReviewCreate) -> dict[str, str]:
    await request.app.state.runs.review(run_id, body)
    return {"status": "accepted"}


@router.post("/runs/{run_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_run(request: Request, run_id: UUID) -> dict[str, str]:
    await request.app.state.runs.retry(run_id)
    return {"status": "accepted"}


@router.post("/runs/{run_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_run(request: Request, run_id: UUID) -> dict[str, str]:
    await request.app.state.runs.cancel(run_id)
    return {"status": "accepted"}
