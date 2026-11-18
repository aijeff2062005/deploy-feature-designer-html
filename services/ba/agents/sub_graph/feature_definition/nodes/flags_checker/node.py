"""Node definition for Feature Definition Flags Checker Node.

Architecture: LlmAgent with before_model_callback for early stop.
- before_model_callback runs logic check (check_fd_flags_logic)
- If no PENDING_SEMANTIC flags → early stop (skip LLM)
- If PENDING_SEMANTIC exists → LLM returns structured evaluations (no tools;
  llm-gate Qwen rejects tool_choice=auto)
"""

from __future__ import annotations

from pathlib import Path

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_response import LlmResponse

from agents.sub_graph.shared.semantic_flags import (
    SemanticFlagsEvaluation,
    apply_semantic_flag_evaluations,
)
from agents.sub_graph.shared.constants import PENDING_SEMANTIC
from config.model_config import base_model
from utils import logging

from .callback import fd_flags_checker_before_model

logger = logging()


async def fd_flags_after_model(
    callback_context: CallbackContext,
    llm_response: LlmResponse,
) -> None:
    parts = llm_response.content.parts if llm_response.content else []
    text = "\n".join(part.text for part in parts if part.text)
    flags = callback_context.state.get("flags", {}).get("fd", {})
    pending_before = {
        flag_id
        for flag_id, flag in flags.items()
        if flag.get("status") == PENDING_SEMANTIC
    }
    updated = apply_semantic_flag_evaluations(callback_context.state, "fd", text)
    flags_after = callback_context.state.get("flags", {}).get("fd", {})
    unresolved = sorted(
        flag_id
        for flag_id in pending_before
        if flags_after.get(flag_id, {}).get("status") == PENDING_SEMANTIC
    )
    if unresolved:
        logger.error(
            "fd_flags_after_model: Semantic evaluation did not resolve pending flags "
            f"{unresolved}; parsed updates={updated}, response_text_present={bool(text)}"
        )
    else:
        logger.info(
            f"fd_flags_after_model: Applied {updated} semantic evaluation(s) "
            f"for pending flags {sorted(pending_before)}"
        )


def create_fd_flags_checker_node():
    """Create Feature Definition flags checker LlmAgent node."""
    prompt_path = Path(__file__).parent / "prompt.md"
    instruction = (
        prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else ""
    )
    return LlmAgent(
        name="fd_flags_checker",
        model=base_model,
        instruction=instruction,
        output_schema=SemanticFlagsEvaluation,
        before_model_callback=fd_flags_checker_before_model,
        after_model_callback=fd_flags_after_model,
    )
