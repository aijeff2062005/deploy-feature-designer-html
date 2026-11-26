**Tài liệu tham chiếu:** REQ-WMS-058 v2.2 — BR-058-C03, BR-058-D08

## 🎯 Mục tiêu

One paragraph describing the outcome a user of the feature observes once this task is done.
Name the actor, the trigger and the result.

## 📌 Hiện trạng

*Khảo sát code ngày 17/11/2026 — branch `develop`, commit `abc1234`*

**Only when the task modifies existing code.** What already works today, in one or two sentences,
then what is missing — the part a developer cannot see from the code alone (absent field, absent
branch, wrong status). Delete this section for a brand-new feature.

## 🛠 Giải pháp

Prose, not a checklist. What to change, where, in which direction: the fields/endpoints/enums that
must exist, the branch or status that must be added, the contract the other side consumes.
Enough for a developer to know what to build and for QC to know what to test — no pseudo-code, no
line ranges, no step-by-step recipe.

## ⚠️ Cần chốt trước khi bắt đầu

Delete this section when nothing blocks. Otherwise: what is undecided, and who decides.

## 📋 Tiêu chí chấp nhận

| # | Tình huống kiểm thử |
|---|---|
| 1 | Given a prepared record, when the tester does X, they observe Y |
| 2 | Given an invalid input, when submitted, the response is Z |
