from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
import httpx2

from ..core.errors import AppError


@asynccontextmanager
async def _session(settings: Any, *, url: str | None = None) -> AsyncIterator[ClientSession]:
    if not settings.huly_token.strip() or not settings.huly_mcp_auth_token.strip():
        raise AppError(
            "HULY_NOT_CONFIGURED",
            "Huly is not configured",
            "Add the Huly API token and internal MCP authentication token to the server environment.",
            503,
        )
    async with httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {settings.huly_mcp_auth_token}"},
        timeout=httpx2.Timeout(45.0),
    ) as http_client:
        async with streamable_http_client(url or settings.huly_mcp_url, http_client=http_client) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


def _decode_result(result: Any) -> Any:
    # The Python MCP SDK exposes Pydantic snake_case attributes, while the wire
    # protocol uses camelCase. Support both so tool errors are never mistaken
    # for successful responses with a missing issue identifier.
    if getattr(result, "is_error", getattr(result, "isError", False)):
        detail = next(
            (str(getattr(item, "text", "")).strip() for item in getattr(result, "content", []) if getattr(item, "text", None)),
            "Huly MCP could not complete the request.",
        )
        raise AppError("HULY_REQUEST_FAILED", "Huly request failed", detail[:600], 502)
    structured = getattr(result, "structured_content", getattr(result, "structuredContent", None))
    if structured is not None:
        return structured
    for item in getattr(result, "content", []):
        text = getattr(item, "text", None)
        if not text:
            continue
        try:
            return json.loads(text)
        except (TypeError, json.JSONDecodeError):
            return {"text": text}
    return {}


def _items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("projects", "issues", "issue", "createdIssue", "created_issue", "items", "results", "result", "data"):
            nested = value.get(key)
            if isinstance(nested, (list, dict)):
                found = _items(nested)
                if found:
                    return found
        if any(key in value for key in ("id", "_id", "identifier", "name", "title")):
            return [value]
    return []


async def list_projects(settings: Any) -> list[dict[str, str]]:
    try:
        async with _session(settings) as session:
            result = await session.call_tool(
                "list_projects", arguments={"workspace": settings.huly_workspace}
            )
    except AppError:
        raise
    except Exception as error:
        raise AppError("HULY_UNAVAILABLE", "Huly is unavailable", "Could not load Huly projects. Check the MCP connection and Huly token configuration.", 502) from error
    projects = []
    for item in _items(_decode_result(result)):
        identifier = str(item.get("identifier") or item.get("shortId") or item.get("code") or "").strip()
        raw_id = str(item.get("id") or item.get("_id") or item.get("projectId") or identifier).strip()
        name = str(item.get("name") or item.get("title") or identifier or raw_id).strip()
        if not name:
            continue
        projects.append({"id": identifier or raw_id, "huly_id": raw_id, "name": name, "identifier": identifier or raw_id})
    return projects


async def create_issue(
    settings: Any,
    *,
    project: str,
    title: str,
    description: str,
    attachments: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    try:
        async with _session(settings) as session:
            tools = await session.list_tools()
            tool = next((item for item in tools.tools if item.name == "create_issue"), None)
            if tool is None:
                raise AppError("HULY_TOOL_UNAVAILABLE", "Huly issue creation unavailable", "The configured MCP server does not expose create_issue.", 502)
            # MCP's Python SDK models expose the wire field ``inputSchema`` as
            # ``input_schema``. Read both forms so required arguments (notably
            # the Huly project identifier) are mapped into the tool call.
            schema = getattr(tool, "input_schema", getattr(tool, "inputSchema", {})) or {}
            properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
            arguments: dict[str, Any] = {}
            for key in properties:
                normalized = key.casefold().replace("_", "")
                if normalized in {"project", "projectid", "projectidentifier", "projectkey"}:
                    arguments[key] = project
                elif normalized in {"title", "name"}:
                    arguments[key] = title
                elif normalized in {"description", "body"}:
                    arguments[key] = description
                elif normalized == "descriptionformat":
                    arguments[key] = "md"
            required = set(schema.get("required", [])) if isinstance(schema, dict) else set()
            if not required.issubset(arguments):
                raise AppError("HULY_SCHEMA_UNSUPPORTED", "Huly issue creation unavailable", "The MCP create_issue tool requires fields this integration does not support.", 502)
            result = await session.call_tool("create_issue", arguments=arguments)
    except AppError:
        raise
    except Exception as error:
        raise AppError("HULY_UNAVAILABLE", "Huly is unavailable", "Could not create the Huly issue. Check the MCP connection, project permissions, and Huly token.", 502) from error
    data = _decode_result(result)
    entries = _items(data)
    item = entries[0] if entries else (data if isinstance(data, dict) else {})
    identifier = str(item.get("identifier") or item.get("key") or item.get("number") or item.get("id") or item.get("_id") or "").strip()
    url = str(item.get("url") or item.get("issueUrl") or item.get("issue_url") or "").strip()
    if not identifier:
        text = str(data.get("text") or "") if isinstance(data, dict) else ""
        # MCP implementations may return the created issue as human-readable text.
        match = re.search(r"\b([A-Z][A-Z0-9_]{1,30}-\d+)\b", text)
        identifier = match.group(1) if match else ""
        url_match = re.search(r"https?://[^\s)\]>]+", text)
        url = url or (url_match.group(0).rstrip(".,") if url_match else "")
    if not identifier:
        raise AppError(
            "HULY_RESPONSE_UNVERIFIED",
            "Huly did not return an issue identifier",
            "The MCP call returned without an issue key, so Agent Designer cannot confirm creation. Check the target Huly project before retrying to avoid creating a duplicate.",
            502,
        )
    uploaded: list[dict[str, str]] = []
    failed: list[dict[str, str]] = []
    if attachments:
        try:
            async with _session(settings, url=settings.huly_attachment_mcp_url) as session:
                tools = await session.list_tools()
                tool = next((item for item in tools.tools if item.name == "add_issue_attachment"), None)
                if tool is None:
                    raise AppError("HULY_ATTACHMENT_UNAVAILABLE", "Huly attachments unavailable", "The configured Huly attachment service does not expose add_issue_attachment.", 502)
                for attachment in attachments:
                    arguments = {
                        "project": project,
                        "identifier": identifier,
                        "filename": attachment["filename"],
                        "contentType": attachment["content_type"],
                        "filePath": attachment["file_path"],
                        "description": attachment.get("description", ""),
                    }
                    try:
                        result = await session.call_tool("add_issue_attachment", arguments=arguments)
                        _decode_result(result)
                        uploaded.append({"filename": attachment["filename"], "status": "uploaded"})
                    except Exception as error:
                        detail = error.detail if isinstance(error, AppError) else "Huly could not attach this file."
                        failed.append({"filename": attachment["filename"], "error": str(detail)[:240]})
        except Exception as error:
            detail = error.detail if isinstance(error, AppError) else "Could not connect to Huly's attachment service."
            failed.extend({"filename": item["filename"], "error": str(detail)[:240]} for item in attachments if item["filename"] not in {x["filename"] for x in uploaded})
    return {"identifier": identifier, "url": url, "attachments": uploaded, "attachment_errors": failed}
