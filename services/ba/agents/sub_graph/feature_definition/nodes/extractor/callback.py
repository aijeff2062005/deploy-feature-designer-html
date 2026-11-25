"""Callbacks for Feature Definition Extractor Node."""

from __future__ import annotations

import json

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse

from agents.sub_graph.feature_definition.schemas import FeatureDefinitionData
from callbacks.inject_context import insert_context
from agents.sub_graph.shared.wiki_context import format_approved_wiki_context
from agents.sub_graph.shared.project_context import fd_context_prompt
from managers.artifact.artifact_manager import merge_feature_definition_data
from utils import logging
from utils.utils import parse_str_to_dict

logger = logging()


async def fd_merge_callback(
    callback_context: CallbackContext,
    llm_response: LlmResponse,
) -> None:
    """
    Merge delta Feature Definition data output from LLM to `ctx.state['artifacts']['feature_definition']`.
    """

    parsed: FeatureDefinitionData | None = None

    raw_text = llm_response.content.parts[0].text
    parsed_dict = parse_str_to_dict(raw_text)
    if parsed_dict:
        try:
            parsed = FeatureDefinitionData.model_validate(parsed_dict)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"fd_merge_callback: Failed to validate dict from text: {e}")

    if not parsed:
        logger.warning("fd_merge_callback: llm_response.parsed is null or invalid")
        return

    ctx_state = callback_context.state
    artifacts = ctx_state.get("artifacts", {})
    current_fd = artifacts.get("feature_definition", {})

    current_data = FeatureDefinitionData(
        goal=current_fd.get("goal") or "",
        actors=current_fd.get("actors") or [],
        scope_in=current_fd.get("scope_in") or [],
        scope_out=current_fd.get("scope_out") or [],
        screen_specs=current_fd.get("screen_specs"),
        continuity=current_fd.get("continuity"),
    )

    merged = merge_feature_definition_data(current_data, parsed)

    artifacts["feature_definition"] = {
        "goal": merged.goal,
        "actors": merged.actors,
        "scope_in": merged.scope_in,
        "scope_out": merged.scope_out,
        "screen_specs": [item.model_dump(exclude_none=True) for item in merged.screen_specs] if merged.screen_specs is not None else [],
        "continuity": merged.continuity.model_dump(exclude_none=True) if merged.continuity else None,
    }
    # Reassign top-level state key to trigger ADK State.__setitem__ and commit delta to session storage
    ctx_state["artifacts"] = artifacts

    logger.info(
        f"fd_merge_callback: [ARTIFACT STATE] Merged FD delta into state: {ctx_state['artifacts']['feature_definition']}"
    )


async def fd_extractor_inject_callback(
    callback_context: CallbackContext,
    llm_request: LlmRequest,
) -> None:
    """
    Inject current Feature Definition current artifact into context.
    """
    state = callback_context.state
    artifacts = state.get("artifacts", {}).get("feature_definition", {})

    current_data_str = json.dumps(
        {
            "goal": artifacts.get("goal") or "",
            "actors": artifacts.get("actors") or [],
            "scope_in": artifacts.get("scope_in") or [],
            "scope_out": artifacts.get("scope_out") or [],
            "screen_specs": artifacts.get("screen_specs") or [],
            "continuity": artifacts.get("continuity") or {},
        },
        ensure_ascii=False,
    )

    context_text = f"=== CURRENT DATA OF feature_definition ===\n{current_data_str}"
    context_text += (
        "\n=== USER-PROVIDED FEATURE BRIEF ===\n"
        + str(state.get("feature_description") or "(not provided)")
    )
    continuity = artifacts.get("continuity") or {}
    unresolved = [
        index for index, handoff in enumerate(continuity.get("handoffs") or [], start=1)
        if not handoff.get("deferred_scope")
    ]
    if unresolved:
        context_text += (
            "\n=== DEFERRED SCOPE RECONCILIATION ===\n"
            f"Branches with missing deferred_scope: {unresolved}. "
            "Before the flags checker runs, reconcile these branches using explicit "
            "downstream ownership in current Scope Out, user statements or approved Wiki. "
            "Return the complete preserved handoffs list only if evidence supports an update. "
            "An empty field does not prove the user has not supplied the information."
        )
    context_text += "\n" + format_approved_wiki_context(state, include_content=True)
    context_text += "\n" + fd_context_prompt(state.get("project_context"))
    insert_context(llm_request, context_text)
    logger.info(
        f"fd_extractor_inject_callback: [ARTIFACT STATE] Current FD Artifact = {artifacts}"
    )
