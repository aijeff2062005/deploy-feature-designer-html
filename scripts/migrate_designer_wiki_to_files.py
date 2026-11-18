"""Export existing PostgreSQL Designer Wiki baselines to project JSON/Markdown."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote_plus

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "wiki"))
sys.path.insert(0, str(ROOT / "packages" / "platform_core"))

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402
from platform_core.storage import get_storage  # noqa: E402
from wiki.basic_memory import BasicMemoryClient  # noqa: E402
from wiki.project_files import ProjectFileWiki  # noqa: E402


def _dsn() -> str:
    env = os.environ
    return (f"postgresql://{quote_plus(env.get('POSTGRES_USER', 'platform'))}:"
            f"{quote_plus(env.get('POSTGRES_PASSWORD', ''))}@{env.get('POSTGRES_HOST', 'localhost')}:"
            f"{env.get('POSTGRES_PORT', '5432')}/{env.get('POSTGRES_DB', 'platform')}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", help="export one project; default exports all")
    parser.add_argument("--apply", action="store_true", help="write files; default is a dry run")
    args = parser.parse_args()
    with psycopg.connect(_dsn(), row_factory=dict_row) as db:
        db.execute('SET search_path TO "designer"')
        if args.project_id:
            rows = db.execute("SELECT * FROM project_design_wiki WHERE project_id=%s", (args.project_id,)).fetchall()
        else:
            rows = db.execute("SELECT * FROM project_design_wiki ORDER BY project_id").fetchall()
        versions = {}
        for row in rows:
            versions[row["project_id"]] = db.execute(
                "SELECT * FROM project_design_wiki_versions WHERE project_id=%s ORDER BY version",
                (row["project_id"],),
            ).fetchall()

    print(f"Found {len(rows)} existing Designer Project Wiki baseline(s)")
    for row in rows:
        print(f"  {row['project_id']} v{row['version']}")
    if not args.apply:
        print("Dry run. Add --apply to export JSON, Markdown and register Basic Memory projects.")
        return 0

    storage = get_storage()
    files = ProjectFileWiki(storage)
    for row in rows:
        project_id = row["project_id"]
        baseline = json.loads(row["baseline_json"])
        for item in versions[project_id]:
            version_baseline = json.loads(item["baseline_json"])
            payload = {
                **version_baseline, "schema_version": "1.0", "project_id": project_id,
                "version": item["version"], "status": "approved",
                "approved_by": "legacy-unknown", "approved_at": "legacy-unknown",
                "legacy_updated_at": item["updated_at"],
                "source_session_id": item["source_session_id"],
                "source_feature_id": item["source_feature_id"],
            }
            original = storage.get(item["markdown_key"]).decode("utf-8") if item.get("markdown_key") else "# Project Design System\n"
            metadata = {"source_session_id": item["source_session_id"],
                        "source_feature_id": item["source_feature_id"]}
            files.write_json(project_id, f"design-system/design-system.v{item['version']:04d}.json", payload)
            files.write_markdown(project_id, f"history/design-system/v{item['version']:04d}.md",
                                 title="Project Design System", document_type="design_system",
                                 version=str(item["version"]), status="approved", body=original,
                                 metadata={**metadata, "approved_by": "legacy-unknown",
                                           "approved_at": "legacy-unknown"})
        current = {
            **baseline, "schema_version": "1.0", "project_id": project_id,
            "version": row["version"], "status": "approved",
            "approved_by": "legacy-unknown", "approved_at": "legacy-unknown",
            "legacy_updated_at": row["updated_at"],
            "source_session_id": row["source_session_id"], "source_feature_id": row["source_feature_id"],
        }
        current_markdown = storage.get(row["markdown_key"]).decode("utf-8") if row.get("markdown_key") else "# Project Design System\n"
        files.write_json(project_id, "design-system/design-system.json", current)
        files.write_markdown(project_id, "design-system.md", title="Project Design System",
                             document_type="design_system", version=str(row["version"]), status="approved",
                             body=current_markdown, metadata={"source_session_id": row["source_session_id"],
                                                             "source_feature_id": row["source_feature_id"],
                                                             "approved_by": "legacy-unknown",
                                                             "approved_at": "legacy-unknown"})
        BasicMemoryClient().ensure_project(project_id, files.markdown_root(project_id))
    print("Designer Project Wiki files exported and Basic Memory projects registered.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
