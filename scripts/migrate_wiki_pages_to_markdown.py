"""Export approved project Wiki pages and validated BA packages to shared files."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "wiki"))
sys.path.insert(0, str(ROOT / "packages" / "platform_core"))

from platform_core import artifacts  # noqa: E402
from platform_core.storage import get_storage  # noqa: E402
from wiki.basic_memory import BasicMemoryClient  # noqa: E402
from wiki.project_files import ProjectFileWiki  # noqa: E402
from wiki.paths import wiki_service_root  # noqa: E402
from wiki.service import WikiService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--apply", action="store_true", help="write files; default is a dry run")
    args = parser.parse_args()
    service = WikiService(wiki_service_root())
    try:
        pages = [page for page in service.list_pages(status="approved", project_id=args.project_id)
                 if page.namespace == "project"]
        packages = [item for item in artifacts.list_for_project(args.project_id)
                    if item.get("kind") == "ba_package" and item.get("storage_key")]
        packages.sort(key=lambda item: (str(item["source_ref"]), int(item["version"])))
        print(f"Project {args.project_id}: {len(pages)} approved project pages")
        print(f"Project {args.project_id}: {len(packages)} validated BA package artifact(s)")
        for page in pages:
            print(f"  {page.page_id} v{page.version}: {page.title}")
        if not args.apply:
            print("Dry run. Add --apply to export these pages.")
            return 0
        files = ProjectFileWiki()
        for page in pages:
            safe_id = re.sub(r"[^A-Za-z0-9_-]", "-", page.page_id).strip("-")[:100] or "page"
            approval = service.conn.execute(
                "SELECT actor, created_at FROM wiki_revision WHERE page_id=? AND status='approved' "
                "ORDER BY created_at DESC LIMIT 1", (page.page_id,),
            ).fetchone()
            body = f"# {page.title}\n\n{page.body.strip()}\n\nSource Wiki page: `{page.page_id}`."
            metadata = {"page_id": page.page_id, "bucket_id": page.bucket_id,
                        "namespace": page.namespace, "source_ids": page.source_ids,
                        "updated_by": page.owner, "updated_at": page.updated_at,
                        "approved_by": approval["actor"] if approval else "legacy-unknown",
                        "approved_at": approval["created_at"] if approval else "legacy-unknown"}
            for path in (f"pages/{safe_id}.md", f"history/pages/{safe_id}/v{page.version}.md"):
                files.write_markdown(args.project_id, path, title=page.title,
                                     document_type=page.bucket_id, version=page.version,
                                     status="approved", body=body, metadata=metadata)
        storage = get_storage()
        for artifact in packages:
            envelope = json.loads(storage.get(artifact["storage_key"]).decode("utf-8"))
            payload = envelope.get("payload") if isinstance(envelope.get("payload"), dict) else envelope
            feature_id = str(envelope.get("feature_id") or payload.get("feature_id") or artifact["source_ref"])
            safe_id = re.sub(r"[^A-Za-z0-9_-]", "-", feature_id).strip("-")[:100] or "feature"
            version = int(artifact["version"])
            metadata = {
                "feature_id": feature_id, "source_session_id": artifact["source_ref"],
                "source_artifact_id": artifact["id"], "source_version": version,
                "approval_status": "validated_export", "published_by": artifact.get("created_by"),
                "published_at": str(artifact.get("created_at") or "legacy-unknown"),
            }
            contract = {
                "schema_version": "1.0", "project_id": args.project_id,
                "feature_id": feature_id, "version": version, "status": "approved",
                "published_by": artifact.get("created_by"),
                "published_at": str(artifact.get("created_at") or "legacy-unknown"),
                "source_artifact_id": artifact["id"], "source_storage_key": artifact["storage_key"],
                "package": envelope,
            }
            files.write_json(args.project_id, f"features/{safe_id}.v{version:04d}.json", contract)
            files.write_json(args.project_id, f"features/{safe_id}.json", contract)
            body = "\n".join([
                f"# {artifact['title']}", "", "Validated BA package exported for Designer handoff.", "",
                f"- Feature ID: `{feature_id}`", f"- Package version: `{version}`",
                f"- Source artifact: `{artifact['id']}`", f"- Source JSON: `{artifact['storage_key']}`", "",
                "## Approved package content", "", "```json",
                json.dumps(envelope, ensure_ascii=False, indent=2, default=str), "```", "",
            ])
            for path in (f"features/{safe_id}.md", f"history/features/{safe_id}/v{version:04d}.md"):
                files.write_markdown(args.project_id, path, title=artifact["title"],
                                     document_type="ba_feature", version=str(version),
                                     status="approved", body=body, metadata=metadata)

        BasicMemoryClient().ensure_project(args.project_id, files.markdown_root(args.project_id))
        print("Exported. Basic Memory project registered; indexing follows its file watcher.")
        return 0
    finally:
        service.close()


if __name__ == "__main__":
    raise SystemExit(main())
