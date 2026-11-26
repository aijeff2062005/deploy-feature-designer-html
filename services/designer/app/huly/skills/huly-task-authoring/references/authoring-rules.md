# Rules for task bodies

Language: Vietnamese technical prose. Field names, enum values, endpoint names and PO document
codes stay in their original form.

## Structure

Exactly these sections, in this order. Omit a section only when it genuinely does not apply.

```markdown
**Tài liệu tham chiếu:** REQ-WMS-058 v2.2 — BR-058-C03, BR-058-D08

## 🎯 Mục tiêu

One paragraph. The outcome the user of the feature observes when this task is done.

## 📌 Hiện trạng

*Khảo sát code ngày 17/11/2026 — branch `develop`, commit `abc1234`*

Only when modifying existing code. What already works (one or two sentences), then what is missing.

## 🛠 Giải pháp

Prose. What to change, where, in which direction — the fields/endpoints/enums that must exist, the
branch or status that must be added, the contract the other side consumes.

## ⚠️ Cần chốt trước khi bắt đầu

Only when something blocks. Say what is undecided and who decides.

## 📋 Tiêu chí chấp nhận

| # | Tình huống kiểm thử |
|---|---|
| 1 | Given X, when Y, the tester observes Z |
```

## Why there is no checklist

*Giải pháp* is prose, not `- [ ]` items. One task is one deliverable, so a checklist of outcomes
inside it would just be that task split into sub-tasks the developer has to carve up again. If you
find yourself wanting three or more checklist lines, you are describing several tasks — split them
(`references/authoring-rules.md` → *Sizing*).

A checklist is allowed only for a genuinely ordered, multi-phase job where the sequence itself is
the information and the developer cannot infer it (migrate data → deploy → backfill). Then use
`1.`-numbered outcomes under *Giải pháp*. Never mix checklist and prose.

## Do

- Reference **only** PO document codes: `REQ-WMS-058 v2.2`, `BR-058-…`, `TC-…`.
- Put the code-survey line (date, branch/commit) as an italic line directly under *Hiện trạng*,
  not inside the heading.
- Write *Giải pháp* so a developer knows what to build and QC knows what to test: direction, fields,
  enum/status names, and the contract the other side consumes.
- Write *Tiêu chí chấp nhận* as observable results — a value in a field, a status, a rendered
  state, an error the user sees. QC must be able to run them.
- The *Tiêu chí chấp nhận* table must span the **full width** of the description pane. Do not set
  a fixed width on its content columns — leave them flexible.
- Keep only the number column (`#`) of that table **thin**. It holds one or two characters, so it
  is pinned to 36px; an even split would give it a wide empty band and push the scenario text
  aside.
- Keep one task to one deliverable.
- Say "kiện" for LPN, "phiếu yêu cầu" for Material Request, "phiếu xuất" for Stock Entry,
  "việc lấy hàng" for Pick Task, "cất kệ" for Putaway, "đóng gói" for Pack — unless the exact
  technical name is what the developer must match.

## Do not

- Do not put a task code or ID in the title. Huly assigns `IMES-<n>`.
- Do not write task type, scope, priority, estimate, assignee or milestone into the body — those
  are Huly fields chosen in the tracker.
- Do not reference local files, local screen IDs or paths (`docs/samples/…`, `T23-spec.md`,
  `IMG-03`). They drift from the real screens. Attach the image instead.
- Do not add Definition of Ready / Definition of Done sections, or an "out of scope" section.
- Do not include pseudo-code, SQL, line numbers or file paths of the codebase. The developer
  reads the code; the task says what must change and how it will be judged.
- Do not invent error codes. If a case has no code in the PO error catalogue, say so in
  *Cần chốt trước khi bắt đầu*.
- Do not use abstractions the reader has to decode ("DoR", "AC", "NFR", "scope creep").

## Sizing

| Signal | Action |
|---|---|
| One field/one endpoint/one screen change | single task |
| Behaviour that must ship with an API and a screen | two tasks, one per side, related |
| You are writing 3+ checklist lines, or the job has several independent scenarios | split into several tasks |
| Blocked by an undecided rule | still create the task, list the blocker, leave it in Todo |

## Pairing backend and frontend

Give the backend task the contract and the rules; give the frontend task the interaction and the
states (loading, empty, error, success). Link the pair with `relates` in the manifest so the UI
work points at the API it consumes.
