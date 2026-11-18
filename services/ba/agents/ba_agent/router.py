"""Top-level router for the unified BA Agent graph."""

from __future__ import annotations

import logging

from google.adk import Context
from google.adk.workflow import node

from agents.ba_agent.constants import (
    DEFAULT_SECTION,
    DEFAULT_TICKET_TYPE,
    IMPLEMENTED_SECTIONS,
    IMPLEMENTED_TICKET_TYPES,
    ROUTE_BR,
    ROUTE_FD,
    ROUTE_LOCKED,
    ROUTE_OPEN_QUESTIONS,
    ROUTE_STUB,
    ROUTE_UC,
    ROUTE_VALIDATE,
    ROUTE_WF,
    SECTION_TO_ROUTE,
    STATE_ACTIVE_SECTION,
    STATE_TICKET_TYPE,
    STATE_VALIDATE_ACTION,
    VALIDATE_MESSAGE_PREFIX,
    SECTION_VALIDATION,
    SECTION_OPEN_QUESTIONS,
)
from agents.ba_agent.section_gate import can_access_section, sync_sections_status_from_flags
from agents.sub_graph.feature_definition.nodes.oq_asm_detector.callback import (
    apply_oq_resolutions,
    resolutions_from_user_message,
)

logger = logging.getLogger(__name__)


def _user_text(ctx: Context) -> str:
    content = getattr(ctx, "user_content", None)
    if content is None:
        return ""
    parts = getattr(content, "parts", None) or []
    if not parts:
        return ""
    return getattr(parts[0], "text", "") or ""


def route_ba_turn(ctx: Context) -> str:
    """Pure routing logic — used by the graph node and by unit tests."""
    state = ctx.state
    user_input = _user_text(ctx)

    ticket_type = state.get(STATE_TICKET_TYPE) or DEFAULT_TICKET_TYPE
    active_section = state.get(STATE_ACTIVE_SECTION) or DEFAULT_SECTION
    state[STATE_TICKET_TYPE] = ticket_type
    state[STATE_ACTIVE_SECTION] = active_section
    state.setdefault("artifacts", {})
    state.setdefault("flags", {})
    state.setdefault("sections_status", {})
    state.setdefault("feature_status", "DRAFT")
    sid = str(state.get("session_id") or state.get("feature_id") or "")
    if sid:
        from managers.control.session import project_id_for_session

        state.setdefault("project_id", project_id_for_session(sid))
    else:
        state.setdefault("project_id", "PRJ-UNKNOWN")
    state["current_turn"] = int(state.get("current_turn", 0)) + 1

    if ticket_type not in IMPLEMENTED_TICKET_TYPES:
        ctx.route = ROUTE_STUB
        return user_input

    if state.get(STATE_VALIDATE_ACTION) or user_input.strip().startswith(
        VALIDATE_MESSAGE_PREFIX
    ):
        state[STATE_VALIDATE_ACTION] = False
        state["_va_run_validate"] = True
        ctx.route = ROUTE_VALIDATE
        logger.info("BA Router: validate → VA subgraph")
        return user_input

    if active_section == SECTION_VALIDATION:
        ctx.route = ROUTE_VALIDATE
        logger.info("BA Router: validation page → VA subgraph (discuss)")
        return user_input

    # Open Questions is a conversational resolution step. It is deliberately
    # outside the sequential FD -> BR -> WF -> UC completion gate.
    if active_section == SECTION_OPEN_QUESTIONS:
        # Apply an explicitly addressed answer before the LLM runs so its
        # response sees the updated blocker state and can guide the user to
        # Validate immediately when the last critical OQ is resolved.
        changed = apply_oq_resolutions(
            state, resolutions_from_user_message(user_input)
        )
        if changed:
            sync_sections_status_from_flags(state)
        ctx.route = ROUTE_OPEN_QUESTIONS
        logger.info("BA Router: section=open_questions route=open_questions")
        return user_input

    if active_section not in IMPLEMENTED_SECTIONS:
        ctx.route = ROUTE_STUB
        return user_input

    allowed, blocked_by = can_access_section(state, active_section)
    if not allowed:
        ctx.route = ROUTE_LOCKED
        state["_locked_reason"] = blocked_by
        logger.info(
            "BA Router: locked section=%s blocked_by=%s", active_section, blocked_by
        )
        return user_input

    route = SECTION_TO_ROUTE[active_section]
    ctx.route = route
    sync_sections_status_from_flags(state)
    logger.info("BA Router: section=%s route=%s", active_section, route)
    return user_input


@node(name="ba_router")
async def ba_router_node(ctx: Context) -> str:
    """Route by ticket_type + active_section. Unimplemented paths go to stub/locked."""
    user_input = route_ba_turn(ctx)
    await _sync_explicit_oq_answers_before_section(ctx, user_input)
    return user_input


async def _sync_explicit_oq_answers_before_section(ctx: Context, user_input: str) -> None:
    """Sync explicit OQ answers from any active section before its agent runs."""
    if ctx.route != ROUTE_OPEN_QUESTIONS:
        resolutions = resolutions_from_user_message(user_input)
        if resolutions and apply_oq_resolutions(ctx.state, resolutions):
            ctx.state["_oq_user_explicit_answer_ids"] = [
                item["id"] for item in resolutions
            ]
            from agents.ba_agent.open_questions import sync_open_questions_sequentially

            await ctx.run_node(
                sync_open_questions_sequentially,
                node_input="Synchronize explicitly answered Open Questions before continuing the active BA section.",
                run_id=f"oq_sync_t{ctx.state.get('current_turn')}_before_section",
            )
            sync_sections_status_from_flags(ctx.state)
