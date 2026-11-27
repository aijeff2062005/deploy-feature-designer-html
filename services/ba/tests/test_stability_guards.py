"""Regression tests for BA stability guards (use cases, workflow sanitizer, BPMN parser)."""

from __future__ import annotations

from types import SimpleNamespace

from agents.sub_graph.use_cases.merge import check_uc_flags_logic, merge_use_cases
from agents.sub_graph.use_cases.schemas import AcceptanceCriterion, UseCaseItem, UseCasesData
from agents.sub_graph.workflow.agent import _sanitize_workflow_markdown
from managers.control.bpmn import parse_workflow_steps


def test_merge_use_cases_drops_obsolete_split_parent() -> None:
    current = [
        {
            "id": "US-03",
            "actor_id": "ACT-GUEST",
            "statement": "Là một ACT-GUEST, tôi muốn đăng ký và đăng xuất.",
            "rule_ids": [],
            "bpmn_node_ids": [],
            "acceptance_criteria": [{"id": "AC-01", "given": "G", "when": "W", "then": "T"}],
        }
    ]
    delta = UseCasesData(
        use_cases=[
            UseCaseItem(
                id="US-03a",
                actor_id="ACT-GUEST",
                statement="Là một ACT-GUEST, tôi muốn đăng ký để dùng dịch vụ.",
                acceptance_criteria=[AcceptanceCriterion(id="AC-01", given="G", when="W", then="T")],
            ),
            UseCaseItem(
                id="US-03b",
                actor_id="ACT-GUEST",
                statement="Là một ACT-GUEST, tôi muốn đăng xuất để bảo mật.",
                acceptance_criteria=[AcceptanceCriterion(id="AC-01", given="G", when="W", then="T")],
            ),
        ]
    )
    merged, _ = merge_use_cases(current, delta)
    ids = [c["id"] for c in merged]
    assert "US-03" not in ids
    assert ids == ["US-03a", "US-03b"]


def test_uc_flags_accepts_nham_and_system_actor_in_ac() -> None:
    ctx = SimpleNamespace(
        state={
            "artifacts": {
                "feature_definition": {"actors": ["ACT-GUEST: Guest", "ACT-AUTH-SYSTEM: System"]},
                "business_rules": [{"id": "BR-01"}],
                "use_cases": [
                    {
                        "id": "US-01",
                        "actor_id": "ACT-GUEST",
                        "statement": "Là một ACT-GUEST, tôi muốn đăng nhập nhằm truy cập hệ thống.",
                        "rule_ids": ["BR-01"],
                        "acceptance_criteria": [
                            {
                                "id": "AC-01",
                                "given": "ACT-GUEST mở màn hình",
                                "when": "ACT-AUTH-SYSTEM xác thực",
                                "then": "Đăng nhập thành công",
                            }
                        ],
                    }
                ],
            },
            "flags": {"uc": {}},
        }
    )
    flags = check_uc_flags_logic(ctx)
    assert flags["UC-F2"]["status"] == "PASS"
    assert flags["UC-F6"]["status"] == "PASS"


def test_workflow_sanitizer_and_bpmn_fallback() -> None:
    raw = (
        "=== FEATURE DEFINITION ===\n"
        '{"goal":"leaked"}\n\n'
        "### WF-F6: Quy trình chính\n"
        "| Step | Actor | Action | BR | Gateway |\n"
        "| :--- | :--- | :--- | :--- | :--- |\n"
        "| **Step 1** | `ACT-USER` | Mở màn hình | `BR-01` | `None` |\n"
        "| **Step 2** | `ACT-SYS` | Xử lý | `BR-01` | `None -> ES-01` |\n"
    )
    cleaned = _sanitize_workflow_markdown(raw)
    assert "=== FEATURE DEFINITION ===" not in cleaned
    assert "## Main Flow" in cleaned
    steps = parse_workflow_steps(cleaned)
    assert len(steps) >= 2
