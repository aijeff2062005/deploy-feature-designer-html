"""Move legacy Project Wiki content out of PostgreSQL after verified copies exist.

Run from the Wiki package environment. The command is read-only unless --apply is
provided; conflicts are reported and their database rows are left untouched.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import psycopg2
from platform_core.db import database_url
from wiki.basic_memory import BasicMemoryClient
from wiki.db import connect, row_to_dict
from wiki.project_files import ProjectFileWiki


def _page_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "-", value).strip("-")[:100] or "page"


def _body_matches(files: ProjectFileWiki, content: str, project_id: str, page_id: str, version: str, body: str) -> bool:
    metadata, actual = files.parse_markdown(content)
    return (metadata.get("project_id") == project_id and metadata.get("page_id") == page_id
            and str(metadata.get("version")) == str(version) and actual.strip() == body.strip())


def migrate_wiki(apply: bool, project_id: str | None) -> dict[str, int]:
    conn = connect(Path("."))
    files = ProjectFileWiki()
    memory = BasicMemoryClient()
    if apply and not memory.endpoint:
        raise RuntimeError("BASIC_MEMORY_MCP_URL is required when applying the migration")
    counts = {"pages": 0, "revisions": 0, "sources": 0, "design_baselines": 0, "conflicts": 0}
    try:
        pages = conn.execute("SELECT * FROM wiki_page WHERE namespace='project' AND body IS NOT NULL" +
                             (" AND project_id=?" if project_id else ""), ((project_id,) if project_id else ())).fetchall()
        revisions_by_page: dict[str, list[dict]] = {}
        for row in conn.execute("SELECT r.* FROM wiki_revision r JOIN wiki_page p ON p.page_id=r.page_id "
                                "WHERE p.namespace='project' AND r.body_snapshot IS NOT NULL" +
                                (" AND p.project_id=?" if project_id else ""), ((project_id,) if project_id else ())):
            item = row_to_dict(row)
            revisions_by_page.setdefault(item["page_id"], []).append(item)
        for raw in pages:
            page = row_to_dict(raw)
            pid, page_id = page["project_id"], page["page_id"]
            if not pid:
                counts["conflicts"] += 1
                continue
            safe = _page_id(page_id)
            status = page["status"] if page["status"] in {"approved", "archived"} else "draft"
            body = str(page["body"] or "")
            markdown = f"# {page['title']}\n\n{body.strip()}\n\nSource Wiki page: `{page_id}`.\n"
            metadata = {"page_id": page_id, "bucket_id": page["bucket_id"], "namespace": "project",
                        "source_ids": page["source_ids"], "updated_by": page["owner"], "updated_at": page["updated_at"]}
            note = files._yaml({"title": page["title"], "type": page["bucket_id"], "project_id": pid,
                                "version": page["version"], "status": status, **metadata}) + markdown
            note_path = f"pages/{safe}.md"
            try:
                existing = memory.read_note(pid, files.markdown_root(pid), note_path)
                existing_text = str(existing.get("content", existing.get("markdown", "")) or "")
                if not _body_matches(files, existing_text, pid, page_id, str(page["version"]), markdown):
                    raise ValueError("Basic Memory note conflicts with PostgreSQL content")
            except RuntimeError as error:
                if "not_found" not in str(error).casefold() and "not found" not in str(error).casefold():
                    counts["conflicts"] += 1
                    continue
                existing_text = ""
            except ValueError:
                counts["conflicts"] += 1
                continue

            pending_snapshots: list[tuple[dict, str, str]] = []
            conflict = False
            for rev in revisions_by_page.get(page_id, []):
                rev_markdown = f"# {page['title']}\n\n{str(rev['body_snapshot']).strip()}\n\nSource Wiki page: `{page_id}`.\n"
                rev_path = f"history/pages/{safe}/revisions/{rev['revision_id']}.md"
                rev_meta = {"page_id": page_id, "revision_id": rev["revision_id"], "bucket_id": page["bucket_id"],
                            "namespace": "project", "source_ids": page["source_ids"]}
                rev_content = files._yaml({"title": page["title"], "type": page["bucket_id"], "project_id": pid,
                                           "version": rev["version"], "status": rev["status"], **rev_meta}) + rev_markdown
                key = files.key(pid, rev_path)
                if files.storage.exists(key):
                    try:
                        prior = files.storage.get(key).decode("utf-8")
                        meta, prior_body = files.parse_markdown(prior)
                        if (meta.get("page_id") != page_id or meta.get("revision_id") != rev["revision_id"]
                                or str(meta.get("version")) != str(rev["version"])
                                or prior_body.strip() != rev_markdown.strip()):
                            raise ValueError("snapshot key contains another revision")
                    except Exception:
                        conflict = True
                        break
                pending_snapshots.append((rev, key, rev_content))
            if conflict:
                counts["conflicts"] += 1
                continue
            if not apply:
                counts["pages"] += 1
                counts["revisions"] += len(pending_snapshots)
                continue
            memory.ensure_project(pid, files.markdown_root(pid))
            if not existing_text:
                memory.write_note(pid, files.markdown_root(pid), note_path, note)
            files.write_basic_memory_note(pid, note_path, note)
            for rev, key, content in pending_snapshots:
                if not files.storage.exists(key):
                    files.storage.put(key, content.encode("utf-8"), content_type="text/markdown; charset=utf-8")
                conn.execute("UPDATE wiki_revision SET body_snapshot=NULL, diff_ref=? WHERE revision_id=?",
                             (key, rev["revision_id"]))
            conn.execute("UPDATE wiki_page SET body=NULL WHERE page_id=?", (page_id,))
            conn.commit()
            counts["pages"] += 1
            counts["revisions"] += len(pending_snapshots)

        sources = conn.execute("SELECT * FROM wiki_source WHERE namespace='project' AND markdown_extract IS NOT NULL" +
                               (" AND project_id=?" if project_id else ""), ((project_id,) if project_id else ())).fetchall()
        for raw in sources:
            src = row_to_dict(raw)
            pid = src.get("project_id")
            if not pid:
                counts["conflicts"] += 1
                continue
            note_path = f"sources/{src['source_id']}.md"
            body = str(src["markdown_extract"] or "")
            metadata = {"source_id": src["source_id"], "filename": src["filename"], "sha256": src["sha256"],
                        "project_id": pid, "namespace": "project", "version": "1", "status": "approved",
                        "type": "source_extraction", "ingested_at": src["ingested_at"], "ingested_by": src["ingested_by"]}
            note = files._yaml(metadata) + body.rstrip() + "\n"
            if not apply:
                counts["sources"] += 1
                continue
            memory.ensure_project(pid, files.markdown_root(pid))
            try:
                prior = memory.read_note(pid, files.markdown_root(pid), note_path)
                prior_text = str(prior.get("content", prior.get("markdown", "")) or "")
                old_meta, old_body = files.parse_markdown(prior_text)
                if old_meta.get("source_id") != src["source_id"] or old_body.strip() != body.strip():
                    raise ValueError("Basic Memory source note conflicts with PostgreSQL extraction")
            except RuntimeError as error:
                if "not_found" not in str(error).casefold() and "not found" not in str(error).casefold():
                    counts["conflicts"] += 1
                    continue
                files.write_basic_memory_note(pid, note_path, note)
            except ValueError:
                counts["conflicts"] += 1
                continue
            key = files.key(pid, f"history/sources/{src['source_id']}.md")
            if files.storage.exists(key):
                prior_meta, prior_body = files.parse_markdown(files.storage.get(key).decode("utf-8"))
                if prior_meta.get("source_id") != src["source_id"] or prior_body.strip() != body.strip():
                    counts["conflicts"] += 1
                    continue
            else:
                files.storage.put(key, note.encode("utf-8"), content_type="text/markdown; charset=utf-8")
            conn.execute("UPDATE wiki_source SET markdown_extract=NULL WHERE source_id=?", (src["source_id"],))
            conn.commit()
            counts["sources"] += 1
        with psycopg2.connect(database_url("designer", "libpq")) as designer_conn:
            designer_conn.autocommit = False
            current_rows = designer_conn.cursor()
            current_rows.execute(
                "SELECT project_id,version,baseline_json,markdown_key,source_session_id,source_feature_id,updated_at "
                "FROM designer.project_design_wiki WHERE baseline_json IS NOT NULL" +
                (" AND project_id=%s" if project_id else ""), ((project_id,) if project_id else ()),
            )
            for row in current_rows.fetchall():
                pid, version, baseline_text, old_key, session_id, feature_id, updated_at = row
                baseline = json.loads(baseline_text)
                version_cursor = designer_conn.cursor()
                version_cursor.execute(
                    "SELECT version,baseline_json,markdown_key,source_session_id,source_feature_id,updated_at "
                    "FROM designer.project_design_wiki_versions WHERE project_id=%s AND baseline_json IS NOT NULL ORDER BY version",
                    (pid,),
                )
                version_rows = version_cursor.fetchall()
                planned: list[tuple[int, str, bytes]] = []
                bad = False
                for ver, ver_json, ver_old_key, ver_session, ver_feature, ver_updated in version_rows:
                    ver_baseline = json.loads(ver_json)
                    ver_markdown = "# Approved design system baseline\n\n"
                    if ver_old_key:
                        try:
                            old_content = files.storage.get(ver_old_key).decode("utf-8")
                            old_meta, old_body = files.parse_markdown(old_content)
                            if isinstance(old_meta.get("baseline"), dict) and old_meta["baseline"] != ver_baseline:
                                bad = True
                                break
                            if old_body.strip():
                                ver_markdown = old_body.rstrip() + "\n"
                        except FileNotFoundError:
                            pass
                    snap_meta = {"title": "Project Design System", "type": "design_system", "project_id": pid,
                                 "version": str(ver), "status": "approved", "baseline": ver_baseline,
                                 "source_session_id": ver_session, "source_feature_id": ver_feature,
                                 "approved_at": str(ver_updated)}
                    snap = (files._yaml(snap_meta) + ver_markdown).encode("utf-8")
                    snap_key = files.key(pid, f"history/design-system/v{ver}.md")
                    if files.storage.exists(snap_key):
                        try:
                            old_meta, _ = files.parse_markdown(files.storage.get(snap_key).decode("utf-8"))
                            if (old_meta.get("project_id") != pid or old_meta.get("version") != str(ver)
                                    or old_meta.get("baseline") != ver_baseline):
                                bad = True
                                break
                        except Exception:
                            bad = True
                            break
                    planned.append((ver, snap_key, snap))
                if not any(int(ver) == int(version) for ver, _key, _content in planned):
                    snap_meta = {"title": "Project Design System", "type": "design_system", "project_id": pid,
                                 "version": str(version), "status": "approved", "baseline": baseline,
                                 "source_session_id": session_id, "source_feature_id": feature_id,
                                 "approved_at": str(updated_at)}
                    current_body = "# Approved design system baseline\n"
                    if old_key:
                        try:
                            old_content = files.storage.get(old_key).decode("utf-8")
                            old_meta, old_body = files.parse_markdown(old_content)
                            if isinstance(old_meta.get("baseline"), dict) and old_meta["baseline"] != baseline:
                                bad = True
                            elif old_body.strip():
                                current_body = old_body.rstrip() + "\n"
                        except FileNotFoundError:
                            pass
                    current_key = files.key(pid, f"history/design-system/v{version}.md")
                    current_snapshot = (files._yaml(snap_meta) + current_body).encode("utf-8")
                    if files.storage.exists(current_key):
                        old_meta, _ = files.parse_markdown(files.storage.get(current_key).decode("utf-8"))
                        if old_meta.get("baseline") != baseline:
                            bad = True
                    planned.append((version, current_key, current_snapshot))
                if bad:
                    counts["conflicts"] += 1
                    continue
                if not apply:
                    counts["design_baselines"] += 1
                    continue
                memory.ensure_project(pid, files.markdown_root(pid))
                current_meta = {"title": "Project Design System", "type": "design_system", "project_id": pid,
                                "version": str(version), "status": "approved", "baseline": baseline,
                                "source_session_id": session_id, "source_feature_id": feature_id,
                                "approved_at": str(updated_at)}
                current_note = files._yaml(current_meta) + "# Approved design system baseline\n"
                note_path = "design-system.md"
                try:
                    old_note = memory.read_note(pid, files.markdown_root(pid), note_path)
                    old_text = str(old_note.get("content", old_note.get("markdown", "")) or "")
                    old_meta, _ = files.parse_markdown(old_text)
                    if isinstance(old_meta.get("baseline"), dict) and old_meta["baseline"] != baseline:
                        counts["conflicts"] += 1
                        continue
                    checksum = old_note.get("checksum")
                    if not checksum:
                        counts["conflicts"] += 1
                        continue
                    memory.write_note(pid, files.markdown_root(pid), note_path, current_note,
                                      expected_checksum=checksum)
                except RuntimeError as error:
                    if "not_found" not in str(error).casefold() and "not found" not in str(error).casefold():
                        counts["conflicts"] += 1
                        continue
                    memory.write_note(pid, files.markdown_root(pid), note_path, current_note)
                files.write_basic_memory_note(pid, note_path, current_note)
                design_version_cursor = designer_conn.cursor()
                for ver, key, content in planned:
                    if not files.storage.exists(key):
                        files.storage.put(key, content, content_type="text/markdown; charset=utf-8")
                    matching = next((item for item in version_rows if int(item[0]) == int(ver)), None)
                    source_session, source_feature, version_updated = (matching[3], matching[4], matching[5]) if matching else (session_id, feature_id, updated_at)
                    design_version_cursor.execute(
                        "INSERT INTO designer.project_design_wiki_versions "
                        "(project_id,version,baseline_json,markdown_key,source_session_id,source_feature_id,updated_at) "
                        "VALUES (%s,%s,NULL,%s,%s,%s,%s) ON CONFLICT(project_id,version) DO UPDATE SET "
                        "baseline_json=NULL,markdown_key=EXCLUDED.markdown_key",
                        (pid, ver, key, source_session, source_feature, version_updated),
                    )
                current_key = files.key(pid, f"history/design-system/v{version}.md")
                design_version_cursor.execute(
                    "UPDATE designer.project_design_wiki SET baseline_json=NULL,markdown_key=%s WHERE project_id=%s", (current_key, pid),
                )
                designer_conn.commit()
                counts["design_baselines"] += 1
        return counts
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", help="Migrate one PRJ-* project; default is all project Wiki content")
    parser.add_argument("--apply", action="store_true", help="Write/verify notes and snapshots, then clear content columns")
    args = parser.parse_args()
    print(json.dumps(migrate_wiki(args.apply, args.project), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
