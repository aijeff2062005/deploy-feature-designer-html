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
from agents.sub_graph.shared.choices_prompt import USER_CHOICES_INSTRUCTION
from agents.sub_graph.workflow.schemas import WorkflowData, WorkflowPatch
from callbacks.filter_history import create_history_filter
from callbacks.inject_context import insert_context
from config.model_config import base_model, fast_model
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


def _sanitize_workflow_markdown(text: str) -> str:
    """Normalize LaTeX arrow artifacts, strip leaked FD context headers, and ensure Main Flow heading."""
    cleaned = re.sub(r"\$\s*(?:\\rightarrow|[\r\s]*ightarrow)\s*\$", "→", text)
    cleaned = re.sub(r"(?:\\rightarrow|\bightarrow\b)", "→", cleaned)
    # Strip accidentally copied Feature Definition prompt block at top of workflow markdown.
    cleaned = re.sub(
        r"(?is)^\s*===\s*FEATURE DEFINITION\s*===.*?(?=\n#{1,3}\s+|\Z)",
        "",
        cleaned,
    ).lstrip()
    # If there is a Step|Actor|Action table without a Main Flow heading, insert ## Main Flow above it.
    if not re.search(r"(?im)^#{1,3}\s+.*main\s+flow", cleaned):
        table_match = re.search(
            r"(?im)^(\|\s*Step\s*\|\s*Actor\s*\|[^\n]*\n)", cleaned
        )
        if table_match:
            before = cleaned[: table_match.start()]
            # Replace an adjacent non-standard heading (e.g. ### WF-F6: ...) if right above the table.
            before = re.sub(r"(?im)^#{1,3}\s+[^\n]+\n\s*\Z", "", before)
            cleaned = f"{before}## Main Flow\n\n{cleaned[table_match.start():]}".lstrip()
    return cleaned


def _apply_workflow_patches(
    previous: str, patches: list[WorkflowPatch]
) -> tuple[str, bool]:
    """Apply incremental find/replace or section-append patches onto existing workflow markdown."""
    updated = previous
    changed = False
    for patch in patches:
        replacement = _sanitize_workflow_markdown(patch.replace or "")
        if patch.find:
            target = patch.find
            if target in updated:
                next_text = updated.replace(target, replacement, 1)
                if next_text != updated:
                    updated = next_text
                    changed = True
                continue
        if patch.append_to_section and replacement.strip():
            section_name = re.escape(patch.append_to_section.strip())
            heading_re = re.compile(
                rf"(?im)^(#{{1,3}}\s+[^\n]*{section_name}[^\n]*\n)"
            )
            match = heading_re.search(updated)
            if match:
                next_heading = re.search(
                    r"(?m)^#{1,3}\s+", updated[match.end() :]
                )
                insert_at = (
                    match.end() + next_heading.start()
                    if next_heading
                    else len(updated)
                )
                before = updated[:insert_at].rstrip()
                after = updated[insert_at:].lstrip("\n")
                addition = replacement.strip()
                updated = (
                    f"{before}\n{addition}\n\n{after}"
                    if after
                    else f"{before}\n{addition}\n"
                )
                changed = True
    return updated, changed


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
    if parsed.workflow_patches and previous_content.strip():
        patched_content, patch_changed = _apply_workflow_patches(
            previous_content, parsed.workflow_patches
        )
        if patch_changed:
            wf["workflow_md_content"] = patched_content
            content_changed = True
    elif parsed.workflow_md_content and parsed.workflow_md_content.strip():
        from managers.control.bpmn import _heading_section, parse_workflow_steps

        sanitized_md = _sanitize_workflow_markdown(parsed.workflow_md_content.strip())
        updated_content, preserved = _preserve_omitted_workflows(
            previous_content, sanitized_md
        )
        old_parsed = parse_workflow_steps(previous_content)
        new_parsed = parse_workflow_steps(updated_content)
        if len(old_parsed.get("steps") or []) >= 3 and len(new_parsed.get("steps") or []) == 0:
            # Guard against destructive full-content overwrites that lose Main Flow steps.
            updated_content = previous_content
        else:
            prev_exc = _heading_section(previous_content, "Exception Flows")
            if prev_exc and prev_exc.strip() and "exception" not in updated_content.lower():
                exc_block = f"## Exception Flows\n{prev_exc.strip()}"
                end_match = re.search(r"(?im)^#{1,3}\s+.*end\s+states", updated_content)
                if end_match:
                    updated_content = (
                        updated_content[: end_match.start()].rstrip()
                        + f"\n\n{exc_block}\n\n"
                        + updated_content[end_match.start() :]
                    )
                else:
                    updated_content = updated_content.rstrip() + f"\n\n{exc_block}\n"
        wf["workflow_md_content"] = updated_content
        content_changed = updated_content != previous_content
    if parsed.artifact_id:
        aid = parsed.artifact_id.strip()
        if 1 <= len(aid) <= 64 and "{" not in aid and "}" not in aid and "\n" not in aid:
            wf["artifact_id"] = aid
    if parsed.version:
        ver = parsed.version.strip()
        if len(ver) <= 24 and re.fullmatch(r"[vV]?\d+(?:\.\d+){0,2}(?:-[A-Za-z0-9._-]+)?", ver):
            wf["version"] = ver.lstrip("vV") or "1.0"
    if parsed.workflow_md_path:
        wpath = parsed.workflow_md_path.strip()
        if len(wpath) <= 200 and "{" not in wpath and "\n" not in wpath:
            wf["workflow_md_path"] = wpath
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
        mappings = list(parsed.handoff_mappings)
        indexes = [mapping.handoff_index for mapping in mappings]
        requested_ids = [mapping.workflow_end_state_id.strip().casefold() for mapping in mappings]
        mappings_are_complete_and_unique = (
            len(mappings) == len(handoffs)
            and sorted(indexes) == list(range(1, len(handoffs) + 1))
            and len(requested_ids) == len(set(requested_ids))
        )
        resolved_mappings = []
        if mappings_are_complete_and_unique:
            for mapping in mappings:
                index = mapping.handoff_index - 1
                requested_id = mapping.workflow_end_state_id.strip()
                matches = [
                    state_id for state_id in parsed_ids
                    if str(state_id).strip().casefold() == requested_id.casefold()
                ]
                if index >= len(handoffs) or len(matches) != 1:
                    resolved_mappings = []
                    break
                resolved_mappings.append((index, str(matches[0]).strip()))

        # Persist mappings atomically only when they cover every handoff and
        # resolve one-to-one to unique End State IDs in the saved Markdown.
        if len(resolved_mappings) == len(handoffs) and handoffs:
            for index, canonical_id in resolved_mappings:
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
) -> LlmResponse | None:
    # Fast-path direct ingest when user pastes a complete structured Markdown workflow table.
    user_text = ""
    for content in reversed(llm_request.contents or []):
        if getattr(content, "role", None) == "user":
            user_text = "\n".join(
                str(getattr(part, "text", "") or "")
                for part in (getattr(content, "parts", None) or [])
                if getattr(part, "text", None)
            ).strip()
            if user_text:
                break
    if user_text and re.search(r"(?im)^\|\s*Step\s*\|\s*Actor\s*\|", user_text):
        from managers.control.bpmn import parse_workflow_steps

        sanitized = _sanitize_workflow_markdown(user_text)
        parsed_steps = parse_workflow_steps(sanitized)
        if len(parsed_steps.get("steps") or []) >= 3:
            id_match = re.search(
                r"(?im)^#{1,3}[^\n]*\b(WF-[A-Z0-9]+(?:-[A-Z0-9]+)*)\b", sanitized
            )
            artifact_id = id_match.group(1).upper() if id_match else None
            payload = WorkflowData(
                artifact_id=artifact_id,
                workflow_md_content=sanitized,
            ).model_dump_json()
            resp = LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[types.Part(text=payload)],
                )
            )
            await wf_merge_callback(callback_context, resp)
            return resp

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
    return None


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
    model=fast_model,
    instruction=(
        "Trích xuất/cập nhật workflow markdown NGẮN GỌN cho **một** luồng/story tại một thời điểm. "
        "CHỈ TRẢ PHẦN THAY ĐỔI (DELTA): Nếu tin nhắn user KHÔNG thêm/sửa bước, BR ref, exception hay end state nào (chỉ hỏi, xác nhận, hoặc yêu cầu xem preview), trả `{}` (để trống `workflow_md_content` và `workflow_patches`). "
        "Nếu `CURRENT WORKFLOW` đã có nội dung và user chỉ sửa/thêm vài dòng, BR ref, exception hoặc end state, HÃY DÙNG `workflow_patches` (find/replace hoặc append_to_section) thay vì viết lại toàn bộ `workflow_md_content`. Chỉ trả full `workflow_md_content` khi tạo mới từ đầu hoặc tái cấu trúc phần lớn tài liệu. "
        "Artifact workflow có thể chứa NHIỀU workflow. Khi sửa một WF-ID bằng `workflow_md_content`, phải giữ nguyên đầy đủ mọi WF-ID khác và mọi bước/BR Ref hiện có; không được thay toàn bộ artifact bằng riêng luồng đang sửa. "
        "Main Flow, Alternative Flows VÀ Exception Flows ĐỀU PHẢI dùng chung định dạng bảng Markdown 5 cột: "
        "`Step | Actor | Action | BR | Gateway` "
        "(BẮT BUỘC dùng đúng tiêu đề `## Main Flow`, `## Exception Flows`, `## End States`; TUYỆT ĐỐI KHÔNG sao chép block `=== FEATURE DEFINITION ===`, KHÔNG dùng mã flag như `WF-F6` làm tiêu đề bảng Main Flow, và KHÔNG viết Exception Flows dưới dạng tiêu đề con #### + gạch đầu dòng; End States dùng bảng `| ID | Description | Branch Condition |`). "
        "Actors phải khớp FD actors (sẽ thành lanes). "
        "Main Flow BẮT BUỘC có ≥3 dòng (Step), mỗi dòng chỉ là 1 hành động nguyên tử của ĐÚNG 1 Actor — TUYỆT ĐỐI KHÔNG gộp thao tác của User (nhập/bấm CTA) và xử lý của System (xác thực/cấp token/điều hướng/bàn giao) vào chung 1 Step; nếu user mô tả gộp, phải tự tách thành từng Step riêng theo Actor để có ≥3 bước rõ ràng cho BPMN swimlanes. "
        "Đặt cổng Gateway `XOR` đúng chuẩn BPMN: chỉ đặt `XOR` ở bước User có nhiều lựa chọn rẽ nhánh khác nhau (hoặc bước System kiểm tra/đánh giá điều kiện rẽ sang `E-xx` khi lỗi); KHÔNG đặt `XOR` ở các bước nhập liệu đơn thuần hay bấm 1 nút CTA đơn (dùng `None`). "
        "Đầy đủ nhánh, không để XOR treo: mọi nhánh phụ được nhắc tới ở tiêu đề hoặc cổng `XOR` (như Đăng ký, Quên mật khẩu, Đăng xuất...) PHẢI có đầy đủ các bước trong `Alternative Flows` và dòng tương ứng trong `End States` (`ES-01`, `ES-02`...). "
        "LUÔN dùng ký tự mũi tên Unicode `→` trong văn bản thường; TUYỆT ĐỐI KHÔNG dùng LaTeX (`$\\rightarrow$`, `\\rightarrow`). "
        "Bao gồm đủ Exception Flows và End States. "
        "Không lặp lại toàn bộ BR — chỉ tham chiếu BR-XX."
        "For every confirmed Feature Definition handoff, return one handoff_mappings entry with its 1-based handoff_index and the exact Workflow End State ID. Mappings must be complete and one-to-one: no two handoffs may use the same Workflow End State ID. "
        "When distinct confirmed branches currently share one broad End State (for example, all roles mapped to Login Success), split that outcome into one role/branch-specific End State per handoff, assign each a unique unused Workflow End State ID, update the complete workflow markdown, and map each handoff to its matching ID. Do this from the existing branch conditions and destinations; do not ask the user to invent technical IDs. "
        "Never copy exit_state_id into workflow_end_state_id unless that exact ID exists in Workflow End States. Never claim the mapping was saved unless the complete unique handoff_mappings list resolved against IDs in the saved workflow markdown and the deterministic WF-F6 result is PASS."
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
    model=fast_model,
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
        "Main Flow, Alternative Flows và Exception Flows đều dùng chung bảng cột `Step | Actor | Action | BR | Gateway` (không viết Exception Flows kiểu #### + bullet), End States dùng bảng `| ID | Description | Branch Condition |`. "
        "Đặt `XOR` đúng vai trò BPMN: chỉ đặt `XOR` ở bước User có nhiều lựa chọn rẽ nhánh hoặc bước System kiểm tra/validate điều kiện rẽ sang `E-xx`; các bước nhập liệu hoặc nhấn 1 CTA đơn dùng `None`. Đảm bảo mọi nhánh nêu ở tiêu đề hoặc `XOR` (như Đăng xuất) đều có đủ bước `AF-xx` và `ES-xx` tương ứng. Luôn dùng mũi tên Unicode `→`, không dùng LaTeX `\\rightarrow`. "
        "Main Flow phải có ≥3 bước nguyên tử (tách riêng bước thao tác của User và bước xử lý/phản hồi của System thành từng dòng theo Actor, không gộp 2 Actor vào 1 Step). "
        "Actors phải khớp Feature Definition (không invent Management/Supplier). "
        "Hỏi làm rõ theo CHECKLIST (ưu tiên required=true). "
        "Không dùng mã flag kỹ thuật (WF-F1…) trong câu trả lời User. "
        "Nhắc OQ/Assumptions nếu có. "
        "After every Workflow change, read PACKAGE CHANGE REVIEW (deterministic checks) and review all existing workflows, BR references, User Stories, BPMN, and UI Touchpoints. Treat current artifact data and deterministic checks as the source of truth; prior assistant prose is not proof that a change was saved. Report concrete findings and affected sections. If failed_checks is non-empty, do not claim the package is okay and do not ask the user to Validate; guide them to fix the listed findings. Only when failed_checks is empty may you say the package is ready for the user to move to User Stories (or Validate after all 4 sections are complete). Never claim that Validate has already run. "
        "TUYỆT ĐỐI KHÔNG đưa lựa chọn dạng 'Chạy Validate' / 'Validate ngay' vào `AGENT_CHOICES_JSON` vì tin nhắn chat không thể kích hoạt nút Validate (nút Validate nằm ở thanh công cụ và chỉ dùng sau khi hoàn thành cả 4 section). Khi Workflow đã đạt, chỉ gợi ý chuyển sang **User Stories (SEC-04)** hoặc **Preview BPMN**. "
        "When FEATURE DEFINITION.continuity.has_successor is true, include an explicit End States table with IDs and describe each distinct terminal branch separately. The validator requires a one-to-one mapping: every continuity.handoffs record must map to one End State, and each End State ID may map to only one handoff. If several roles or conditions share a broad success outcome, split it into separate End State rows with unique IDs. Generate unused technical IDs from the workflow itself; do not ask the user to provide IDs when branch conditions/destinations are clear. Keep successor detailed screens outside this feature's scope. "
        "Keep continuity `exit_state_id` (handoff outcome identity) separate from `workflow_end_state_id` (the referenced ID in the Workflow End States). Preserve user-provided End State IDs; never rename them or overwrite `exit_state_id` to force a match. The extractor must return the full one-to-one `handoff_mappings` list; mappings are saved atomically only if every index is present, every target ID is unique, and each exact ID exists once in the saved Markdown. Only report WF-F6 from the deterministic package review. "
        "When the existing workflow lacks distinct End States for clearly distinct handoff branches, update the workflow and mapping together; do not leave a many-to-one mapping or tell the user to enter codes. If a destination or branch condition is genuinely ambiguous, ask about that business meaning, not a technical ID. Never claim a mapping or BR reference was saved unless it appears in CURRENT WORKFLOW and the deterministic review confirms the result. "
        "Before finalizing, ensure every Business Rule ID in BUSINESS RULES appears in the saved workflow markdown, including cross-cutting rules such as security. Reference each rule in the relevant step's BR column; WF-F5 passing for some BR references does not prove V-02 traceability passed for all rules."
        "You have tools: wiki first (`wiki.search_pages` / `wiki.fetch_page`); "
        "do NOT `web_search` for FD scope_out / non-goals / unrelated browsing; "
        "`web_search` only after wiki miss for in-package clarification; "
        "`preview_bpmn` after actor-step rows. Never approve/ingest/propose wiki.\n\n"
        + USER_CHOICES_INSTRUCTION
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
