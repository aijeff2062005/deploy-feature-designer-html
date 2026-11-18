"""Publish exported BA packages to the project's Assets (Sprint 4).

Each export stores the Common Envelope in platform storage and registers a
``ba_package`` artifact. The version only moves when the package content
(``payload``) changes, so re-exporting an unchanged feature is a no-op.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from managers.database.connection import SessionLocal
from managers.database.models import BaPackageModel

logger = logging.getLogger(__name__)


def feature_markdown_path(project_id: str, title: str, feature_id: str, files=None) -> str:
    """Return a readable, stable project Wiki path for a BA feature page."""
    from wiki.project_files import ProjectFileWiki

    files = files or ProjectFileWiki()
    normalized = unicodedata.normalize("NFKD", str(title or ""))
    ascii_title = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", ascii_title).strip("-").title() or "Feature"
    path = f"Features/Ba-{slug}.md"
    if not files.exists(project_id, path):
        return path
    metadata = files.read_metadata(project_id, path)
    if str(metadata.get("feature_id") or "") == str(feature_id):
        return path

    suffix = re.sub(r"[^A-Za-z0-9_-]", "-", str(feature_id)).strip("-")[:100] or "feature"
    return f"Features/Ba-{slug}-{suffix}.md"


def _render_wiki_template(name: str, **context: Any) -> str:
    from jinja2 import Environment, FileSystemLoader, StrictUndefined

    template_root = Path(__file__).resolve().parents[2] / "templates"
    environment = Environment(
        loader=FileSystemLoader(template_root),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
        undefined=StrictUndefined,
    )
    return environment.get_template(name).render(**context).strip() + "\n"


def build_ba_feature_body(title: str, description: str, feature_id: str, version: int,
                          artifact_id: str, storage_key: str, sections: dict[str, Any],
                          package_payload: dict[str, Any] | None = None) -> str:
    from managers.control.touchpoints import sanitize_touchpoints

    sections = dict(sections)
    for section_name in ("feature_definition", "use_cases", "business_rules", "bpmn_reference"):
        if not isinstance(sections.get(section_name), dict):
            sections[section_name] = {}
    use_cases_section = sections.get("use_cases") or {}
    touchpoints_section = sections.get("ui_touchpoints") or {}
    workflow_section = sections.get("workflow") or {}
    feature_definition = sections.get("feature_definition") or {}
    use_cases = use_cases_section.get("use_cases", []) if isinstance(use_cases_section, dict) else []
    touchpoints = touchpoints_section.get("ui_touchpoints", []) if isinstance(touchpoints_section, dict) else []
    touchpoints = touchpoints if isinstance(touchpoints, list) else []
    workflow_md = workflow_section.get("workflow_md_content", "") if isinstance(workflow_section, dict) else ""
    return _render_wiki_template(
        "wiki/ba-feature.md.j2",
        title=title,
        description=description,
        feature_id=feature_id,
        version=version,
        artifact_id=artifact_id,
        storage_key=storage_key,
        sections=sections,
        package_payload=package_payload or {},
        touchpoints=sanitize_touchpoints(
            touchpoints,
            use_cases,
            workflow_md=str(workflow_md or ""),
            feature_goal=str(feature_definition.get("goal") or ""),
        ),
    )


def build_project_readme(project_id: str, feature_id: str, title: str, description: str,
                         source_artifact_id: str, source_version: int, published_by: str | None,
                         sections: dict[str, Any] | None = None,
                         project_description_override: str | None = None) -> str:
    """Build README content from the Wiki template and approved feature data."""
    import psycopg
    from platform_core.db import database_url
    from wiki.project_files import ProjectFileWiki

    files = ProjectFileWiki()
    with psycopg.connect(database_url("core", "libpq")) as conn:
        project = conn.execute(
            "SELECT name, description FROM core.projects WHERE id = %s", (project_id,),
        ).fetchone()
    project_name = str(project[0] if project else project_id)
    user_description = str((project[1] if project else "") or "").strip()
    exists = files.exists(project_id, "README.md")
    current_metadata = files.read_metadata(project_id, "README.md") if exists else {}
    current_text = files.read(project_id, "README.md").decode("utf-8") if exists else ""
    parts = current_text.split("---", 2)
    old_body = parts[2].strip() if len(parts) == 3 else ""

    if project_description_override is not None:
        project_description = project_description_override.strip()
        description_source = "reviewed_in_ba_approval"
    elif user_description:
        project_description = user_description
        description_source = "project_creation_form"
    elif current_metadata.get("project_description"):
        project_description = str(current_metadata["project_description"])
        description_source = str(current_metadata.get("project_description_source") or "generated_from_approved_feature")
    else:
        project_description = description
        description_source = "generated_from_approved_feature"

    features = []
    for line in old_body.splitlines():
        marker = "<!-- feature-id:"
        if marker not in line or line.endswith(f"<!-- feature-id:{feature_id} -->"):
            continue
        feature_key = line.split(marker, 1)[1].split("-->", 1)[0].strip()
        link = re.match(r"^- \[(.*?)\]\(([^)]+)\):\s*(.*?)\s*<!--", line)
        if link:
            feature_title, feature_path, feature_description = link.groups()
            features.append({
                "id": feature_key,
                "title": feature_title,
                "path": feature_path,
                "description": feature_description,
            })
    feature_definition = (sections or {}).get("feature_definition") or {}
    feature_summary = str(
        feature_definition.get("summary")
        or description
        or feature_definition.get("goal")
        or ""
    ).strip()
    features.append({
        "id": feature_id,
        "title": title,
        "path": feature_markdown_path(project_id, title, feature_id, files),
        "description": feature_summary,
    })
    body = _render_wiki_template(
        "wiki/project-readme.md.j2",
        project_name=project_name,
        project_description=project_description,
        features=features,
    )
    old_version = int(current_metadata.get("version") or 0)
    metadata = {
        "title": project_name, "type": "project_readme", "project_id": project_id,
        "version": str(old_version + 1), "status": "approved",
        "project_description": project_description,
        "project_description_source": description_source,
        "source_feature_id": feature_id, "source_artifact_id": source_artifact_id,
        "source_artifact_version": source_version, "approved_by": published_by,
        "approved_at": datetime.now(timezone.utc).isoformat(),
    }
    return files._yaml(metadata) + body

def _content_hash(envelope: dict[str, Any]) -> str:
    body = envelope.get("payload", envelope)
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def publish_ba_package(
    session_id: str, envelope: dict[str, Any], created_by: str | None, *, sync_project_wiki: bool = True,
    project_description_override: str | None = None,
) -> dict[str, Any] | None:
    """Store + register the package; returns the artifact row (None without a project)."""
    from platform_core import artifacts
    from platform_core.storage import get_storage, project_key

    db = SessionLocal()
    try:
        pkg = db.get(BaPackageModel, session_id)
        project_id = getattr(pkg, "project_id", None)
        title = (getattr(pkg, "feature_title", None) or "BA package").strip()
        sections = {str(item.section_id): item.data or {} for item in (getattr(pkg, "sections", None) or [])}
    finally:
        db.close()
    if not project_id:
        return None

    digest = _content_hash(envelope)
    last = artifacts.latest("ba_package", session_id)
    if last and last["content_hash"] == digest:
        if sync_project_wiki:
            _publish_project_wiki(project_id, session_id, title, envelope, last, created_by, sections, project_description_override)
        if not artifacts.latest("bpmn", session_id):
            _publish_bpmn(project_id, session_id, title, envelope, last, created_by)  # packages from before BPMN assets
        return last
    version = (last["version"] + 1) if last else 1
    key = project_key(project_id, "ba", "packages", session_id, f"ba-package-v{version}.json")
    get_storage().put(
        key,
        json.dumps(envelope, ensure_ascii=False, indent=2, default=str).encode("utf-8"),
        content_type="application/json",
    )
    row, created = artifacts.register(
        project_id=project_id,
        kind="ba_package",
        source_module="ba",
        source_ref=session_id,
        title=title,
        storage_key=key,
        content_hash=digest,
        version=version,
        meta={"correlation_id": envelope.get("correlation_id"), "feature_id": envelope.get("feature_id")},
        created_by=created_by,
    )
    if created:
        logger.info("Published BA package %s v%s -> %s", session_id, version, key)
    if sync_project_wiki:
        _publish_project_wiki(project_id, session_id, title, envelope, row, created_by, sections, project_description_override)
    if created:
        _publish_bpmn(project_id, session_id, title, envelope, row, created_by)
    return row


def _publish_project_wiki(
    project_id: str, session_id: str, title: str, envelope: dict[str, Any],
    artifact: dict[str, Any], created_by: str | None, sections: dict[str, Any] | None = None,
    project_description_override: str | None = None,
) -> None:
    """Mirror only validated/exported BA packages into the project knowledge files."""
    try:
        from wiki.basic_memory import BasicMemoryClient
        from wiki.project_files import ProjectFileWiki

        files = ProjectFileWiki()
        payload = envelope.get("payload") if isinstance(envelope.get("payload"), dict) else envelope
        feature_id = str(envelope.get("feature_id") or payload.get("feature_id") or session_id)
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "-", feature_id).strip("-")[:100] or "feature"
        feature_path = feature_markdown_path(project_id, title, feature_id, files)
        feature_stem = feature_path.rsplit("/", 1)[-1][:-3]
        version = int(artifact.get("version") or 1)
        published_at = datetime.now(timezone.utc).isoformat()
        metadata = {
            "feature_id": feature_id,
            "source_session_id": session_id,
            "source_artifact_id": str(artifact.get("id") or ""),
            "source_version": version,
            "approval_status": "validated_export",
            "published_by": created_by,
            "published_at": published_at,
        }
        json_path = f"Features/{feature_stem}.v{version:04d}.json"
        files.write_json(project_id, json_path, {
            "schema_version": "1.0", "project_id": project_id,
            "feature_id": feature_id, "version": version, "status": "approved",
            "published_by": created_by, "published_at": published_at,
            "source_artifact_id": artifact.get("id"), "source_storage_key": artifact.get("storage_key"),
            "package": envelope,
        })
        files.write_json(project_id, f"Features/{feature_stem}.json", {
            "schema_version": "1.0", "project_id": project_id,
            "feature_id": feature_id, "version": version, "status": "approved",
            "published_by": created_by, "published_at": published_at,
            "source_artifact_id": artifact.get("id"), "source_storage_key": artifact.get("storage_key"),
            "package": envelope,
        })
        section_data = sections or {}
        body = build_ba_feature_body(
            title, str(payload.get("feature_description") or ""), feature_id, version,
            str(artifact.get("id") or ""), str(artifact.get("storage_key") or ""), section_data,
            package_payload=payload,
        )
        files.write_markdown(project_id, feature_path, title=title,
                             document_type="ba_feature", version=str(version), status="approved",
                             body=body, metadata=metadata)
        files.write_markdown(project_id, f"history/Features/{feature_stem}/v{version:04d}.md",
                             title=title, document_type="ba_feature", version=str(version),
                             status="approved", body=body, metadata=metadata)
        readme = build_project_readme(
            project_id, safe_id, title, str(payload.get("feature_description") or ""),
            str(artifact.get("id") or ""), version, created_by, section_data, project_description_override,
        )
        files.storage.put(files.key(project_id, "README.md"), readme.encode("utf-8"),
                          content_type="text/markdown; charset=utf-8")
    except Exception:  # noqa: BLE001 - Assets/export are authoritative; Wiki indexing can be retried.
        logger.exception("Could not sync validated BA package %s to Project Wiki", session_id)
        raise
    try:
        BasicMemoryClient().ensure_project(project_id, files.markdown_root(project_id))
    except Exception:  # noqa: BLE001 - Markdown is saved; Basic Memory indexing can be retried.
        logger.exception("Basic Memory indexing is pending for BA feature %s", session_id)


def _publish_bpmn(
    project_id: str, session_id: str, title: str, envelope: dict[str, Any], package: dict[str, Any], created_by: str | None
) -> None:
    """The package's reference BPMN as its own artifact (same version, linked to the package)."""
    from platform_core import artifacts
    from platform_core.storage import get_storage, project_key

    ref = ((envelope.get("payload") or {}).get("artifacts") or {}).get("bpmn_reference") or {}
    xml = str(ref.get("bpmn_xml_content") or "").strip()
    if not xml:
        return
    try:
        version = package["version"]
        key = project_key(project_id, "ba", "bpmn", session_id, f"bpmn-v{version}.bpmn")
        get_storage().put(key, xml.encode("utf-8"), content_type="application/xml")
        artifacts.register(
            project_id=project_id,
            kind="bpmn",
            source_module="ba",
            source_ref=session_id,
            title=f"{title} — BPMN",
            storage_key=key,
            content_hash=hashlib.sha256(xml.encode("utf-8")).hexdigest(),
            version=version,
            derived_from_id=package["id"],
            meta={"feature_id": session_id},
            created_by=created_by,
        )
        logger.info("Published BPMN %s v%s -> %s", session_id, version, key)
    except Exception:  # noqa: BLE001
        logger.exception("Publishing BPMN for %s failed", session_id)
