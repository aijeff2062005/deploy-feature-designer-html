"""Workflow flag logic."""

from __future__ import annotations

import re
from typing import Any

from agents.ba_agent.section_gate import sync_sections_status_from_flags
from agents.sub_graph.shared.constants import (
    FAIL,
    MISSING_VALUE,
    PASS,
    PENDING_SEMANTIC,
    WARNING,
)
from agents.sub_graph.shared.flag_helpers import apply_not_yet_reset, flag_dict, was_passed
from managers.control.bpmn import parse_workflow_steps


def check_wf_flags_logic(ctx) -> dict[str, Any]:
    wf = ctx.state.get("artifacts", {}).get("workflow", {})
    content = str(wf.get("workflow_md_content", "") or "")
    existing = dict(ctx.state.get("flags", {}).get("wf", {}))
    flags: dict[str, Any] = {}
    has_content = bool(content.strip())
    parsed = parse_workflow_steps(content)
    steps = len(parsed.get("steps") or [])

    # WF-F1: Main flow ≥3 steps (blocker) — code count, sticky PASS
    if not has_content:
        flags["WF-F1"] = flag_dict(
            1, "Main flow defined", MISSING_VALUE, "Chưa có workflow markdown."
        )
    elif steps < 3:
        flags["WF-F1"] = flag_dict(
            1, "Main flow defined", FAIL, f"Cần ≥3 bước Main Flow, hiện ~{steps}."
        )
    else:
        flags["WF-F1"] = flag_dict(1, "Main flow defined", PASS)

    # WF-F2: End-to-end coverage (blocker, semantic)
    if not has_content or steps < 3:
        flags["WF-F2"] = flag_dict(
            2, "End-to-end coverage", MISSING_VALUE, "Cần Main Flow đủ bước trước."
        )
    elif was_passed(existing, "WF-F2"):
        flags["WF-F2"] = flag_dict(2, "End-to-end coverage", PASS)
    else:
        flags["WF-F2"] = flag_dict(
            2,
            "End-to-end coverage",
            PENDING_SEMANTIC,
            "Cần xác nhận luồng liên tục từ trigger → End State.",
        )

    # WF-F3: Exception flows (warning) — heuristic + optional sticky
    if "exception" in content.lower():
        flags["WF-F3"] = flag_dict(3, "Exception flows exist", PASS)
    elif has_content:
        flags["WF-F3"] = flag_dict(
            3,
            "Exception flows exist",
            WARNING,
            "Khuyến nghị thêm section Exception Flows.",
        )
    else:
        flags["WF-F3"] = flag_dict(3, "Exception flows exist", WARNING, "Chưa có workflow.")

    # WF-F4: Actor consistency (blocker, semantic) — needs FD actors
    if not has_content:
        flags["WF-F4"] = flag_dict(4, "Actor consistency", MISSING_VALUE)
    elif was_passed(existing, "WF-F4"):
        flags["WF-F4"] = flag_dict(4, "Actor consistency", PASS)
    else:
        flags["WF-F4"] = flag_dict(
            4,
            "Actor consistency",
            PENDING_SEMANTIC,
            "Cần đối chiếu actors trong Workflow với Feature Definition.actors[].",
        )

    # WF-F5: BR references (warning)
    rules = (ctx.state.get("artifacts") or {}).get("business_rules") or []
    known_rule_ids = {
        str(rule.get("id") or "").strip().casefold()
        for rule in rules
        if isinstance(rule, dict) and str(rule.get("id") or "").strip()
    }
    referenced_rule_ids = {
        reference.strip().casefold()
        for step in parsed.get("steps") or []
        for reference in re.findall(r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+", step.get("br_ref") or "")
    }
    if referenced_rule_ids and (not known_rule_ids or referenced_rule_ids & known_rule_ids):
        flags["WF-F5"] = flag_dict(5, "BR references", PASS)
    elif has_content:
        flags["WF-F5"] = flag_dict(
            5, "BR references", WARNING, "Khuyến nghị tham chiếu BR-XX trong Main Flow."
        )
    else:
        flags["WF-F5"] = flag_dict(5, "BR references", WARNING)

    fd = (ctx.state.get("artifacts") or {}).get("feature_definition") or {}
    continuity = fd.get("continuity") or {}
    handoffs = continuity.get("handoffs") or [] if isinstance(continuity, dict) else []
    if isinstance(continuity, dict) and continuity.get("has_successor") is True:
        if not handoffs and continuity.get("exit_state"):
            handoffs = [{
                "branch_condition": continuity.get("exit_state"),
                "exit_state": continuity.get("exit_state"),
                "exit_state_id": continuity.get("exit_state_id"),
                "workflow_end_state_id": continuity.get("workflow_end_state_id"),
            }]
        state_labels = parsed.get("end_states") or []
        state_ids = parsed.get("end_state_ids") or []
        matched_ids = []
        unmatched = []
        for index, handoff in enumerate(handoffs, start=1):
            mapped_workflow_id = handoff.get("workflow_end_state_id")
            mapped_ids = [mapped_workflow_id]
            if len(handoffs) == 1:
                # Older artifacts can retain a stale per-handoff mapping after
                # the top-level mapping was corrected. Try both, accepting
                # only IDs that resolve uniquely in this workflow.
                mapped_ids.append(continuity.get("workflow_end_state_id"))
            matches = []
            for candidate in mapped_ids:
                workflow_end_state_id = re.sub(
                    r"^[*_`]+|[*_`]+$", "", str(candidate or "").strip()
                ).strip()
                if not workflow_end_state_id:
                    continue
                candidate_matches = [
                    row for row, state_id in enumerate(state_ids)
                    if str(state_id).strip().casefold() == workflow_end_state_id.casefold()
                ]
                if len(candidate_matches) == 1:
                    matches = candidate_matches
                    break

            if not matches:
                expected = _normalized_text(handoff.get("exit_state") or "")
                normalized_labels = [_normalized_text(label) for label in state_labels]
                matches = [
                    row for row, label in enumerate(normalized_labels)
                    if expected and expected in label
                ]
            if len(matches) != 1 or len(state_ids) != len(state_labels):
                unmatched.append(str(index))
                continue
            row = matches[0]
            state_id = state_ids[row]
            handoff["workflow_end_state_id"] = state_id
            matched_ids.append(state_id)
        if not handoffs or unmatched or len(matched_ids) != len(set(matched_ids)):
            detail = (
                "Cần một End State có mã, khớp duy nhất cho từng nhánh bàn giao; "
                "kiểm tra các nhánh: " + (", ".join(unmatched) if unmatched else "mã End State bị dùng lặp") + "."
            )
            flags["WF-F6"] = flag_dict(
                6, "End states match handoffs", MISSING_VALUE if not state_ids else FAIL, detail
            )
        else:
            continuity["handoffs"] = handoffs
            if len(handoffs) == 1:
                continuity["workflow_end_state_id"] = handoffs[0]["workflow_end_state_id"]
            fd["continuity"] = continuity
            ctx.state["artifacts"] = {**(ctx.state.get("artifacts") or {}), "feature_definition": fd}
            flags["WF-F6"] = flag_dict(6, "End states match handoffs", PASS)
    elif parsed.get("end_states"):
        flags["WF-F6"] = flag_dict(6, "End states defined", PASS)
    elif has_content:
        flags["WF-F6"] = flag_dict(
            6, "End states defined", WARNING, "Khuyến nghị thêm section End States."
        )
    else:
        flags["WF-F6"] = flag_dict(6, "End states defined", WARNING)
    apply_not_yet_reset(flags, existing)

    all_flags = dict(ctx.state.get("flags", {}))
    all_flags["wf"] = flags
    ctx.state["flags"] = all_flags
    sync_sections_status_from_flags(ctx.state)
    return flags


def _normalized_text(value: Any) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", str(value or "").casefold()).split())


def _main_flow_section(content: str) -> str:
    """Slice `## Main Flow` … next heading so exception/end-state lines are ignored."""
    lines = content.splitlines()
    start = None
    for i, raw in enumerate(lines):
        if re.match(r"^#{1,3}\s*main flow\b", raw.strip(), re.IGNORECASE):
            start = i + 1
            break
    if start is None:
        return content
    end = len(lines)
    for j in range(start, len(lines)):
        if re.match(r"^#{1,3}\s+\S", lines[j].strip()) and not re.match(
            r"^#{1,3}\s*main flow\b", lines[j].strip(), re.IGNORECASE
        ):
            end = j
            break
    return "\n".join(lines[start:end])


def _count_main_flow_steps(content: str) -> int:
    """Count Main Flow steps with the canonical workflow parser."""
    return len(parse_workflow_steps(content).get("steps") or [])
