"""WF nodes — extractor, flags, discussion."""

from __future__ import annotations

import json
import re
from pathlib import Path

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from agents.sub_graph.shared.constants import PENDING_SEMANTIC
from agents.sub_graph.shared.context_trim import (
    trim_business_rules,
    trim_feature_definition,
    trim_open_items,
    trim_workflow,
)
from agents.sub_graph.shared.semantic_flags import (
    SemanticFlagsEvaluation,
    empty_evaluation_response,
)
from agents.sub_graph.shared.discussion_tools import discussion_tools
from agents.sub_graph.workflow.schemas import WorkflowData
from callbacks.filter_history import create_history_filter
from callbacks.inject_context import insert_context
from config.model_config import base_model
from utils.utils import parse_str_to_dict

from google.adk.workflow import Workflow

from agents.sub_graph.workflow.nodes.flags_checker.logic import check_wf_flags_logic
from agents.ba_agent.section_gate import (
    mark_downstream_needs_review,
    record_package_change_review,
)


def _preserve_omitted_workflows(previous: str, updated: str) -> tuple[str, list[str]]:
    """Keep existing WF blocks if a model-generated replacement accidentally omits them."""
    heading = re.compile(r"(?im)^#{1,3}[^\n]*\b(WF-[A-Z0-9]+(?:-[A-Z0-9]+)*-\d+)\b[^\n]*(?:\n|$)")

    def blocks(text: str) -> dict[str, str]:
        matches = list(heading.finditer(text))
        result: dict[str, str] = {}
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            result[match.group(1).upper()] = text[match.start():end].strip()
        return result

    old_blocks = blocks(previous)
    new_blocks = blocks(updated)
    omitted = [workflow_id for workflow_id in old_blocks if workflow_id not in new_blocks]
    if omitted:
        updated = updated.rstrip() + "\n\n" + "\n\n".join(
            old_blocks[workflow_id] for workflow_id in omitted
        )
    return updated, omitted


async def wf_merge_callback(
    callback_context: CallbackContext, llm_response: LlmResponse
) -> None:
    response_text = "\n".join(
        str(part.text or "")
        for part in (llm_response.content.parts if llm_response.content else [])
        if getattr(part, "text", None)
    )
    parsed_dict = parse_str_to_dict(response_text)
    if not parsed_dict:
        return
    try:
        parsed = WorkflowData.model_validate(parsed_dict)
    except Exception:
        return
    state = callback_context.state
    artifacts = dict(state.get("artifacts", {}))
    wf = dict(artifacts.get("workflow", {}))
    previous_content = str(wf.get("workflow_md_content") or "")
    preserved: list[str] = []
    content_changed = False
    if parsed.workflow_md_content and parsed.workflow_md_content.strip():
        updated_content, preserved = _preserve_omitted_workflows(
            previous_content, parsed.workflow_md_content.strip()
        )
        wf["workflow_md_content"] = updated_content
        content_changed = updated_content != previous_content
    if parsed.artifact_id:
        wf["artifact_id"] = parsed.artifact_id
    if parsed.version:
        wf["version"] = parsed.version
    if parsed.workflow_md_path:
        wf["workflow_md_path"] = parsed.workflow_md_path
    artifacts["workflow"] = wf
    mapping_changed = False
    if parsed.handoff_mappings:
        from managers.control.bpmn import parse_workflow_steps

        fd = dict(artifacts.get("feature_definition") or {})
        continuity = dict(fd.get("continuity") or {})
        handoffs = [dict(item) for item in continuity.get("handoffs") or []]
        parsed_ids = parse_workflow_steps(wf.get("workflow_md_content") or "").get(
            "end_state_ids", []
        )
        for mapping in parsed.handoff_mappings:
            index = mapping.handoff_index - 1
            requested_id = mapping.workflow_end_state_id.strip()
            matches = [
                state_id for state_id in parsed_ids
                if str(state_id).strip().casefold() == requested_id.casefold()
            ]
            if index >= len(handoffs) or len(matches) != 1:
                continue
            canonical_id = str(matches[0]).strip()
            if handoffs[index].get("workflow_end_state_id") != canonical_id:
                handoffs[index]["workflow_end_state_id"] = canonical_id
                mapping_changed = True

        if mapping_changed:
            continuity["handoffs"] = handoffs
            if len(handoffs) == 1:
                continuity["workflow_end_state_id"] = handoffs[0]["workflow_end_state_id"]
            fd["continuity"] = continuity
            artifacts["feature_definition"] = fd

    state["artifacts"] = artifacts
    if content_changed or mapping_changed:
        mark_downstream_needs_review(state, "workflow")
        state["_ba_workflow_changed_turn"] = state.get("current_turn")
        if preserved:
            state["_ba_preserved_workflows"] = preserved


async def wf_extractor_inject(
    callback_context: CallbackContext, llm_request: LlmRequest
) -> None:
    wf = trim_workflow(callback_context.state.get("artifacts", {}).get("workflow", {}))
    br = trim_business_rules(
        callback_context.state.get("artifacts", {}).get("business_rules", [])
    )
    fd = trim_feature_definition(
        callback_context.state.get("artifacts", {}).get("feature_definition", {})
    )
    insert_context(
        llm_request,
        f"=== FEATURE DEFINITION ===\n{json.dumps(fd, ensure_ascii=False)}\n\n"
        f"=== BUSINESS RULES ===\n{json.dumps(br, ensure_ascii=False)}\n\n"
        f"=== CURRENT WORKFLOW ===\n{json.dumps(wf, ensure_ascii=False)}",
    )


async def wf_flags_before(
    callback_context: CallbackContext, llm_request: LlmRequest
) -> LlmResponse | None:
    flags = check_wf_flags_logic(callback_context)
    pending = {k: v for k, v in flags.items() if v.get("status") == PENDING_SEMANTIC}
    if not pending:
        return empty_evaluation_response()
    wf = trim_workflow(callback_context.state.get("artifacts", {}).get("workflow", {}))
    fd = trim_feature_definition(
        callback_context.state.get("artifacts", {}).get("feature_definition", {})
    )
    insert_context(
        llm_request,
        f"=== FEATURE DEFINITION (actors) ===\n{json.dumps(fd, ensure_ascii=False)}\n\n"
        f"=== WORKFLOW ===\n{json.dumps(wf, ensure_ascii=False)}\n\n"
        f"=== PENDING ===\n{json.dumps(pending, ensure_ascii=False)}",
    )
    return None


async def wf_flags_after(
    callback_context: CallbackContext, llm_response: LlmResponse
) -> None:
    from agents.sub_graph.shared.semantic_flags import apply_semantic_flag_evaluations

    text = "\n".join(
        str(part.text or "")
        for part in (llm_response.content.parts if llm_response.content else [])
        if getattr(part, "text", None)
    )
    apply_semantic_flag_evaluations(callback_context.state, "wf", text)


async def wf_discussion_inject(
    callback_context: CallbackContext, llm_request: LlmRequest
) -> None:
    from agents.sub_graph.shared.constants import (
        FAIL,
        MISSING_VALUE,
        NOT_YET_REASONABLE,
        PENDING_SEMANTIC,
        WARNING,
    )


    wf = trim_workflow(callback_context.state.get("artifacts", {}).get("workflow", {}))
    fd = trim_feature_definition(
        callback_context.state.get("artifacts", {}).get("feature_definition", {})
    )
    br = trim_business_rules(
        callback_context.state.get("artifacts", {}).get("business_rules", [])
    )
    state = callback_context.state
    if state.get("_ba_workflow_changed_turn") == state.get("current_turn"):
        if (state.get("sections_status") or {}).get("bpmn_reference") == "needs_review":
            from managers.control.bpmn import ensure_bpmn_artifact

            ensure_bpmn_artifact(state, force=True)
        record_package_change_review(state, "workflow")
        if state.get("_ba_preserved_workflows"):
            review = dict(state.get("_ba_change_review") or {})
            review["preserved_workflows"] = state["_ba_preserved_workflows"]
            state["_ba_change_review"] = review
    flags = callback_context.state.get("flags", {}).get("wf", {})
    actionable = {MISSING_VALUE, FAIL, PENDING_SEMANTIC, NOT_YET_REASONABLE, WARNING}
    checklist = [
        {
            "priority": i + 1,
            "flag_id": fid,
            "status": info.get("status"),
            "required": fid in {"WF-F1", "WF-F2", "WF-F4"},
            "message": info.get("detail") or info.get("name"),
        }
        for i, (fid, info) in enumerate(flags.items())
        if str(info.get("status", "")).upper() in actionable
    ]
    insert_context(
        llm_request,
        f"=== FEATURE DEFINITION ===\n{json.dumps(fd, ensure_ascii=False, indent=2)}\n\n"
        f"=== BUSINESS RULES ===\n{json.dumps(br, ensure_ascii=False, indent=2)}\n\n"
        f"=== WORKFLOW ===\n{json.dumps(wf, ensure_ascii=False, indent=2)}\n\n"
        f"=== CHECKLIST ===\n{json.dumps(checklist, ensure_ascii=False, indent=2)}\n\n"
        f"=== PACKAGE CHANGE REVIEW (deterministic checks; source of truth) ===\n{json.dumps(callback_context.state.get('_ba_change_review') or {}, ensure_ascii=False, indent=2)}\n\n"
        f"=== OPEN QUESTIONS ===\n{json.dumps(trim_open_items(callback_context.state.get('open_questions', [])), ensure_ascii=False)}\n\n"
        f"=== ASSUMPTIONS ===\n{json.dumps(trim_open_items(callback_context.state.get('assumptions', [])), ensure_ascii=False)}\n\n"
        f"=== OQ SYNC RESULT ===\n{json.dumps(callback_context.state.get('_oq_sync_result') or {}, ensure_ascii=False)}\n"
        f"=== EXPLICIT OQ ANSWER IDS THIS TURN ===\n{json.dumps(callback_context.state.get('_oq_user_explicit_answer_ids') or [], ensure_ascii=False)}",
    )


async def wf_change_review_after(
    callback_context: CallbackContext, llm_response: LlmResponse
) -> None:
    """Append authoritative package and explicit OQ synchronization status."""
    state = callback_context.state
    review = state.get("_ba_change_review") or {}
    review_is_current = (
        review.get("edited_section") == "workflow"
        and review.get("turn") == state.get("current_turn")
    )
    oq_result = state.get("_oq_sync_result") or {}
    explicit_oq_ids = state.get("_oq_user_explicit_answer_ids") or []
    oq_sync_is_current = bool(explicit_oq_ids) and oq_result.get("turn") == state.get("current_turn")
    if not llm_response.content or not llm_response.content.parts:
        return

    lines: list[str] = []
    if review_is_current:
        failed = review.get("failed_checks") or []
        warnings = review.get("warnings") or []
        lines.append("\n\n**Deterministic Workflow package review:**")
        if failed:
            lines.append("Required checks remain; do not advance or validate:")
            for finding in failed:
                lines.append(
                    f"- **{finding.get('check_id', '')} {finding.get('name', '')}:** "
                    f"{finding.get('detail', '')}"
                )
                if finding.get("suggestion"):
                    lines.append(f"  Suggestion: {finding['suggestion']}")
        else:
            lines.append("No blocking package findings were detected. This is not a completed Validate run.")
        if warnings:
            lines.append("Warnings:")
            for finding in warnings:
                lines.append(
                    f"- **{finding.get('check_id', '')} {finding.get('name', '')}:** "
                    f"{finding.get('detail', '')}"
                )
        preserved = review.get("preserved_workflows") or []
        if preserved:
            lines.append("Other workflow blocks preserved: " + ", ".join(f"`{item}`" for item in preserved) + ".")

    if oq_sync_is_current:
        questions = {
            str(item.get("id") or "").upper(): item
            for item in (state.get("open_questions") or [])
            if isinstance(item, dict)
        }
        lines.append("\n\n**Open Question sync status (from saved state):**")
        for oq_id in explicit_oq_ids:
            item = questions.get(str(oq_id).upper(), {})
            status = str(item.get("status") or "open").lower()
            sync_status = str(item.get("sync_status") or "").lower()
            label = (
                "resolved and synchronized"
                if status == "resolved" and sync_status == "verified"
                else f"status={status}, sync={sync_status or 'not verified'}"
            )
            lines.append(f"- {oq_id}: {label}")

    if not lines:
        return
    llm_response.content.parts[0].text = (
        (llm_response.content.parts[0].text or "") + "\n".join(lines)
    )


_wf_prompt = (
    Path(__file__).resolve().parent / "nodes/flags_checker/prompt.md"
)
_wf_flags_instruction = (
    _wf_prompt.read_text(encoding="utf-8")
    if _wf_prompt.exists()
    else "Đánh giá WF-F2/WF-F4. Trả SemanticFlagsEvaluation JSON. KHÔNG gọi tool."
)

_wf_extractor = LlmAgent(
    name="wf_extractor",
    model=base_model,
    instruction=(
        "Trích xuất/cập nhật workflow markdown NGẮN GỌN cho **một** luồng/story tại một thời điểm. "
        "Artifact workflow có thể chứa NHIỀU workflow. Khi sửa một WF-ID, phải giữ nguyên đầy đủ mọi WF-ID khác và mọi bước/BR Ref hiện có; không được thay toàn bộ artifact bằng riêng luồng đang sửa. Trả lại toàn bộ nội dung workflow sau khi merge. "
        "Main Flow và Exception Flows dùng bảng cột: "
        "`Step | Actor | Action | BR | Gateway` "
        "(Gateway = XOR hoặc AND hoặc để trống). "
        "Actors phải khớp FD actors (sẽ thành lanes). "
        "Main Flow ≥3 bước, Exception Flows, End States. "
        "Không lặp lại toàn bộ BR — chỉ tham chiếu BR-XX."
        "When the user confirms a handoff-to-End-State mapping, return it in handoff_mappings with a 1-based handoff_index and the exact Workflow End State ID. "
        "Never copy exit_state_id into workflow_end_state_id unless that exact ID exists in Workflow End States. Do not guess ambiguous mappings."
    ),
    output_schema=WorkflowData,
    before_model_callback=[
        create_history_filter(
            visible_authors=["wf_discussion"],
            process_latest_message_only=True,
        ),
        wf_extractor_inject,
    ],
    after_model_callback=wf_merge_callback,
)

_wf_flags = LlmAgent(
    name="wf_flags_checker",
    model=base_model,
    instruction=_wf_flags_instruction,
    output_schema=SemanticFlagsEvaluation,
    before_model_callback=wf_flags_before,
    after_model_callback=wf_flags_after,
)

_wf_discussion = LlmAgent(
    name="wf_discussion",
    model=base_model,
    instruction=(
        "Bạn là BA Agent section Workflow. "
        "Xác nhận Main Flow / Exception / End States cho **một** luồng tại một thời điểm. "
        "Bảng bước dùng cột `Step | Actor | Action | BR | Gateway` "
        "với Gateway XOR (nhánh độc quyền) hoặc AND (song song) hoặc để trống. "
        "Actors phải khớp Feature Definition (không invent Management/Supplier). "
        "Hỏi làm rõ theo CHECKLIST (ưu tiên required=true). "
        "Không dùng mã flag kỹ thuật (WF-F1…) trong câu trả lời User. "
        "Nhắc OQ/Assumptions nếu có. "
        "After every Workflow change, read PACKAGE CHANGE REVIEW (deterministic checks) and review all existing workflows, BR references, User Stories, BPMN, and UI Touchpoints. Report concrete findings and affected sections. If failed_checks is non-empty, do not claim the package is okay and do not ask the user to Validate; guide them to fix the listed findings. Only when failed_checks is empty may you say the package is ready for the user to run Validate. Never claim that Validate has already run. "
        "When FEATURE DEFINITION.continuity.has_successor is true, include an explicit End States table with IDs and describe each distinct terminal branch separately. Every continuity.handoffs record must match exactly one End State; do not collapse branches or invent destinations. Ask the user to resolve missing/mismatched branches before completion. Keep successor detailed screens outside this feature's scope. "
        "Keep continuity `exit_state_id` (handoff outcome identity) separate from `workflow_end_state_id` (the referenced ID in the Workflow End States). Preserve user-provided End State IDs; never rename them or overwrite `exit_state_id` to force a match. Use an explicit `workflow_end_state_id` mapping when available, and only report WF-F6 from the deterministic package review. "
        "If the user confirms a handoff-to-End-State mapping, include it in the extractor's `handoff_mappings` output so it is saved into Feature Definition.continuity.handoffs. Only map to an exact ID present in Workflow End States; never claim a Feature Definition mapping was saved unless that structured field was returned and persisted. If the destination is ambiguous, ask the user instead of guessing. "
        "You have tools: wiki first (`wiki.search_pages` / `wiki.fetch_page`); "
        "do NOT `web_search` for FD scope_out / non-goals / unrelated browsing; "
        "`web_search` only after wiki miss for in-package clarification; "
        "`preview_bpmn` after actor-step rows. Never approve/ingest/propose wiki."
    ),
    tools=discussion_tools(include_preview_bpmn=True),
    before_model_callback=[
        create_history_filter(visible_authors=["wf_discussion"], max_turns=3),
        wf_discussion_inject,
    ],
    after_model_callback=wf_change_review_after,
)

# WF: skip OQ detector (already captured in FD) — 3 LLM calls not 4.
wf_subgraph = Workflow(
    name="workflow_workflow",
    edges=[
        ("START", _wf_extractor),
        (_wf_extractor, _wf_flags),
        (_wf_flags, _wf_discussion),
    ],
)
