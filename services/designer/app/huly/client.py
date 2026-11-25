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


async def list_members(settings: Any) -> list[dict[str, str]]:
    """Return assignable workspace members using the pinned MCP tool."""
    try:
        async with _session(settings) as session:
            result = await session.call_tool(
                "list_members", arguments={"workspace": settings.huly_workspace}
            )
    except AppError:
        raise
    except Exception as error:
        raise AppError("HULY_UNAVAILABLE", "Huly is unavailable", "Could not load Huly workspace members.", 502) from error
    members: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in _items(_decode_result(result)):
        name = str(item.get("name") or item.get("displayName") or item.get("fullName") or item.get("username") or "").strip()
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        members.append({"name": name, "email": str(item.get("email") or "").strip()})
    return members


def _issue_type(item: dict[str, Any]) -> str:
    value = item.get("type") or item.get("taskType") or item.get("task_type") or item.get("kind") or ""
    if isinstance(value, dict):
        value = value.get("name") or value.get("title") or value.get("identifier") or ""
    return str(value).strip().casefold()


def _issue_identifier(item: dict[str, Any]) -> str:
    return str(item.get("identifier") or item.get("key") or item.get("number") or item.get("id") or item.get("_id") or "").strip()


async def list_epics(settings: Any, *, project: str) -> list[dict[str, str]]:
    """List Epic issues in a Huly project for parent selection."""
    try:
        async with _session(settings) as session:
            tools = await session.list_tools()
            tool = next((item for item in tools.tools if item.name == "list_issues"), None)
            if tool is None:
                raise AppError("HULY_TOOL_UNAVAILABLE", "Huly issue listing unavailable", "The configured MCP server does not expose list_issues.", 502)
            schema = getattr(tool, "input_schema", getattr(tool, "inputSchema", {})) or {}
            properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
            names = {key.casefold().replace("_", ""): key for key in properties}
            project_key = next((names[key] for key in ("project", "projectid", "projectidentifier", "projectkey") if key in names), "project")
            details_key = names.get("includeDetails".casefold(), "include_details")
            cursor_key = next((names[key] for key in ("cursor", "nextcursor", "pagetoken") if key in names), "")
            limit_key = next((names[key] for key in ("limit", "pagesize", "pageSize".casefold()) if key in names), "")
            issues: list[dict[str, Any]] = []
            cursor = ""
            for _ in range(20):
                arguments: dict[str, Any] = {project_key: project, details_key: False}
                if limit_key:
                    arguments[limit_key] = 100
                if cursor and cursor_key:
                    arguments[cursor_key] = cursor
                data = _decode_result(await session.call_tool("list_issues", arguments=arguments))
                issues.extend(_items(data))
                next_cursor = data.get("nextCursor") or data.get("next_cursor") if isinstance(data, dict) else ""
                if not next_cursor or not cursor_key:
                    break
                cursor = str(next_cursor)
    except AppError:
        raise
    except Exception as error:
        raise AppError("HULY_UNAVAILABLE", "Huly is unavailable", "Could not load Epics from the selected Huly project.", 502) from error
    epics = []
    for item in issues:
        if _issue_type(item) != "epic":
            continue
        identifier = _issue_identifier(item)
        if identifier:
            epics.append({"identifier": identifier, "title": str(item.get("title") or item.get("name") or identifier)})
    return epics


async def list_project_metadata(settings: Any, *, project: str) -> dict[str, Any]:
    """Read the selectable issue metadata from Huly's project/workspace config."""
    try:
        async with _session(settings) as session:
            results = {}
            for name, arguments in (
                ("list_statuses", {"project": project}),
                ("list_task_types", {"project": project}),
                ("list_components", {"project": project}),
                ("list_milestones", {"project": project}),
                ("list_labels", {"workspace": settings.huly_workspace}),
                ("list_projects", {"workspace": settings.huly_workspace, "include_details": True}),
            ):
                results[name] = _decode_result(await session.call_tool(name, arguments=arguments))
    except AppError:
        raise
    except Exception as error:
        raise AppError("HULY_UNAVAILABLE", "Huly metadata unavailable", "Could not load issue fields from the selected Huly project.", 502) from error

    def options(name: str) -> list[dict[str, Any]]:
        return _items(results.get(name))

    project_data = next(
        (item for item in _items(results.get("list_projects")) if str(item.get("identifier") or "") == project),
        {},
    )
    default_status_id = str((project_data.get("extra") or {}).get("defaultIssueStatus") or "")
    status_options = options("list_statuses")
    default_status = next(
        (str(item.get("name")) for item in status_options if item.get("id") == default_status_id),
        str(status_options[0].get("name")) if status_options else "",
    )
    return {
        "statuses": status_options,
        "task_types": options("list_task_types"),
        "components": options("list_components"),
        "milestones": options("list_milestones"),
        "labels": options("list_labels"),
        "default_status": default_status,
    }


async def search_issues(settings: Any, *, project: str, query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Search existing Huly issues for the Related field; this does not mutate them."""
    try:
        async with _session(settings) as session:
            data = _decode_result(await session.call_tool(
                "search_issues", arguments={"query": query, "project": project, "limit": min(max(limit, 1), 50)}
            ))
    except AppError:
        raise
    except Exception as error:
        raise AppError("HULY_UNAVAILABLE", "Huly issue search unavailable", "Could not search issues in the selected Huly project.", 502) from error
    issues = []
    for item in _items(data):
        identifier = _issue_identifier(item)
        title = str(item.get("title") or item.get("name") or "").strip()
        if identifier and title:
            issues.append({"identifier": identifier, "title": title, "url": str(item.get("url") or item.get("issueUrl") or "")})
    return issues


async def create_issue(
    settings: Any,
    *,
    project: str,
    title: str,
    description: str,
    priority: str = "none",
    status: str = "Todo",
    assignee: str = "",
    due_date: str = "",
    epic_id: str = "",
    task_type: str = "",
    labels: list[str] | None = None,
    component: str = "",
    milestone: str = "",
    estimation_hours: float | None = None,
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
                    arguments[key] = "markdown"
                elif normalized == "priority" and priority:
                    arguments[key] = priority
                elif normalized == "status" and status:
                    arguments[key] = status
                elif normalized == "assignee" and assignee:
                    arguments[key] = assignee
                elif normalized == "duedate" and due_date:
                    arguments[key] = due_date
                elif normalized == "type" and task_type:
                    arguments[key] = task_type
                elif normalized == "labels" and labels:
                    arguments[key] = labels
                elif normalized == "component" and component:
                    arguments[key] = component
                elif normalized == "milestone" and milestone:
                    arguments[key] = milestone
                elif normalized == "estimation" and estimation_hours is not None:
                    arguments[key] = estimation_hours
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
    parent_error = ""
    if epic_id:
        try:
            async with _session(settings) as session:
                result = await session.call_tool("set_parent", arguments={"issueId": identifier, "parentId": epic_id})
                _decode_result(result)
        except Exception as error:
            parent_error = error.detail if isinstance(error, AppError) else "Task was created, but Huly could not link it to the selected Epic."
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
    return {"identifier": identifier, "url": url, "parent_epic": epic_id, "parent_error": parent_error, "attachments": uploaded, "attachment_errors": failed}


async def add_dependency(settings: Any, *, issue_id: str, blocker_issue_id: str) -> None:
    """Record that issue_id depends on blocker_issue_id in Huly."""
    try:
        async with _session(settings) as session:
            result = await session.call_tool(
                "add_blocked_by",
                arguments={"issueId": issue_id, "blockerIssueId": blocker_issue_id},
            )
            _decode_result(result)
    except AppError:
        raise
    except Exception as error:
        raise AppError("HULY_RELATION_FAILED", "Could not link task dependency", "Huly could not save this dependency.", 502) from error


async def add_related_issue(settings: Any, *, issue_id: str, related_issue_id: str) -> None:
    """Create Huly's symmetric related-to relation between two issues."""
    try:
        async with _session(settings) as session:
            result = await session.call_tool(
                "add_relation",
                arguments={"issueId": issue_id, "relatedIssueId": related_issue_id},
            )
            _decode_result(result)
    except AppError:
        raise
    except Exception as error:
        raise AppError("HULY_RELATION_FAILED", "Could not link related issue", "Huly could not save this related-issue link.", 502) from error
