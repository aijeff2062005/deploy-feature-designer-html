"""MCP client for the private Basic Memory project index."""
from __future__ import annotations

import anyio
import json
import os
import re
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def project_name(project_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", project_id or ""):
        raise ValueError("Invalid project_id")
    return f"platform-{project_id.lower()}"


class BasicMemoryClient:
    def __init__(self, endpoint: str | None = None):
        self.endpoint = (endpoint or os.getenv("BASIC_MEMORY_MCP_URL", "")).strip()

    async def _call_async(self, tool: str, arguments: dict[str, Any]):
        async with streamable_http_client(self.endpoint) as streams:
            # MCP 1.x returned (reader, writer, session_id); current 2.x returns
            # only the transport streams. The session API itself is unchanged.
            reader, writer = streams[:2]
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                result = await session.call_tool(tool, arguments)
                if getattr(result, "is_error", getattr(result, "isError", False)):
                    detail = "\n".join(getattr(item, "text", "") for item in result.content).strip()
                    raise RuntimeError(f"Basic Memory {tool} call failed: {detail}")
                data = getattr(result, "structured_content", getattr(result, "structuredContent", None))
                if data is not None:
                    if isinstance(data, dict) and set(data) == {"result"}:
                        return data["result"]
                    return data
                content = "\n".join(getattr(item, "text", "") for item in result.content).strip()
                try:
                    return json.loads(content) if content else {}
                except json.JSONDecodeError:
                    return content

    def call(self, tool: str, arguments: dict[str, Any]):
        if not self.endpoint:
            raise RuntimeError("BASIC_MEMORY_MCP_URL is not configured")
        try:
            return anyio.run(self._call_async, tool, arguments)
        except BaseExceptionGroup as error:
            # MCP v2 can wrap a tool error raised inside its async context
            # managers in nested ExceptionGroups. Flatten those groups so
            # callers such as ensure_project can reliably recognize normal
            # states like ALREADY_EXISTS and actionable server errors.
            messages: list[str] = []

            def collect(exc: BaseException) -> None:
                if isinstance(exc, BaseExceptionGroup):
                    for child in exc.exceptions:
                        collect(child)
                else:
                    message = str(exc).strip()
                    if message:
                        messages.append(message)

            collect(error)
            raise RuntimeError("; ".join(messages) or "Basic Memory MCP call failed") from error

    def ensure_project(self, project_id: str, root: str) -> str:
        name = project_name(project_id)
        try:
            self.call("bm_project", {"command": "create", "name": name,
                                      # Basic Memory paths are relative to its configured
                                      # project root (shown by bm_project list as /projects/...),
                                      # not host/container absolute paths like /app/data/....
                                      "path": f"/{root.strip('/')}",
                                      "output_format": "json"})
        except Exception as error:
            if not any(x in str(error).casefold() for x in ("already_exists", "already exists", "project exists")):
                raise
        return name

    def read_note(self, project_id: str, root: str, identifier: str) -> dict[str, Any]:
        """Read the canonical Markdown note from its Basic Memory project."""
        name = self.ensure_project(project_id, root)
        result = self.call("bm_read", {
            "identifier": identifier,
            "project": name,
            "include_frontmatter": True,
            "output_format": "json",
        })
        if not isinstance(result, dict):
            raise RuntimeError("Basic Memory bm_read returned an unexpected response")
        return result

    def write_note(
        self,
        project_id: str,
        root: str,
        identifier: str,
        content: str,
        *,
        overwrite: bool = False,
        expected_checksum: str | None = None,
    ) -> dict[str, Any]:
        """Create or replace a canonical Markdown note with an optional revision guard."""
        name = self.ensure_project(project_id, root)
        arguments: dict[str, Any] = {
            "identifier": identifier,
            "content": content,
            "project": name,
            "output_format": "json",
        }
        if expected_checksum:
            arguments["expected_checksum"] = expected_checksum
        else:
            arguments["overwrite"] = overwrite
        result = self.call("bm_write", arguments)
        if not isinstance(result, dict):
            raise RuntimeError("Basic Memory bm_write returned an unexpected response")
        return result

    def edit_note(
        self,
        project_id: str,
        root: str,
        identifier: str,
        command: str,
        *,
        content: str | None = None,
        expected_checksum: str | None = None,
        **options: Any,
    ) -> dict[str, Any]:
        """Apply an incremental Basic Memory note edit, guarded against stale reads."""
        name = self.ensure_project(project_id, root)
        arguments: dict[str, Any] = {
            "identifier": identifier,
            "command": command,
            "project": name,
            "output_format": "json",
            **options,
        }
        if content is not None:
            arguments["content"] = content
        if expected_checksum:
            arguments["expected_checksum"] = expected_checksum
        result = self.call("bm_edit", arguments)
        if not isinstance(result, dict):
            raise RuntimeError("Basic Memory bm_edit returned an unexpected response")
        return result

    def search(
        self, project_id: str, root: str, query: str, limit: int = 10, *, status: str = "approved",
    ) -> list[dict[str, Any]]:
        name = self.ensure_project(project_id, root)
        # Scope by the Basic Memory project itself. Its BM25 index does not
        # expose arbitrary frontmatter fields such as project_id/status as
        # searchable filters, and hybrid mode can fail when semantic indexing
        # has no configured vector backend.
        result = self.call("bm_search", {"query": query, "mode": "text",
                                         "items": ["note", "observation", "relation"],
                                         "page_size": max(1, min(limit, 50)), "project": name,
                                         "output_format": "json"})
        if isinstance(result, dict):
            hits = result.get("results", result.get("matches", []))
            return [item for item in hits if isinstance(item, dict)] if isinstance(hits, list) else []
        return []
