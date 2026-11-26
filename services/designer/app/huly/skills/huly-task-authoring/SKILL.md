---
name: huly-task-authoring
description: Create Huly tasks/issues (single or bulk) in a named project with or without a parent epic — formatted descriptions, assignee, priority, due date, relations and screenshot attachments. Also the only working write path on this server (the official CLI fails with AccountMismatch). Use when the user asks to create/author a task, assign work to a dev, split an epic into dev tasks, or bulk-create issues with a manifest.
argument-hint: "<project like IMES> | <epic ID like IMES-1388> [manifest.json]"
metadata:
  author: anhle
  version: "1.1"
---

# Huly Task Authoring

Create tasks in the configured Huly workspace (`$HULY_ENDPOINT`) as **sub-tasks of an existing epic**
or as **standalone issues** in a project, with clean formatting, relations, and design screenshots
attached.

Two entry points:

```bash
# one issue, epic optional — the common case for "tạo task cho <dev>"
python3 ~/.commandcode/skills/huly-task-authoring/scripts/huly_tasks.py create-one \
    --project IMES --title "..." --body-file body.md \
    --assignee "Khai" --priority 1 --due "2026-11-18 17:00"

# many tasks under one epic
python3 ~/.commandcode/skills/huly-task-authoring/scripts/huly_tasks.py create --manifest tasks.json
```

## Non-negotiables

1. **Create only in the project the user names.** When the user names an epic, attach every task to
   that epic; when they say "no parent epic / task lẻ", create top-level issues in the named project
   (`--project IMES` without `--parent`). Never touch a different project, never edit or delete
   existing issues.
2. **Never print, echo or commit a token.** Read it from the configured token file (or env), pass via
   env or in-process.
3. **Do not invent task IDs or codes.** The title is the title; Huly assigns `IMES-<n>` itself.
4. **Do not describe UI in words when a screenshot exists.** Attach the image instead of writing
   screen IDs — local screen IDs and the real screens drift apart.
5. **Stop and report if the epic has no free sequence numbers or the token lacks write scope.**
6. **Always reply with the Huly links of what you created** — one link per task
   (`- IMES-1390: https://… — title`), epic link first when there is one. Never report a task
   without its link.
7. **Verify before reporting.** Run `verify` (epic) or re-read the issue (`create-one`) and check the
   description is a blob ref and the card fields are present. A task that only "looks created" in the
   API but spins forever in the UI is not done.

## Prerequisites (configured per machine)

Edit `~/.huly-skill/config.env` (copy from `config.env.example`):

| Need | Config key |
|---|---|
| Endpoint | `HULY_ENDPOINT` (default `https://huly.imespro.ai`) |
| Workspace UUID | `HULY_WS` (empty = read from token) |
| Workspace slug (for browser links) | `HULY_WORKSPACE_SLUG` |
| Read token (`read:*`) | `HULY_ASSISTANT_TOKEN_FILE` → path on this machine |
| Write token (`read:*`, `write:*`) | `HULY_TASK_TOKEN_FILE` → path on this machine |

Raw JWTs may be exported as `HULY_ASSISTANT_TOKEN` / `HULY_TASK_TOKEN` instead of files.

Check scope before writing:

```bash
python3 ~/.commandcode/skills/huly-task-authoring/scripts/huly_tasks.py check
```

## Workflow A — one issue (epic optional)

This is the path for "tạo task cho <dev> làm hôm nay, urgent, không có issue cha".

```bash
# 1. check scope + resolve people/project once
python3 ~/.commandcode/skills/huly-task-authoring/scripts/huly_tasks.py check
python3 ~/.commandcode/skills/huly-task-authoring/scripts/huly_tasks.py person "Khai"

# 2. dry-run first: confirms project, parent, assignee, status, priority, due
python3 ~/.commandcode/skills/huly-task-authoring/scripts/huly_tasks.py create-one \
    --project IMES --title "..." --body-file body.md \
    --assignee Khai --priority 1 --due "2026-11-18 17:00" --dry-run

# 3. create
python3 ~/.commandcode/skills/huly-task-authoring/scripts/huly_tasks.py create-one \
    --project IMES --title "..." --body-file body.md \
    --assignee Khai --priority 1 --due "2026-11-18 17:00" --json
```

Flags: `--project` (identifier/name/id), `--title`, `--body-file` **or** `--body`,
`--assignee` (name or id), `--parent` (epic identifier; omit for top-level), `--status`,
`--priority` (0 none, 1 urgent, 2 high, 3 medium, 4 low), `--due` (`YYYY-MM-DD` or
`YYYY-MM-DD HH:MM`, local time), `--image` (repeatable), `--dry-run`, `--json`.

The script assigns the next project sequence number, seeds `ISSUE_DEFAULTS`, uploads the body as a
ProseMirror blob, and advances `project.sequence` so the UI does not hand out the same number later.

Verify afterwards — the description must be a `<id>-description-<ts>` ref, not raw text:

```bash
python3 ~/.commandcode/skills/huly-data/scripts/huly_run.py -- issue view IMES-1422 --output json
```

## Workflow B — a list of tasks under one epic

### B1. Resolve the epic and people

```bash
# epic by number -> _id, title, project
python3 ~/.commandcode/skills/huly-task-authoring/scripts/huly_tasks.py epic IMES-1388

# person name -> person _id  (fuzzy, first match wins)
python3 ~/.commandcode/skills/huly-task-authoring/scripts/huly_tasks.py person "Thanh Son"
python3 ~/.commandcode/skills/huly-task-authoring/scripts/huly_tasks.py person "Van Ty"
```

Confirm the epic title with the user before creating anything.

### B2. Author the manifest

Copy `assets/manifest.example.json` and fill it in. One entry per task:

```json
{
  "epic": "IMES-1388",
  "tasks": [
    {
      "key": "be-fields",
      "title": "Bổ sung trường nghiệp vụ chuyển kho trên phiếu yêu cầu và phiếu xuất",
      "assignee": "Duong Thanh Son",
      "body_file": "bodies/be-fields.md",
      "images": ["~/images/design-01.jpg"],
      "relates": []
    },
    {
      "key": "fe-entry",
      "title": "Đổi điểm vào chuyển kho về trung tâm điều phối",
      "assignee": "Van Ty",
      "body_file": "bodies/fe-entry.md",
      "images": ["~/images/design-02.jpg"],
      "relates": ["be-fields"]
    }
  ]
}
```

`body_file` and `images` are relative to the manifest (or `~`/absolute on your machine).
`relates` takes keys from the same manifest (not Huly IDs) — the script creates first, then links.

### B3. Write each body

Follow `assets/task-body-template.md` exactly. Section order:

`Tài liệu tham chiếu` → `Mục tiêu` → `Hiện trạng` (chỉ khi sửa code cũ) → `Giải pháp` → `Cần chốt trước khi bắt đầu` → `Tiêu chí chấp nhận`

Rules (see `references/authoring-rules.md`):

- Reference only the PO document codes (`REQ-WMS-058 v2.2 — BR-058-C03`). **No local file paths.**
- Under *Hiện trạng*, put the survey line (date, branch/commit) as italic line, then what already
  exists, then what is missing. Omit the section entirely for a new feature.
- *Giải pháp* is **prose**, not a `- [ ]` checklist: what to change, where, in which direction,
  which fields/enums/status the other side consumes. A checklist inside one task is just that task
  split into sub-tasks the developer has to carve up again — if you want 3+ lines, split the task.
- *Tiêu chí chấp nhận* is a numbered table of observable outcomes QC can run.
- Omit `Cần chốt trước khi bắt đầu` when nothing blocks.
- No DoR/DoD, no "out of scope" section, no task-type/priority/estimate inside the body —
  those are Huly fields.

### B4. Create

```bash
python3 ~/.commandcode/skills/huly-task-authoring/scripts/huly_tasks.py create --manifest tasks.json --dry-run   # preview numbering first
python3 ~/.commandcode/skills/huly-task-authoring/scripts/huly_tasks.py create --manifest tasks.json
```

The script: reads the project sequence, assigns consecutive `number`/`identifier`, creates the
issue doc attached to the epic (`collection: subIssues`), converts the body to ProseMirror and
stores it as a description blob, uploads + attaches images, then sets relations. It writes
`created.json` (key → id, identifier, url) next to the manifest and prints a link block.

### B5. Verify

```bash
python3 ~/.commandcode/skills/huly-task-authoring/scripts/huly_tasks.py verify --epic IMES-1388
```

Checks: every task is attached to the epic, has a blob description, assignee set, images readable,
relations present, **and the Sub-issues panel wiring** (`parent.childInfo`/`subIssues` plus each
child's `collection`/`parents`). The last one is what the UI actually renders — `attachedTo` alone
is not enough, and a parent can look healthy in the row list while the panel is empty. Fix anything
reported before telling the user it is done; `relink --epic <IDENT>` repairs the wiring in bulk.

### B6. Report back with links

Always end your reply with the links — epic first, then each task:

```bash
python3 ~/.commandcode/skills/huly-task-authoring/scripts/huly_tasks.py links --epic IMES-1388
```

```
Epic: https://<your-host>/workbench/<workspace-slug>/tracker/IMES-1388 — [EPIC] Chuyển kho nội bộ
- IMES-1390: https://<your-host>/workbench/<workspace-slug>/tracker/IMES-1390 — API gợi ý kho khác…
- IMES-1391: https://<your-host>/workbench/<workspace-slug>/tracker/IMES-1391 — Hiển thị gợi ý…
```

URL shape is `https://<host>/workbench/<workspace-slug>/tracker/<IDENTIFIER>`. The host comes from
`HULY_ENDPOINT`, the slug from `HULY_WORKSPACE_SLUG`.

## Formatting rules that matter

Huly stores issue descriptions as a **ProseMirror JSON blob** referenced by
`<issueId>-description-<timestamp>`. Writing markdown into the `description` field directly shows
up blank in the UI. The script handles this — but if you ever write by hand, read
`references/huly-write-api.md`.

Colour: Huly markdown has no colour syntax. Convey priority through structure instead —
bold for key nouns, a blockquote (`>`) for the one warning that must not be missed, and a table
for acceptance criteria.

Table widths: tables must span the **full width** of the description pane. The converter therefore
leaves `colwidth` unset on every column so the columns flex, with one exception — a leading number
column (`#`, `STT`, `Số`) is pinned to 36px because it holds one or two characters and would
otherwise take an equal share of the width. Never set a fixed width on the content columns of an
acceptance-criteria table; that shrinks the table to a narrow band.

## Edge cases

| Symptom | Cause | Fix |
|---|---|---|
| `platform:status:AccountMismatch` from the official CLI | CLI sends the account UUID as `createdBy`/`modifiedBy`; server wants the 19-digit person id | Do not use the CLI for writes; use this skill's script (`_PID` = `account.socialIds[0]`) |
| `Insufficient token scope: delete:*` | write token has no delete scope | Cannot delete. Detach (`attachedTo: tracker:ids:NoParent`) or set `status: tracker:status:Canceled` + rename `[RÁC - IGNORE]`, then tell the user to remove it |
| Issue detail pane spins forever / never renders | doc created via raw tx without `ISSUE_DEFAULTS` (`parents`, `childInfo`, `relations`, `isDone`, `estimation`…) | Seed them — `create`/`create-one` do; for an existing doc `update_issue(id, ISSUE_DEFAULTS)` |
| Description shows blank in UI | markdown written to `description` instead of a blob ref | `repair-descriptions`, or `create-one` with `--body-file` |
| New issue missing from the project list | envelope had no `attachedTo` | Top-level must be `attachedTo: tracker:ids:NoParent`, `attachedToClass: tracker:class:Issue` |
| Parent task created but its **Sub-issues section is empty** in the UI, while `verify` lists the children | children carried `attachedTo` but not the fields the panel reads | `create`/`create-one` now call `link_child` (child `collection: subIssues` + `parents[]`, parent `childInfo[]` + `subIssues`). For legacy issues run `huly_tasks.py relink --epic <IDENT>`; `verify` now reports `Sub-issues panel wiring: BROKEN` |
| `unknown status "Todo" in project` from the CLI | CLI resolves statuses by `space == projectId`, but statuses live in `core:space:Model` | Pass the status **id** (`tracker:status:Todo`) with this script, not the CLI's `--status` name |
| Attachment unreadable (404 on `/files/<ws>?file=<id>`) | upload used the display name as multipart `filename` | Filename must equal the `fileId` used in the query string |
| Sequence collides with a UI-created issue | project `sequence` not advanced | `create`/`create-one` bump it; re-run `verify` |
| `No Huly token configured` | config file missing on this machine | copy `config.env.example` to `~/.huly-skill/config.env` and set the token paths |

## Files

- `scripts/huly_tasks.py` — the whole write path (`create-one`, `create`, `relink`, `attach`, `relate`, `verify`, `links`, `repair-descriptions`)
- `scripts/_huly_config.py` — config/env resolution (no machine paths baked in)
- `references/huly-write-api.md` — transaction/blob/attachment mechanics as verified against the server
- `references/authoring-rules.md` — content and language rules for task bodies
- `assets/task-body-template.md` — copy this for every task body
- `assets/manifest.example.json` — manifest skeleton
