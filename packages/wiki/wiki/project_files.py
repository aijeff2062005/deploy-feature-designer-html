"""Project scoped Markdown and JSON files in the platform's shared storage."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import PurePosixPath
from typing import Any

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

    def write_json(self, project_id: str, path: str, value: dict[str, Any]) -> str:
        encoded = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
        self.storage.put(self.key(project_id, path), encoded, content_type="application/json")
        return hashlib.sha256(encoded).hexdigest()

    def read(self, project_id: str, path: str) -> bytes:
        return self.storage.get(self.key(project_id, path))

    def read_metadata(self, project_id: str, path: str) -> dict[str, Any]:
        content = self.read(project_id, path).decode("utf-8")
        parts = content.split("---", 2)
        if len(parts) != 3 or parts[0].strip():
            return {}
        metadata: dict[str, Any] = {}
        for line in parts[1].splitlines():
            key, separator, value = line.partition(":")
            if not separator:
                continue
            try:
                metadata[key.strip()] = json.loads(value.strip())
            except json.JSONDecodeError:
                metadata[key.strip()] = value.strip().strip('"')
        return metadata

    def exists(self, project_id: str, path: str) -> bool:
        return self.storage.exists(self.key(project_id, path))
