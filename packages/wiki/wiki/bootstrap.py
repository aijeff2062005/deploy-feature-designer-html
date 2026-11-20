"""Create the initial, project-scoped knowledge structure in Basic Memory."""
from __future__ import annotations

from typing import Any

from wiki.basic_memory import BasicMemoryClient
from wiki.project_files import ProjectFileWiki

BOOTSTRAP_VERSION = "1"


def _documents(project_name: str, description: str, huly_url: str, huly_project: dict[str, Any] | None = None) -> dict[str, tuple[str, str]]:
    overview = description.strip() or "Project description has not been provided."
    return {
        "README.md": ("project_readme", (
            f"# {project_name}\n\n{overview}\n\n## Knowledge map\n"
            "- [Project overview](PROJECT.md)\n- [Huly](HULY.md)\n- [Policies](POLICY.md)\n"
            "- [Business rules](RULES.md)\n- [Design system](design-system.md)\n"
            "- [Templates](TEMPLATES/README.md)\n- [Terms](GLOSSARY/TERMS.md)\n"
            "- [Abbreviations](GLOSSARY/ABBREVIATIONS.md)\n- [Definitions](GLOSSARY/DEFINITIONS.md)\n"
        )),
        "PROJECT.md": ("project_overview", f"# {project_name}\n\n{overview}\n"),
        "HULY.md": ("project_huly", (
            f"# Huly project link\n\nPlatform project: {project_name}\n\n"
            f"Huly project: {(huly_project or {}).get('name') or 'Not configured'}\n\n"
            f"Huly project ID: {(huly_project or {}).get('huly_id') or 'Not configured'}\n\n"
            f"Huly URL: {huly_url.strip() or 'Not configured'}\n"
        )),
        "POLICY.md": ("project_policy", (
            "# Project policies\n\nNo project-specific policies have been approved yet. "
            "Agents must not infer policy.\n\n## Security and access\n- Not defined.\n\n"
            "## Privacy and data handling\n- Not defined.\n\n## Retention and compliance\n- Not defined.\n"
        )),
        "RULES.md": ("project_rules", (
            "# Project rules\n\nNo project-specific business rules have been approved yet. "
            "Agents must not invent rules.\n\n## Approved rules\n- None yet.\n\n"
            "Each rule must include an ID, description, source, approval status, and version.\n"
        )),
        "design-system.md": ("design_system", (
            "# Project design system\n\nNo design system has been approved yet. "
            "Approved design decisions and baselines belong here.\n\n"
            "## Brand and visual direction\n- Not defined.\n\n## Color, typography, and spacing\n- Not defined.\n\n"
            "## Components and interaction rules\n- Not defined.\n"
        )),
        "TEMPLATES/README.md": ("project_templates", (
            "# Project templates\n\nStore approved project templates in this folder. "
            "Each template should include purpose, owner, version, and source.\n\nNone approved yet.\n"
        )),
        "GLOSSARY/TERMS.md": ("project_terms", "# Project terms\n\nNo approved project-specific terms yet.\n"),
        "GLOSSARY/ABBREVIATIONS.md": ("project_abbreviations", "# Abbreviations\n\nNo approved project-specific abbreviations yet.\n"),
        "GLOSSARY/DEFINITIONS.md": ("project_definitions", "# Definitions\n\nNo approved project-specific definitions yet.\n"),
    }


def bootstrap_project_wiki(
    project_id: str, project_name: str, description: str = "", huly_url: str = "",
    huly_project: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create missing starter notes, mirror them, and snapshot bootstrap content."""
    files = ProjectFileWiki()
    memory = BasicMemoryClient()
    if not memory.endpoint:
        raise RuntimeError("BASIC_MEMORY_MCP_URL is required to bootstrap project knowledge")
    root = files.markdown_root(project_id)
    memory.ensure_project(project_id, root)
    created: list[str] = []
    existing: list[str] = []
    for path, (document_type, body) in _documents(project_name, description, huly_url, huly_project).items():
        try:
            current = memory.read_note(project_id, root, path)
            content = str(current.get("content", current.get("markdown", "")) or "")
            if not content:
                raise RuntimeError(f"Basic Memory returned empty note {path}")
            existing.append(path)
        except RuntimeError as error:
            message = str(error).casefold()
            if "not_found" not in message and "not found" not in message:
                raise
            metadata = {
                "title": path.rsplit("/", 1)[-1].removesuffix(".md").replace("_", " "),
                "type": document_type,
                "project_id": project_id,
                "namespace": "project",
                "version": BOOTSTRAP_VERSION,
                "status": "draft",
                "bootstrap_version": BOOTSTRAP_VERSION,
            }
            content = files._yaml(metadata) + body.rstrip() + "\n"
            try:
                memory.write_note(project_id, root, path, content)
            except RuntimeError as write_error:
                if "already_exists" not in str(write_error).casefold() and "already exists" not in str(write_error).casefold():
                    raise
                current = memory.read_note(project_id, root, path)
                content = str(current.get("content", current.get("markdown", "")) or "")
                if not content:
                    raise RuntimeError(f"Basic Memory returned empty note {path}")
                existing.append(path)
            else:
                created.append(path)

        key = files.key(project_id, path)
        files.storage.put(key, content.encode("utf-8"), content_type="text/markdown; charset=utf-8")
        snapshot_key = files.key(project_id, f"history/bootstrap/v{BOOTSTRAP_VERSION}/{path}")
        if not files.storage.exists(snapshot_key):
            files.storage.put(snapshot_key, content.encode("utf-8"), content_type="text/markdown; charset=utf-8")

    return {"project_id": project_id, "template_version": BOOTSTRAP_VERSION, "created": created, "already_present": existing}
