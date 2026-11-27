"""Regression tests for Designer structured JSON repair."""

from __future__ import annotations

from app.core.llm import parse_structured_output
from app.workflow.designer import DesignDecision


def test_parse_structured_output_repairs_unescaped_choice_tag() -> None:
    broken = r'''{
  "intent": "propose_scope",
  "reply": "Tuyệt vời! Bạn xác nhận chứ?\n\n<!-- AGENT_CHOICES_JSON:{"question":"Xác nhận?","multi":false,"options":[{"label":"Đồng ý","value":"Tôi đồng ý tiếp tục.","recommended":true},{"label":"Chỉnh sửa","value":"Tôi muốn chỉnh sửa."}]} -->"
}'''
    result = parse_structured_output(broken, DesignDecision)
    assert result.intent == "propose_scope"
    assert "AGENT_CHOICES_JSON" in (result.reply or "")
