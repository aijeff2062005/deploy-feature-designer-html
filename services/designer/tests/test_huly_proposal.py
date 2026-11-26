from app.huly.proposal import (
    HulyTaskProposal,
    ProposedTask,
    proposal_prompt,
    proposal_review_prompt,
    task_authoring_skill,
)


def test_task_authoring_skill_is_loaded_into_the_proposal_prompt():
    prompt = proposal_prompt(
        title="Feature title",
        draft={"goal": "Source goal"},
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
    assert "Feature title" in prompt
    assert "BA:FEATURE" in prompt


def test_review_prompt_audits_screen_based_splits_against_sources():
    candidate = HulyTaskProposal(
        summary="Candidate plan",
        tasks=[
            ProposedTask(
                key="journey-one",
                title="Deliver the user journey",
                outcome="Source-backed result",
                solution="Source-backed behavior",
                scope_in=["Source-backed scope"],
                acceptance_criteria=["Given a source condition, when an action occurs, then the result is visible"],
                priority="none",
                source_refs=["BA:FEATURE"],
            )
        ],
    )
    prompt = proposal_review_prompt(original_prompt="SOURCE EVIDENCE", proposal=candidate)

    assert "SOURCE EVIDENCE" in prompt
    assert "Screens are supporting evidence, never task boundaries by themselves" in prompt
    assert "CANDIDATE TASK PROPOSAL TO AUDIT" in prompt
    assert "Correct the proposal where needed" in prompt
