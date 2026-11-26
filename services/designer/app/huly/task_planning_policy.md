# Huly feature-task planning and authoring

Use the attached BA package and approved design as evidence. This skill guides planning and
wording; it must not add business facts that are absent from those sources.

## Plan by outcomes

1. Read the feature goal, use cases, business rules, workflow and exceptions before looking at
   the screen list. Identify the user-visible outcomes and complete business journeys first.
2. Treat screens as evidence and scope references, not as task boundaries. Keep all steps of one
   cohesive journey together, even when the journey crosses several screens. Do not split a task
   because screens look like separate pages or implementation layers.
3. Create a separate task only when the sources support a separately verifiable business outcome,
   an independently deliverable capability, or a real prerequisite. Group related validation and
   exception cases when they protect the same journey or business outcome.
4. Do not target a fixed number of tasks. A feature may need one task or several; derive the split
   from outcomes, independently testable work and actual dependencies in the source.
5. Before returning the proposal, check that no journey was fragmented by screen, no source-defined
   outcome or exception was silently dropped, task scopes do not contradict each other, and every
   dependency represents a real order constraint.

## Write each task

- Use clear Vietnamese and a title that names the capability or outcome being delivered.
- State the observable result for the user or business. Describe the behavior to deliver, the
  included scope, any explicitly out-of-scope work, and acceptance scenarios a tester can run.
- Use the order: references; goal; current state only when the source documents existing behavior;
  solution; unresolved decisions only when they block; acceptance criteria.
- Keep priority, task type, assignee, deadline, estimate, Epic, and relations in Huly fields. A
  priority rationale must be grounded in source evidence or a documented dependency.
- Cite the supplied source IDs. Associate each task with its relevant BA sources and approved
  screens; do not cite every screen just because it exists.
- Write acceptance criteria as observable Given/When/Then outcomes, derived from use cases,
  business rules, workflow exceptions, or approved screen behavior. Do not substitute generic
  “matches the design” statements for missing requirements.
- If a fact, expected result, error message, ownership boundary or dependency is unclear, record
  it as an open question for the affected task. Do not guess.
- Do not fabricate current implementation state, APIs, validations, values, owners, dates, estimates,
  or task splits. Do not mention local paths or internal implementation layers unless the source
  makes them part of the deliverable.

## Review the proposal before returning it

Compare every task against the BA package and design. If multiple tasks divide the screens of one
end-to-end journey, merge them unless the source proves that each part can ship and be accepted
independently. Keep exception work separate from the happy path only when it is a distinct,
testable deliverable; link it to the main-flow task when it has a real dependency. Recheck the
revised task scopes and acceptance criteria against the supplied source IDs.
