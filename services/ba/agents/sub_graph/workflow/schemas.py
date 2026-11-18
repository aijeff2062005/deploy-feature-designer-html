"""Workflow section schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class WorkflowHandoffMapping(BaseModel):
    """Explicit link from one FD handoff to a Workflow End State."""

    handoff_index: int = Field(
        ..., ge=1, description="1-based index of the matching Feature Definition continuity.handoffs record."
    )
    workflow_end_state_id: str = Field(
        ..., description="Exact ID from the Workflow End States section; never copy exit_state_id unless it is also the actual Workflow ID."
    )


class WorkflowData(BaseModel):
    """Delta output from WF extractor — merge vào artifacts.workflow."""

    artifact_id: str | None = Field(
        None,
        description="ID artifact dạng WF-{MODULE}-001. Chỉ set khi User/PO cung cấp.",
    )
    version: str | None = Field(
        None,
        description="Version semver của workflow doc, VD 1.0.0.",
    )
    workflow_md_path: str | None = Field(
        None,
        description="Đường dẫn file markdown nếu có; thường để trống khi content inline.",
    )
    workflow_md_content: str | None = Field(
        None,
        description=(
            "Full workflow markdown. Bắt buộc có các section: "
            "## Main Flow (bảng | Step | Actor | Action | BR Ref |, ≥3 bước), "
            "## Exception Flows, ## End States, và tùy chọn ## Sequence Diagram (mermaid). "
            "Chỉ cập nhật khi User cung cấp/chỉnh sửa luồng."
        ),
    )
    handoff_mappings: list[WorkflowHandoffMapping] | None = Field(
        None,
        description=(
            "Explicitly confirmed mappings from Feature Definition handoffs to Workflow End State IDs. "
            "Only include a mapping when the user's confirmation or the supplied artifacts identify exactly one matching End State. "
            "Use the exact Workflow End State ID and preserve exit_state_id. Leave empty and ask the user when ambiguous."
        ),
    )
