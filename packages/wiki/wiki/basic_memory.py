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
        async with streamable_http_client(self.endpoint) as (reader, writer, _):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                result = await session.call_tool(tool, arguments)
                if getattr(result, "isError", False):
                    detail = "\n".join(getattr(item, "text", "") for item in result.content).strip()
                    raise RuntimeError(f"Basic Memory {tool} call failed: {detail}")
                data = getattr(result, "structuredContent", None)
                if data is not None:
                    return data
                content = "\n".join(getattr(item, "text", "") for item in result.content).strip()
                try:
                    return json.loads(content) if content else {}
                except json.JSONDecodeError:
                    return content

    def call(self, tool: str, arguments: dict[str, Any]):
        if not self.endpoint:
            raise RuntimeError("BASIC_MEMORY_MCP_URL is not configured")
        return anyio.run(self._call_async, tool, arguments)

    def ensure_project(self, project_id: str, root: str) -> str:
        name = project_name(project_id)
        try:
            self.call("bm_project", {"command": "create", "name": name,
                                      "path": f"{os.getenv('BASIC_MEMORY_PROJECT_ROOT', '/app/data').rstrip('/')}/{root.strip('/')}",
                                      "output_format": "json"})
        except Exception as error:
            if not any(x in str(error).casefold() for x in ("already_exists", "already exists", "project exists")):
                raise
        return name

    def search(self, project_id: str, root: str, query: str, limit: int = 10) -> list[dict[str, Any]]:
        name = self.ensure_project(project_id, root)
        result = self.call("bm_search", {"query": query, "mode": "hybrid",
                                         "filters": {"project_id": project_id, "status": "approved"},
                                         "items": ["notes", "observations", "relations"],
                                         "page_size": max(1, min(limit, 50)), "project": name,
                                         "output_format": "json"})
        if isinstance(result, dict):
            hits = result.get("results", result.get("matches", []))
            return [item for item in hits if isinstance(item, dict)] if isinstance(hits, list) else []
        return []
