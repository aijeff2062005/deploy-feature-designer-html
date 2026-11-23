"""Project scoped Markdown and JSON files in the platform's shared storage."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import PurePosixPath
from typing import Any

import yaml

from platform_core.storage import get_storage, project_key

_PROJECT = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
_PATH = re.compile(r"^[A-Za-z0-9_./-]{1,240}$")


class ProjectFileWiki:
    def __init__(self, storage=None):
        self.storage = storage or get_storage()

    @staticmethod
    def _parts(project_id: str, path: str) -> tuple[str, str]:
        project_id = (project_id or "").strip()
        path = (path or "").replace("\\", "/").strip("/")
        parsed = PurePosixPath(path)
        if not _PROJECT.fullmatch(project_id) or not _PATH.fullmatch(path) or parsed.is_absolute() or any(p in {"", ".", ".."} for p in parsed.parts):
            raise ValueError("Invalid project Wiki path or project_id")
        if parsed.suffix.lower() not in {".md", ".json", ".css", ".html", ".svg", ".png", ".jpg", ".jpeg", ".webp"}:
            raise ValueError("Unsupported project Wiki file type")
        return project_id, parsed.as_posix()

    @staticmethod
    def _yaml(metadata: dict[str, Any]) -> str:
        return "---\n" + "".join(f"{k}: {json.dumps(v, ensure_ascii=False)}\n" for k, v in metadata.items()) + "---\n\n"

    def key(self, project_id: str, path: str) -> str:
        project_id, path = self._parts(project_id, path)
        return project_key(project_id, "wiki", *PurePosixPath(path).parts)

    def markdown_root(self, project_id: str) -> str:
        if not _PROJECT.fullmatch(project_id or ""):
            raise ValueError("Invalid project_id")
        return project_key(project_id, "wiki")

    def write_markdown(self, project_id: str, path: str, *, title: str, document_type: str,
                       version: str, status: str, body: str, metadata: dict[str, Any] | None = None) -> str:
        if status not in {"draft", "approved", "archived"}:
            raise ValueError("Invalid project Wiki status")
        fields = {"title": title, "type": document_type, "project_id": project_id,
                  "version": version, "status": status, **(metadata or {})}
        content = self._yaml(fields) + body.rstrip() + "\n"
        self.storage.put(self.key(project_id, path), content.encode(), content_type="text/markdown; charset=utf-8")
        return hashlib.sha256(content.encode()).hexdigest()

    def write_basic_memory_note(self, project_id: str, path: str, content: str) -> str:
        """Write through Basic Memory when configured, then mirror to shared storage."""
        from wiki.basic_memory import BasicMemoryClient

        key = self.key(project_id, path)
        memory = BasicMemoryClient()
        if not memory.endpoint:
            self.storage.put(key, content.encode("utf-8"), content_type="text/markdown; charset=utf-8")
            return hashlib.sha256(content.encode("utf-8")).hexdigest()
        root = self.markdown_root(project_id)
        memory.ensure_project(project_id, root)
        checksum = None
        try:
            existing = memory.read_note(project_id, root, path)
            checksum = existing.get("checksum")
            if not checksum:
                raise RuntimeError("Basic Memory bm_read did not return a note checksum")
        except RuntimeError as error:
            message = str(error).casefold()
            if "not_found" not in message and "not found" not in message:
                raise
        result = memory.write_note(
            project_id, root, path, content, expected_checksum=checksum,
        )
        self.storage.put(key, content.encode("utf-8"), content_type="text/markdown; charset=utf-8")
        return str(result.get("checksum") or hashlib.sha256(content.encode("utf-8")).hexdigest())

    def sync_project_index(self, project_id: str, path: str, entries: list[dict[str, str]], *, title: str) -> str:
        """Update a managed link-only section in a project Wiki note and snapshot its prior version."""
        from wiki.basic_memory import BasicMemoryClient

        memory = BasicMemoryClient()
        if not memory.endpoint:
            raise RuntimeError("BASIC_MEMORY_MCP_URL is required to sync Project Wiki indexes")
        root = self.markdown_root(project_id)
        memory.ensure_project(project_id, root)
        checksum = None
        old = ""
        try:
            current = memory.read_note(project_id, root, path)
            checksum = current.get("checksum")
            old = str(current.get("content", current.get("markdown", "")) or "")
            if not checksum:
                raise RuntimeError("Basic Memory bm_read did not return a note checksum")
        except RuntimeError as error:
            if "not_found" not in str(error).casefold() and "not found" not in str(error).casefold():
                raise
            try:
                old = self.read(project_id, path).decode("utf-8")
            except Exception:
                old = ""

        begin, end = "<!-- BEGIN MANAGED PROJECT WIKI INDEX -->", "<!-- END MANAGED PROJECT WIKI INDEX -->"
        _, body = self.parse_markdown(old)
        if begin in body and end in body:
            body = body[:body.index(begin)].rstrip() + "\n\n" + body[body.index(end) + len(end):].lstrip()
        # Remove only the exact empty-state copy shipped by bootstrap; keep any
        # project-authored notes around it intact when approved links arrive.
        empty_states = (
            "No project-specific policies have been approved yet. Agents must not infer policy.\n\n",
            "No project-specific business rules have been approved yet. Agents must not invent rules.\n\n## Approved rules\n- None yet.\n\n",
            "No approved project-specific terms yet.\n",
            "No approved project-specific abbreviations yet.\n",
            "No approved project-specific definitions yet.\n",
            "None approved yet.\n",
        )
        for empty_state in empty_states:
            body = body.replace(empty_state, "")
        lines = [begin, "## Approved entries"]
        for entry in entries:
            target = entry.get("path", "")
            label = entry.get("title", target)
            lines.append(f"- [{label}]({target}) — `{entry.get('page_id', '')}` · v{entry.get('version', '1')}")
        if not entries:
            lines.append("- None approved yet.")
        lines.append(end)
        body = body.rstrip()
        if not body:
            body = f"# {title}"
        content = self._yaml({"title": title, "type": "project_wiki_index", "project_id": project_id,
                              "namespace": "project", "version": "1", "status": "approved"}) + body + "\n\n" + "\n".join(lines) + "\n"
        if old != content and old:
            snapshot_path = f"history/indexes/{path}/{uuid.uuid4().hex}.md"
            self.storage.put(self.key(project_id, snapshot_path), old.encode("utf-8"), content_type="text/markdown; charset=utf-8")
        result = memory.write_note(project_id, root, path, content, expected_checksum=checksum)
        self.storage.put(self.key(project_id, path), content.encode("utf-8"), content_type="text/markdown; charset=utf-8")
        return str(result.get("checksum") or hashlib.sha256(content.encode("utf-8")).hexdigest())

    def write_json(self, project_id: str, path: str, value: dict[str, Any]) -> str:
        encoded = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
        self.storage.put(self.key(project_id, path), encoded, content_type="application/json")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def parse_markdown(content: str) -> tuple[dict[str, Any], str]:
        """Read this module's JSON-compatible YAML frontmatter and Markdown body."""
        parts = content.split("---", 2)
        if len(parts) != 3 or parts[0].strip():
            return {}, content
        try:
            parsed = yaml.safe_load(parts[1])
        except yaml.YAMLError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed, parts[2].lstrip("\r\n")
        metadata: dict[str, Any] = {}
        for line in parts[1].splitlines():
            key, separator, value = line.partition(":")
            if not separator:
                continue
            try:
                metadata[key.strip()] = json.loads(value.strip())
            except json.JSONDecodeError:
                # Basic Memory normalizes JSON-written frontmatter into YAML and
                # may emit single-quoted scalars (for example ``version: '1'``).
                # json.loads does not accept YAML single quotes, so retain the
                # scalar value without its YAML quoting characters.
                scalar = value.strip()
                if len(scalar) >= 2 and scalar[0] == scalar[-1] == "'":
                    scalar = scalar[1:-1].replace("''", "'")
                elif len(scalar) >= 2 and scalar[0] == scalar[-1] == '"':
                    scalar = scalar[1:-1]
                metadata[key.strip()] = scalar
        return metadata, parts[2].lstrip("\r\n")

    def read(self, project_id: str, path: str) -> bytes:
        return self.storage.get(self.key(project_id, path))

    def read_metadata(self, project_id: str, path: str) -> dict[str, Any]:
        content = self.read(project_id, path).decode("utf-8")
        metadata, _ = self.parse_markdown(content)
        return metadata

    def exists(self, project_id: str, path: str) -> bool:
        return self.storage.exists(self.key(project_id, path))
