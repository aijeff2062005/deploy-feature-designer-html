"""BR extractor node."""

from __future__ import annotations

from google.adk.agents import LlmAgent

from agents.sub_graph.business_rules.schemas import BusinessRulesData
from callbacks.filter_history import create_history_filter
from config.model_config import fast_model

from .callback import br_extractor_inject_callback, br_merge_callback


def create_br_extractor_node():
    return LlmAgent(
        name="br_extractor",
        model=fast_model,
        instruction=(
            "Trích xuất business rules từ tin nhắn user theo chế độ DELTA. "
            "CHỈ trả về các rule được thêm mới hoặc sửa trong lượt này; KHÔNG lặp lại các rule cũ không đổi (backend tự merge theo id). "
            "Nếu user không thêm/sửa rule nào, trả `{\"business_rules\": []}`. "
            "Trả JSON với business_rules: [{id, statement, source}]. "
            "Không xóa rule cũ trừ khi user yêu cầu sửa id cụ thể."
        ),
        output_schema=BusinessRulesData,
        before_model_callback=[
            # Extractor only needs the latest user turn + injected FD/rules.
            create_history_filter(
                visible_authors=["br_discussion"],
                process_latest_message_only=True,
            ),
            br_extractor_inject_callback,
        ],
        after_model_callback=br_merge_callback,
    )
