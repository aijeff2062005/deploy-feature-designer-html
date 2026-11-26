"""Pydantic Schemas for Feature Definition Node & OQ/ASM Detection."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class FeatureHandoffData(BaseModel):
    """One outgoing workflow branch and its downstream feature contract."""

    branch_condition: str | None = Field(None, description="Confirmed condition that selects this terminal branch.")
    successor_feature_id: str | None = Field(None, description="Known downstream feature ID, if created.")
    successor_feature_title: str | None = Field(None, description="Human-readable downstream feature or destination screen title; an ID is optional.")
    exit_state_id: str | None = Field(None, description="Stable identifier for this continuity outcome.")
    workflow_end_state_id: str | None = Field(None, description="Matching End State ID in the approved workflow artifact.")
    exit_state: str | None = Field(None, description="Confirmed outcome and state handed off by this branch.")
    deferred_scope: list[str] | None = Field(None, description="Scope owned by the downstream feature for this branch.")


class IncomingFeatureHandoffData(BaseModel):
    """One approved upstream branch accepted by the current feature."""

    predecessor_feature_id: str | None = Field(None, description="Approved upstream feature ID.")
    predecessor_feature_title: str | None = Field(None, description="Approved upstream feature title.")
    predecessor_version: str | None = Field(None, description="Approved upstream version used as source.")
    predecessor_end_state_id: str | None = Field(None, description="Approved upstream End State ID.")
    predecessor_end_state: str | None = Field(None, description="Approved upstream End State description.")
    entry_state: str | None = Field(None, description="Verifiable starting condition inherited by this branch.")


class FeatureContinuityData(BaseModel):
    """Explicit upstream/downstream feature handoff contracts."""

    has_successor: bool | None = Field(None, description="Set only after the user confirms whether a successor exists.")
    has_predecessor: bool | None = Field(None, description="Set only after the user confirms whether this feature continues approved prior work.")
    handoffs: list[FeatureHandoffData] | None = Field(None, description="One record per distinct outgoing workflow branch.")
    incoming_handoffs: list[IncomingFeatureHandoffData] | None = Field(None, description="Approved predecessor branches accepted by this feature.")
    # Legacy single-handoff fields are retained so existing saved sessions remain readable.
    successor_feature_id: str | None = Field(None, description="Known downstream feature ID, if created.")
    successor_feature_title: str | None = Field(None, description="User-confirmed downstream feature title.")
    exit_state_id: str | None = Field(None, description="Stable identifier for this continuity outcome.")
    workflow_end_state_id: str | None = Field(None, description="Matching End State ID in the approved workflow artifact.")
    exit_state: str | None = Field(None, description="Confirmed outcome and state handed to the downstream feature.")
    deferred_scope: list[str] | None = Field(None, description="Confirmed scope excluded here and owned by the downstream feature.")
    predecessor_feature_id: str | None = Field(None, description="Approved upstream feature ID, when known.")
    predecessor_feature_title: str | None = Field(None, description="Approved upstream feature title.")
    predecessor_version: str | None = Field(None, description="Approved upstream feature version used as source.")
    predecessor_end_state_id: str | None = Field(None, description="Upstream approved End State ID referenced here.")
    entry_state: str | None = Field(None, description="Confirmed starting condition inherited from the upstream End State.")


class FeatureScreenSpec(BaseModel):
    """Detailed, ordered screen requirements provided for a feature."""

    screen_id: str = Field(..., description="Stable screen slug, e.g. chon-chuyen-bay-moi.")
    name: str = Field(..., description="Human-readable screen title.")
    sequence: int = Field(..., ge=1, description="1-based position in the user flow.")
    platform: str | None = Field(None, description="Target platform or viewport, if specified.")
    purpose: str | None = Field(None, description="What this screen lets the user accomplish.")
    sections: list[str] = Field(default_factory=list, description="Named UI blocks and required visible content.")
    actions: list[str] = Field(default_factory=list, description="Primary/secondary actions and destinations.")
    states: list[str] = Field(default_factory=list, description="Loading, empty, disabled, error, success, or other specified states.")
    rules: list[str] = Field(default_factory=list, description="Screen-specific business rules and validations.")
    data_contract: list[str] = Field(default_factory=list, description="Data passed in, calculated, or handed off by this screen.")
    notes: list[str] = Field(default_factory=list, description="Other explicit constraints from the user's brief.")


class FeatureDefinitionData(BaseModel):
    """Schema cho FD Extractor output. LLM trả về delta mỗi turn."""

    goal: str | None = Field(
        None,
        description="WHY feature này cần tồn tại — business value cụ thể. "
        "Chỉ cập nhật khi User cung cấp thông tin mới về mục tiêu.",
    )
    actors: list[str] | None = Field(
        None,
        description="Danh sách actors liên quan (format: 'ACT-NAME: mô tả vai trò'). "
        "Nếu có thay đổi, trả về toàn bộ danh sách mới; null nếu không thay đổi; [] nếu người dùng xóa toàn bộ.",
    )
    scope_in: list[str] | None = Field(
        None,
        description="Danh sách tóm tắt ngắn gọn các màn hình/chức năng thuộc phạm vi (1 câu ~15–30 từ cho mỗi màn hình hoặc capability). "
        "KHÔNG chép chi tiết UI/layout (Top Bar, Placeholder, px, danh sách Inline Error, CTA) vào đây — phần đó thuộc screen_specs. "
        "Nếu có thay đổi, trả về toàn bộ danh sách mới; null nếu không thay đổi; [] nếu người dùng xóa toàn bộ.",
    )
    scope_out: list[str] | None = Field(
        None,
        description="Capabilities KHÔNG thuộc phạm vi. "
        "Nếu có thay đổi, trả về toàn bộ danh sách mới; null nếu không thay đổi; [] nếu người dùng xóa toàn bộ.",
    )
    screen_specs: list[FeatureScreenSpec] | None = Field(
        None,
        description="Complete ordered screen-by-screen requirements when explicitly supplied; null preserves existing specs, [] clears them.",
    )

    continuity: FeatureContinuityData | None = Field(
        None,
        description="Confirmed feature handoff details; never infer missing links or states.",
    )


class OpenQuestionItem(BaseModel):
    """Mô tả một câu hỏi mở (Open Question) được phát hiện."""

    id: str | None = Field(None, description="ID tự động sinh dạng OQ-XXX")
    question: str = Field(..., description="Câu hỏi kết thúc bằng dấu ?")
    severity: str = Field("warning", description="Mức độ: critical | warning | info")
    assigned_to: str = Field(
        "PO", description="Vai trò chịu trách nhiệm: PO | Tech Lead | BA | Designer"
    )
    impact: str = Field(
        ..., description="Mô tả ảnh hưởng nếu câu hỏi không được làm rõ"
    )
    affected_artifacts: list[dict[str, str]] = Field(
        default_factory=list,
        description="Existing BA artifact references this question may affect, including the artifact for the section where it was raised when applicable.",
    )


class AssumptionItem(BaseModel):
    """Mô tả một giả định (Assumption) được phát hiện."""

    id: str | None = Field(None, description="ID tự động sinh dạng ASM-XX")
    statement: str = Field(
        ..., description="Giả định cụ thể, có thể verify hoặc invalidate"
    )


class OQArtifactReference(BaseModel):
    artifact_type: Literal[
        "feature_definition", "business_rules", "workflow", "use_cases", "ui_touchpoints"
    ]
    artifact_id: str
    reason: str = ""
    oq_id: str | None = None


class OpenQuestionResolution(BaseModel):
    """User answered an existing Open Question — do not invent a new OQ."""

    id: str = Field(..., description="ID OQ đã tồn tại, ví dụ OQ-001")
    answer: str = Field(..., description="Câu trả lời cụ thể của User cho OQ này")
    status: str = Field(
        "resolved",
        description="resolved khi đã trả lời đủ; open nếu vẫn chưa đủ",
    )
    affected_artifacts: list["OQArtifactReference"] = Field(
        default_factory=list,
        description="Artifact IDs cần cập nhật vì câu trả lời này, ví dụ BR-RESET-001, WF-RESET-001, US-RESET-001.",
    )


class OQArtifactUpdate(BaseModel):
    oq_id: str
    artifact_type: Literal[
        "feature_definition", "business_rules", "workflow", "use_cases", "ui_touchpoints"
    ]
    artifact_id: str
    updated_value: str = Field(
        ..., description="Updated one artifact: markdown for workflow, JSON object for business_rules/use_cases."
    )


class OQFollowThroughData(BaseModel):
    """Artifact updates made after an OQ answer, before the BA reply is composed."""

    reviewed_oq_ids: list[str] = Field(default_factory=list)
    impacted_artifacts: list[OQArtifactReference] = Field(default_factory=list)
    updates: list[OQArtifactUpdate] = Field(default_factory=list)
    no_change_reason: str | None = Field(
        None, description="Required when an answered OQ does not require artifact changes."
    )


class OQASMDetectionData(BaseModel):
    """Schema cho OQ & ASM Detector output."""

    open_questions: list[OpenQuestionItem] = Field(
        default_factory=list,
        description="Danh sách các Open Questions mới được phát hiện từ tin nhắn User.",
    )
    assumptions: list[AssumptionItem] = Field(
        default_factory=list,
        description="Danh sách các Assumptions mới được phát hiện từ tin nhắn User.",
    )
    resolved_questions: list[OpenQuestionResolution] = Field(
        default_factory=list,
        description="OQ đã có (theo id) được User trả lời trong tin nhắn này.",
    )


__all__ = [
    "AssumptionItem",
    "FeatureContinuityData",
    "FeatureHandoffData",
    "FeatureDefinitionData",
    "FeatureScreenSpec",
    "IncomingFeatureHandoffData",
    "OQASMDetectionData",
    "OpenQuestionItem",
    "OpenQuestionResolution",
    "OQArtifactReference",
    "OQArtifactUpdate",
    "OQFollowThroughData",
]
