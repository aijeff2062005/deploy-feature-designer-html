# Huly feature-task planning and authoring

Use the attached BA package and approved design as evidence. This skill guides planning and
wording; it must not add business facts that are absent from those sources.

## Plan by outcomes (Fixed 2-Task Structure)

1. Read the feature goal, use cases, business rules, workflow and exceptions before looking at
   the screen list. Identify the user-visible outcomes and complete business journeys first.
2. Treat screens as evidence and scope references, not as task boundaries. Keep all steps of one
   cohesive journey together, even when the journey crosses several screens. Do not split a task
   because screens look like separate pages or implementation layers.
3. Split each Feature into **exactly 2 tasks**:
   - **Task 1 (`task_kind: "happy_path"`):** Covers the complete Main Flow and all Alternative Flows
     from the entry point to the exit point (including transition/handoff to the next Feature).
   - **Task 2 (`task_kind: "exception"`):** Covers all Exception Flows (session validation,
     boundary inputs, resource/availability checks, safety/constraint rules) and **must declare a
     dependency on Task 1** (`dependency_keys` containing Task 1's `key`).
4. **Role-agnostic & Natural Language:** Never split or prefix tasks by role (`[FE]`, `[BE]`, `[QA]`).
   Never include BA identifier codes (`BR-`, `US-`, `ASM-`, `SEC-`, `UC-`, `FR-`, `NFR-`, `REQ-`)
   or code variable names (`snake_case`, `camelCase`) anywhere in human-readable task fields
   (`title`, `outcome`, `current_state`, `solution`, `scope_in`, `scope_out`, `acceptance_criteria`,
   `priority_rationale`). Keep machine IDs strictly inside `source_refs`.
5. **Single Source of Truth:** Do not copy verbatim error message strings, detailed pricing/rate
   tables, or exact UI measurements/specs into the ticket body. Refer back to `"theo thiết kế đính kèm"`
   or `"theo UI spec.md"`.
6. **Priority & Rationale Defaults:**
   - **Task 1 (`happy_path`):** Default `priority: "high"` with a concise 1-sentence
     `priority_rationale` explaining how it delivers the primary end-to-end journey and handoff.
   - **Task 2 (`exception`):** Set `priority: "high"` when it protects session security,
     data/resource integrity, or safety rules; set `priority: "medium"` for light input
     validations. Always include a concise 1-sentence `priority_rationale`.
7. Before returning the proposal, check that the proposal has exactly 2 tasks (Task 1 `happy_path`,
   Task 2 `exception` depending on Task 1), no journey was fragmented by screen, no source-defined
   outcome or exception was silently dropped, and task scopes do not contradict each other.

## Write each task

- Use clear, natural Vietnamese and a title that names the capability or outcome being delivered (no
  `[FE]`/`[BE]`/`[QA]` prefixes, no BA codes, no `snake_case`/`camelCase` identifiers).
- **Solution (`solution`):** Write 1–2 concise sentences summarizing the overall implementation
  approach. Do NOT repeat the bullet list from `scope_in`.
- **Scope (`scope_in` / `scope_out`):**
  - **Task 1 `scope_in`:** Start with 1 overview bullet for building the screens and navigation flow
    per attached design, followed by 1 concise bullet per screen describing its core behavior.
  - **Task 1 `scope_out`:** Explicitly separate (a) exception guardrails owned by Task 2 and (b)
    downstream or out-of-scope Features.
  - **Task 2 `scope_in`:** Group by guardrail categories (session check -> input/boundary validation
    -> live availability/resource checks -> safety/business constraints) instead of listing
    fragmented micro-errors.
  - **Task 2 `scope_out`:** State that base screen layout and primary flow are completed in Task 1.
- **Synthesize Flowing Acceptance Criteria (DO NOT copy User Stories):**
  - Never write mechanical `"Với <given>; khi <when>; thì <then>"` sentences or copy User Story
    rows 1-to-1.
  - **Task 1 (`happy_path`):** Write 4–6 flowing Vietnamese criteria: 1 End-to-End scenario per valid
    branch (Main Flow and each Alternative Flow) opening with **`Kiểm thử luồng <tên nhánh> (End-to-End): ...`**
    (NEVER start with `"Hành trình ..."`) connected with `→` from entry to next-Feature
    handoff with verified data/calculation consistency, plus 1–2 criteria for overall design
    fidelity (`theo thiết kế đính kèm`) and automatic state/total recalculation.
  - **Task 2 (`exception`):** Write 3–5 group-level guardrail criteria covering all Exception Flows,
    referencing `"theo thiết kế đính kèm"` or `"theo UI spec.md"` instead of copying verbatim error
    messages.
- Keep priority, task type, assignee, deadline, estimate, Epic, and relations in Huly fields.
- Cite the supplied source IDs in `source_refs` only. Associate each task with its relevant BA
  sources and approved screens; do not cite every screen just because it exists.
- If a fact, expected result, error message, ownership boundary or dependency is unclear, record
  it as an open question for the affected task. Do not guess.
- Do not fabricate current implementation state, APIs, validations, values, owners, dates, estimates,
  or task splits. Do not mention local paths or internal implementation layers unless the source
  makes them part of the deliverable.

## Review the proposal before returning it

Compare both tasks against the BA package and design. Verify that: (1) there are exactly 2 tasks
(`task_kind: "happy_path"` for Task 1 covering Main Flow + Alternative Flows, `task_kind: "exception"`
for Task 2 covering all Exception Flows and depending on Task 1); (2) no `[FE]`/`[BE]`/`[QA]`
prefixes, BA codes (`BR-`, `US-`, `ASM-`, `SEC-`), or `snake_case`/`camelCase` variable names appear
in task content; (3) verbatim error strings, detailed pricing tables, and UI specs are not copied
into the body (use `"theo thiết kế đính kèm"` or `"theo UI spec.md"`); (4) `acceptance_criteria`
are synthesized into natural, flowing Vietnamese (rewrite any `"Với ...; khi ...; thì ..."` or
copied User Story lines, and rewrite any criterion opening with `"Hành trình ..."` into
`"Kiểm thử luồng ... (End-to-End): ..."`); and (5) `priority` is non-`"none"` with a concise 1-sentence
`priority_rationale`. Recheck the revised task scopes and acceptance criteria against `source_refs`.
