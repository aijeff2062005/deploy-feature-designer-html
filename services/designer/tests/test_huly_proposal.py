import pytest

from app.huly.proposal import (
    HulyTaskProposal,
    ProposedTask,
    find_proposal_style_issues,
    proposal_prompt,
    proposal_review_prompt,
    task_authoring_skill,
    validate_proposal_links,
)


def test_task_authoring_skill_is_loaded_into_the_proposal_prompt():
    prompt = proposal_prompt(
        title="Feature title",
        draft={"goal": "Source goal", "acceptance_criteria": "Với A; khi B; thì C."},
        ba_context='{"use_cases": []}',
        design_context='{"screens": []}',
        source_ids=["BA:FEATURE", "SCREEN:one"],
    )

    assert "TASK-AUTHORING SKILL" in prompt
    assert "Treat screens as evidence and scope references, not as task boundaries" in task_authoring_skill()
    assert "huly-task-authoring" in task_authoring_skill()
    assert "## 🎯 Mục tiêu" in task_authoring_skill()
    assert "scope and priority rationale" in task_authoring_skill()
    assert "huly_tasks.py create" not in task_authoring_skill()
    assert "REQ-WMS-058" not in task_authoring_skill()
    assert "Deterministic draft" not in prompt
    assert "Với A; khi B; thì C." not in prompt
    assert "exactly 2 tasks" in prompt
    assert 'task_kind: "happy_path"' in prompt
    assert 'task_kind: "exception"' in prompt
    assert "theo thiết kế đính kèm" in prompt
    assert "Feature title" in prompt
    assert "BA:FEATURE" in prompt


def test_review_prompt_audits_screen_based_splits_against_sources():
    candidate = HulyTaskProposal(
        summary="Candidate plan",
        tasks=[
            ProposedTask(
                key="journey-one",
                task_kind="happy_path",
                title="Deliver the user journey",
                outcome="Source-backed result",
                solution="Source-backed behavior",
                scope_in=["Source-backed scope"],
                acceptance_criteria=["Với A; khi B; thì C."],
                priority="high",
                priority_rationale="Delivers primary journey",
                source_refs=["BA:FEATURE"],
            )
        ],
    )
    issues = find_proposal_style_issues(candidate)
    assert any("Với ...; khi ...; thì ..." in item for item in issues)

    prompt = proposal_review_prompt(
        original_prompt="SOURCE EVIDENCE",
        proposal=candidate,
        style_issues=issues,
    )

    assert "SOURCE EVIDENCE" in prompt
    assert "Screens are supporting evidence, never task boundaries by themselves" in prompt
    assert "fixed 2-task structure" in prompt
    assert "DETECTED STYLE VIOLATIONS TO FIX NOW" in prompt
    assert "CANDIDATE TASK PROPOSAL TO AUDIT" in prompt
    assert "Correct the proposal where needed" in prompt


def test_style_guardrails_detect_ba_codes_role_tags_and_code_identifiers():
    dirty = HulyTaskProposal(
        summary="Dirty plan",
        tasks=[
            ProposedTask(
                key="happy-path",
                task_kind="happy_path",
                title="[FE] Luồng chính BR-01",
                outcome="Cập nhật seat_map và totalPrice",
                solution="Theo thiết kế đính kèm",
                scope_in=["Luồng chính"],
                acceptance_criteria=["Kiểm tra US-02 thành công"],
                priority="high",
                priority_rationale="Tạo luồng chính",
                source_refs=["BA:FEATURE"],
            ),
            ProposedTask(
                key="exception-flows",
                task_kind="exception",
                title="Kiểm soát ngoại lệ",
                outcome="Chặn lỗi theo UI spec.md",
                solution="Theo UI spec.md",
                scope_in=["Chặn lỗi"],
                acceptance_criteria=["Chặn lỗi nhập liệu theo UI spec.md"],
                priority="high",
                priority_rationale="Bảo vệ toàn vẹn dữ liệu",
                dependency_keys=["happy-path"],
                source_refs=["BA:FEATURE"],
            ),
        ],
    )
    issues = find_proposal_style_issues(dirty)
    assert any("role tag" in item for item in issues)
    assert any("BA identifier code" in item for item in issues)
    assert any("snake_case/camelCase" in item for item in issues)


def test_validate_proposal_links_enforces_two_tasks_kind_and_dependency():
    valid = HulyTaskProposal(
        summary="Two-task feature plan",
        tasks=[
            ProposedTask(
                key="happy-path",
                task_kind="happy_path",
                title="Luồng chính và nhánh thay thế",
                outcome="Hoàn tất luồng chính",
                solution="Thực hiện theo thiết kế đính kèm",
                scope_in=["Main Flow", "Alternative Flows"],
                acceptance_criteria=["E2E luồng chính thành công → chuyển tiếp sang bước kế tiếp"],
                priority="high",
                priority_rationale="Hoàn thiện luồng nghiệp vụ chính",
                source_refs=["BA:FEATURE"],
            ),
            ProposedTask(
                key="exception-flows",
                task_kind="exception",
                title="Xử lý các ngoại lệ",
                outcome="Đảm bảo các chốt chặn ngoại lệ",
                solution="Kiểm tra ngoại lệ theo UI spec.md",
                scope_in=["Exception Flows"],
                acceptance_criteria=["Chặn phiên hết hạn theo thiết kế đính kèm"],
                priority="medium",
                priority_rationale="Ngăn lỗi nhập liệu",
                dependency_keys=["happy-path"],
                source_refs=["BA:FEATURE"],
            ),
        ],
    )
    validate_proposal_links(valid, {"BA:FEATURE"})

    missing_dep = valid.model_copy(
        update={
            "tasks": [
                valid.tasks[0],
                valid.tasks[1].model_copy(update={"dependency_keys": []}),
            ]
        }
    )
    with pytest.raises(ValueError, match="must depend on Task 1"):
        validate_proposal_links(missing_dep, {"BA:FEATURE"})

    wrong_kind = valid.model_copy(
        update={
            "tasks": [
                valid.tasks[0],
                valid.tasks[1].model_copy(update={"task_kind": "happy_path"}),
            ]
        }
    )
    with pytest.raises(ValueError, match="task_kind='exception'"):
        validate_proposal_links(wrong_kind, {"BA:FEATURE"})

    # Single happy_path task (when a feature has no Exception Flows) is allowed
    single_task = HulyTaskProposal(summary="Only one", tasks=[valid.tasks[0]])
    validate_proposal_links(single_task, {"BA:FEATURE"})
